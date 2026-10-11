"""명령줄 인터페이스.

  # 가사 있음 — 글자는 그대로, 시간만 입힌다 (최종본용)
  lyricsync song.mp3 --lyrics lyrics.txt -o song.lrc

  # 가사 없음 — 받아쓰기까지 (초벌용)
  lyricsync song.mp3 -o song.lrc

  # 여러 포맷 한번에
  lyricsync song.mp3 --lyrics lyrics.txt -f lrc -f srt -f json

  # 창 띄우기 (윈도우용 GUI)
  lyricsync --gui
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .asr import ASRError, BACKENDS
from .audio import AudioError
from .formats import WRITERS
from .pipeline import Cancelled, SyncOptions, quality_report, run_sync


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="lyricsync",
        description="음원에서 가사 타임스탬프(LRC/SRT)를 자동으로 뽑습니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "audio", type=Path, nargs="?",
        help="음원 파일 (mp3, m4a, flac, wav ...)",
    )
    p.add_argument(
        "--gui", action="store_true",
        help="창을 띄웁니다. 인자 없이 실행해도 같습니다.",
    )
    p.add_argument(
        "-l", "--lyrics", type=Path,
        help="가사 텍스트 파일. 주면 글자는 이 파일 그대로 쓰고 시간만 입힙니다. "
             "안 주면 받아쓰기까지 합니다.",
    )
    p.add_argument(
        "-o", "--output", type=Path,
        help="출력 경로. 생략하면 음원과 같은 이름으로 씁니다.",
    )
    p.add_argument(
        "-f", "--format", dest="formats", action="append",
        choices=sorted(WRITERS), metavar="FMT",
        help=f"출력 포맷, 여러 번 지정 가능 ({', '.join(sorted(WRITERS))}). 기본: lrc",
    )
    p.add_argument(
        "--word-level", action="store_true",
        help="LRC에 단어별 타임스탬프까지 넣습니다 (가라오케식 하이라이트용).",
    )

    g = p.add_argument_group("인식 설정")
    g.add_argument(
        "--model", default="medium",
        help="Whisper 모델 크기. tiny/base/small/medium/large-v3. 기본: medium",
    )
    g.add_argument(
        "--language", default="ko",
        help="가사 언어 코드. 'auto'면 자동 감지. 기본: ko",
    )
    g.add_argument(
        "--backend", choices=BACKENDS,
        help="ASR 백엔드를 직접 고릅니다. 기본은 설치된 것 중 최선.",
    )
    g.add_argument(
        "--device", default="auto", choices=("auto", "cpu", "cuda"),
        help="연산 장치. 기본: auto",
    )
    g.add_argument(
        "--no-separate", dest="separate", action="store_false",
        help="보컬 분리(Demucs)를 건너뜁니다. 빠르지만 인식률이 떨어집니다.",
    )

    g2 = p.add_argument_group("후처리")
    g2.add_argument(
        "--offset", type=float, default=0.0, metavar="SEC",
        help="전체 타임스탬프를 이만큼 밉니다. 음수면 당깁니다. 예: --offset -0.3",
    )
    g2.add_argument(
        "--max-chars", type=int, default=38,
        help="가사 없이 돌릴 때 한 줄 최대 글자수. 기본: 38",
    )
    g2.add_argument(
        "--keep-temp", action="store_true",
        help="중간 파일(분리된 보컬 등)을 지우지 않습니다.",
    )
    g2.add_argument("-q", "--quiet", action="store_true", help="진행 로그를 숨깁니다.")
    g2.add_argument("--version", action="version", version=f"lyricsync {__version__}")
    return p


def resolve_outputs(
    audio: Path, output: Path | None, formats: list[str]
) -> dict[str, Path]:
    """포맷별 출력 경로를 정한다."""
    if output and len(formats) == 1:
        return {formats[0]: output}
    stem_dir = output.parent if output else audio.parent
    stem = output.stem if output else audio.stem
    return {fmt: stem_dir / f"{stem}.{fmt}" for fmt in formats}


def run(args: argparse.Namespace) -> int:
    quiet = args.quiet
    formats = list(dict.fromkeys(args.formats or ["lrc"]))

    if not args.audio.exists():
        print(f"오류: 음원 파일이 없습니다 — {args.audio}", file=sys.stderr)
        return 2
    if args.lyrics and not args.lyrics.exists():
        print(f"오류: 가사 파일이 없습니다 — {args.lyrics}", file=sys.stderr)
        return 2

    def log(msg: str) -> None:
        if not quiet:
            print(f"  {msg}", file=sys.stderr)

    opts = SyncOptions(
        audio=args.audio,
        outputs=resolve_outputs(args.audio, args.output, formats),
        lyrics=args.lyrics,
        model=args.model,
        language=args.language,
        backend=args.backend,
        device=args.device,
        separate=args.separate,
        offset=args.offset,
        word_level=args.word_level,
        max_chars=args.max_chars,
    )

    try:
        result = run_sync(opts, on_log=log, keep_temp=args.keep_temp)
    except (ValueError, AudioError, ASRError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1 if not isinstance(exc, ValueError) else 2
    except Cancelled:
        return 130

    for path in result.written:
        print(path, file=sys.stdout)

    if not quiet:
        print("", file=sys.stderr)
        for line in quality_report(result):
            print(f"  {line}" if line else "", file=sys.stderr)

    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)

    # 인자 없이 실행하거나 --gui면 창을 띄운다.
    # exe를 더블클릭하면 인자가 없으므로 이 경로로 들어온다.
    if args.gui or args.audio is None:
        try:
            from .gui import launch
        except ImportError as exc:
            print(
                f"오류: GUI를 띄울 수 없습니다 ({exc}).\n"
                "tkinter가 없는 환경입니다. 음원 경로를 인자로 주고 쓰세요:\n"
                "  lyricsync song.mp3 --lyrics lyrics.txt",
                file=sys.stderr,
            )
            return 2
        return launch(args)

    try:
        return run(args)
    except KeyboardInterrupt:
        print("\n중단됨.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
