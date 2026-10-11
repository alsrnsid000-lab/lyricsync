"""CLI와 GUI가 공유하는 작업 본체.

로그를 콜백으로 빼서 CLI는 stderr로, GUI는 창에 흘린다. 단계 사이마다
취소 요청을 확인한다(모델 추론 도중에는 끊을 수 없다 — 아래 주석 참고).
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .align import TimedLine, align_lyrics, parse_lyrics, segment_words, shift
from .asr import ASRError, transcribe
from .audio import AudioError, prepare
from .formats import render
from .runtime import prepare_model_env

# 이 값보다 신뢰도가 낮은 줄은 "손봐야 할 곳"으로 분류한다.
LOW_CONFIDENCE = 0.5

STAGES = ("준비", "보컬 분리", "음성 인식", "정렬", "저장")


class Cancelled(RuntimeError):
    """사용자가 작업을 취소했다."""


@dataclass
class SyncOptions:
    audio: Path
    outputs: dict[str, Path]
    lyrics: Path | None = None
    model: str = "medium"
    language: str = "ko"
    backend: str | None = None
    device: str = "auto"
    separate: bool = True
    offset: float = 0.0
    word_level: bool = False
    max_chars: int = 38


@dataclass
class SyncResult:
    lines: list[TimedLine]
    written: list[Path]
    backend: str
    language: str
    duration: float
    separated: bool
    had_lyrics: bool
    word_count: int

    @property
    def weak(self) -> list[TimedLine]:
        return [ln for ln in self.lines if ln.confidence < LOW_CONFIDENCE]

    @property
    def interpolated(self) -> list[TimedLine]:
        return [ln for ln in self.lines if ln.interpolated]

    @property
    def weak_ratio(self) -> float:
        if not self.lines:
            return 0.0
        return len(self.weak) / len(self.lines)


@dataclass
class Progress:
    """진행 상황 보고용. GUI가 단계 표시에 쓴다."""

    on_log: object = None
    on_stage: object = None
    cancel: threading.Event | None = None
    _stage: int = field(default=0, init=False)

    def log(self, msg: str) -> None:
        if self.on_log:
            self.on_log(msg)  # type: ignore[operator]

    def stage(self, name: str) -> None:
        self._stage += 1
        if self.on_stage:
            self.on_stage(name, self._stage, len(STAGES))  # type: ignore[operator]

    def check(self) -> None:
        """취소 요청이 있으면 여기서 끊는다.

        Demucs와 Whisper 추론 자체는 중간에 끊을 수 없어서, 단계가 끝난
        직후에만 반응한다. GUI는 "취소 중…"을 띄워 이 지연을 설명한다.
        """
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled("사용자가 취소했습니다.")


def load_lyrics(path: Path):
    """가사 파일을 읽어 줄 목록으로. 인코딩은 UTF-8 → CP949 순으로 시도한다."""
    raw = None
    for encoding in ("utf-8", "utf-8-sig", "cp949"):
        try:
            raw = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    if raw is None:
        raw = path.read_text(encoding="utf-8", errors="replace")

    lines = parse_lyrics(raw)
    if not lines:
        raise ValueError(
            f"가사 파일에서 읽을 줄이 없습니다 — {path.name}\n"
            "빈 파일이거나 섹션 표기만 들어 있는지 확인하세요."
        )
    return lines


def run_sync(
    opts: SyncOptions,
    on_log=None,
    on_stage=None,
    cancel: threading.Event | None = None,
    keep_temp: bool = False,
) -> SyncResult:
    """음원 → 타임스탬프 파일. 실패는 예외로 올린다."""
    p = Progress(on_log=on_log, on_stage=on_stage, cancel=cancel)
    prepare_model_env()

    # --- 가사 읽기 -------------------------------------------------------
    p.stage("준비")
    lyric_lines = []
    if opts.lyrics:
        lyric_lines = load_lyrics(opts.lyrics)
        p.log(f"가사 {len(lyric_lines)}줄 읽음 — 글자는 이 파일 그대로 씁니다.")
    else:
        p.log("가사 파일 없음 — 받아쓰기까지 진행합니다 (글자 오류 가능).")
    p.check()

    tmpdir = Path(tempfile.mkdtemp(prefix="lyricsync-"))
    try:
        # --- 오디오 준비 -------------------------------------------------
        p.stage("보컬 분리")
        if opts.separate:
            p.log("보컬 분리 중… 음원 길이에 따라 수 분 걸립니다.")
        else:
            p.log("보컬 분리 생략 (--no-separate)")
        wav, duration, separated = prepare(
            opts.audio, tmpdir, separate=opts.separate, on_log=p.log
        )
        p.log(f"음원 길이 {duration:.1f}초 · 보컬 분리 {'완료' if separated else '안 함'}")
        p.check()

        # --- 음성 인식 ---------------------------------------------------
        p.stage("음성 인식")
        p.log(f"음성 인식 중… (모델 {opts.model}) 처음 쓰는 모델이면 먼저 내려받습니다.")
        result = transcribe(
            wav,
            model_size=opts.model,
            language="" if opts.language in ("auto", "") else opts.language,
            backend=opts.backend,
            device=opts.device,
        )
        p.log(
            f"단어 {len(result.words)}개 인식 "
            f"(백엔드 {result.backend} · 언어 {result.language})"
        )
        p.check()

        # --- 정렬 --------------------------------------------------------
        p.stage("정렬")
        if lyric_lines:
            p.log("가사와 정렬 중…")
            lines = align_lyrics(lyric_lines, result.words, audio_duration=duration)
        else:
            lines = segment_words(result.words, max_chars=opts.max_chars)
        if not lines:
            raise ASRError("출력할 줄이 없습니다. 연주곡이거나 보컬을 못 찾았습니다.")

        if opts.offset:
            shift(lines, opts.offset)
            p.log(f"오프셋 {opts.offset:+.2f}초 적용")
        p.check()

        # --- 저장 --------------------------------------------------------
        p.stage("저장")
        meta = {"title": opts.audio.stem, "by": f"lyricsync {__version__}"}
        written: list[Path] = []
        for fmt, path in opts.outputs.items():
            text = render(
                fmt, lines, meta=meta, word_level=opts.word_level, offset_ms=0
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            written.append(path)
            p.log(f"저장: {path.name}")

        return SyncResult(
            lines=lines,
            written=written,
            backend=result.backend,
            language=result.language,
            duration=duration,
            separated=separated,
            had_lyrics=bool(lyric_lines),
            word_count=len(result.words),
        )

    finally:
        if keep_temp:
            p.log(f"중간 파일: {tmpdir}")
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)


def quality_report(result: SyncResult, limit: int = 12) -> list[str]:
    """손봐야 할 구간을 사람이 읽을 줄 목록으로. CLI와 GUI가 같이 쓴다."""
    out: list[str] = []
    if not result.lines:
        return ["출력된 줄이 없습니다."]

    out.append(f"전체 {len(result.lines)}줄")
    weak = result.weak
    if not weak:
        out.append("전부 신뢰도 양호 — 그대로 써도 됩니다.")
        return out

    pct = 100.0 * result.weak_ratio
    out.append(f"확인 권장 {len(weak)}줄 ({pct:.0f}%)")
    if result.interpolated:
        out.append(
            f"이 중 {len(result.interpolated)}줄은 음원에서 못 찾아 "
            "앞뒤 사이로 추정했습니다."
        )
    out.append("")
    for ln in weak[:limit]:
        mark = "추정" if ln.interpolated else "낮음"
        preview = ln.text if len(ln.text) <= 30 else ln.text[:29] + "…"
        out.append(f"  [{mark}] {ln.start:7.2f}s  conf {ln.confidence:.2f}  {preview}")
    if len(weak) > limit:
        out.append(f"  … 외 {len(weak) - limit}줄")

    out.append("")
    if not result.had_lyrics:
        out.append("팁: 정식 가사를 넣고 다시 돌리면 글자 오류가 사라집니다.")
    elif pct > 40:
        out.append(
            "팁: 절반 가까이 낮습니다. 가사가 이 음원 버전과 맞는지"
            " (라이브/리믹스 아닌지) 확인해 보세요."
        )
    elif not result.separated:
        out.append("팁: 보컬 분리를 켜면 인식률이 올라갑니다.")
    return out
