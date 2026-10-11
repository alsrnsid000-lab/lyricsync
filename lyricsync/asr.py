"""ASR 백엔드: 보컬 트랙에서 단어 단위 타임스탬프를 뽑는다.

백엔드 셋을 지원하고, 설치된 것 중 가장 좋은 걸 자동으로 고른다.

  whisperx        정확도 최상. wav2vec2로 강제 정렬까지 하므로 단어 경계가 날카롭다.
  faster-whisper  속도 최상. CPU에서도 쓸만하다. 단어 타임스탬프 지원.
  whisper         공식 구현. 느리지만 어디서나 돌아간다.

어느 것도 없으면 설치 안내를 띄우고 종료한다. import는 전부 지연 로딩이라,
whisper 없이도 이 모듈을 불러 테스트할 수 있다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .align import Word
from .normalize import normalize_token

BACKENDS = ("whisperx", "faster-whisper", "whisper")

INSTALL_HINT = """\
ASR 백엔드가 없습니다. 아래 중 하나를 설치하세요.

  # 정확도 우선 (권장)
  pip install whisperx

  # 속도 우선
  pip install faster-whisper

  # 공식 구현
  pip install openai-whisper

보컬 분리까지 쓰려면 (강력 권장):
  pip install demucs
"""


class ASRError(RuntimeError):
    """ASR 실패."""


@dataclass
class ASRResult:
    words: list[Word]
    language: str
    backend: str


def detect_backend() -> str | None:
    """설치된 백엔드 중 우선순위가 가장 높은 것의 이름."""
    from importlib.util import find_spec

    for name, module in (
        ("whisperx", "whisperx"),
        ("faster-whisper", "faster_whisper"),
        ("whisper", "whisper"),
    ):
        try:
            if find_spec(module) is not None:
                return name
        except (ImportError, ValueError):
            continue
    return None


def _clean_words(raw: list[tuple[str, float, float, float]]) -> list[Word]:
    """정규화하고, 시간이 없거나 비교 불가한 토큰을 버린다.

    원문 표기는 raw에 남긴다. 가사 없이 출력할 때 이 표기를 쓴다.
    """
    words: list[Word] = []
    for text, start, end, score in raw:
        display = (text or "").strip()
        norm = normalize_token(display)
        if not norm:
            continue
        if start is None or end is None:
            continue
        start = float(start)
        end = max(float(end), start)
        words.append(
            Word(text=norm, start=start, end=end, score=float(score), raw=display)
        )
    words.sort(key=lambda w: (w.start, w.end))
    return words


def _run_whisperx(
    audio: Path, model_size: str, language: str, device: str, compute_type: str
) -> ASRResult:
    import whisperx

    model = whisperx.load_model(model_size, device, compute_type=compute_type)
    audio_data = whisperx.load_audio(str(audio))
    result = model.transcribe(audio_data, language=language or None)
    detected = result.get("language", language or "ko")

    # 강제 정렬: 단어 경계를 오디오에 맞춰 날카롭게 만든다.
    try:
        align_model, metadata = whisperx.load_align_model(
            language_code=detected, device=device
        )
        result = whisperx.align(
            result["segments"], align_model, metadata, audio_data, device,
            return_char_alignments=False,
        )
    except Exception as exc:  # 해당 언어 정렬 모델이 없을 수 있다
        print(f"  [주의] 강제 정렬 생략 — {exc}", file=sys.stderr)

    raw: list[tuple[str, float, float, float]] = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []) or []:
            raw.append((
                w.get("word", ""),
                w.get("start"),
                w.get("end"),
                w.get("score", 1.0) or 1.0,
            ))
    return ASRResult(_clean_words(raw), detected, "whisperx")


def _run_faster_whisper(
    audio: Path, model_size: str, language: str, device: str, compute_type: str
) -> ASRResult:
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        str(audio),
        language=language or None,
        word_timestamps=True,
        vad_filter=True,
        # 가사는 말보다 반복이 많다. 압축비 임계값을 풀어야 후렴에서
        # "반복이니 환각"으로 오판해 구간을 버리는 일이 줄어든다.
        compression_ratio_threshold=2.8,
    )
    raw: list[tuple[str, float, float, float]] = []
    for seg in segments:
        for w in seg.words or []:
            prob = getattr(w, "probability", 1.0)
            raw.append((w.word, w.start, w.end, prob if prob is not None else 1.0))
    return ASRResult(_clean_words(raw), info.language or language or "ko", "faster-whisper")


def _run_whisper(
    audio: Path, model_size: str, language: str, device: str, compute_type: str
) -> ASRResult:
    import whisper

    model = whisper.load_model(model_size, device=None if device == "auto" else device)
    result = model.transcribe(
        str(audio),
        language=language or None,
        word_timestamps=True,
        condition_on_previous_text=False,  # 후렴 반복에서 폭주하는 것 방지
        verbose=False,
    )
    raw: list[tuple[str, float, float, float]] = []
    for seg in result.get("segments", []):
        for w in seg.get("words", []) or []:
            raw.append((
                w.get("word", ""),
                w.get("start"),
                w.get("end"),
                w.get("probability", 1.0) or 1.0,
            ))
    return ASRResult(_clean_words(raw), result.get("language", language or "ko"), "whisper")


_RUNNERS = {
    "whisperx": _run_whisperx,
    "faster-whisper": _run_faster_whisper,
    "whisper": _run_whisper,
}


def transcribe(
    audio: Path,
    model_size: str = "large-v3",
    language: str = "ko",
    backend: str | None = None,
    device: str = "auto",
    compute_type: str | None = None,
) -> ASRResult:
    """음원에서 단어 단위 타임스탬프를 뽑는다."""
    chosen = backend or detect_backend()
    if chosen is None:
        raise ASRError(INSTALL_HINT)
    if chosen not in _RUNNERS:
        raise ASRError(f"모르는 백엔드: {chosen} (가능: {', '.join(BACKENDS)})")

    if device == "auto":
        device = _best_device()
    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    runner = _RUNNERS[chosen]
    try:
        result = runner(audio, model_size, language, device, compute_type)
    except ImportError as exc:
        raise ASRError(f"{chosen} 로딩 실패: {exc}\n\n{INSTALL_HINT}") from exc

    if not result.words:
        raise ASRError(
            "단어를 하나도 인식하지 못했습니다.\n"
            "  - 보컬 분리(--separate)를 켜 보세요\n"
            "  - --language 를 음원 언어에 맞게 지정해 보세요\n"
            "  - 연주곡이라면 가사를 뽑을 수 없습니다"
        )
    return result


def _best_device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"
