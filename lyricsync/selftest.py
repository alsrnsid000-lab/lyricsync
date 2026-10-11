"""번들 자가진단.

exe가 제대로 묶였는지 확인한다. PyInstaller는 "빌드 성공"이라고 해놓고
실행하면 임포트에서 죽는 번들을 만들 수 있다 — 정적 분석이 놓친 모듈은
실행해 봐야만 드러난다. 그래서 CI가 빌드 직후 이걸 실제로 돌린다.

  lyricsync.exe --selftest report.txt

결과를 파일로 쓰는 이유: 창 모드(console=False)로 묶이면 stdout이 없어서
화면으로는 아무것도 못 돌려준다.
"""

from __future__ import annotations

import importlib
import sys
import traceback
from pathlib import Path

# (모듈 이름, 설명, 없으면 치명적인가)
CHECKS: list[tuple[str, str, bool]] = [
    ("torch", "PyTorch — Demucs가 쓴다", True),
    ("demucs", "보컬 분리", True),
    ("demucs.separate", "보컬 분리 진입점", True),
    ("faster_whisper", "음성 인식", True),
    ("ctranslate2", "faster-whisper 백엔드", True),
    ("tkinter", "창 UI", True),
    ("pkg_resources", "일부 라이브러리가 런타임에 찾는다", False),
    ("torchaudio", "오디오 입출력", False),
    ("soundfile", "오디오 입출력", False),
    ("onnxruntime", "음성 구간 검출(VAD)", False),
]


def _probe(name: str) -> tuple[bool, str]:
    """모듈을 실제로 임포트해 본다. 존재 확인만으로는 부족하다."""
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    version = getattr(module, "__version__", "")
    return True, str(version) if version else "ok"


def run(out_path: str | None = None) -> int:
    """진단을 돌리고 보고서를 쓴다. 치명적 항목이 빠졌으면 1."""
    from . import __version__
    from .runtime import ffmpeg_status, is_frozen, model_cache_dir

    lines: list[str] = []
    fatal: list[str] = []
    warn: list[str] = []

    lines.append(f"lyricsync {__version__} 자가진단")
    lines.append(f"번들 실행: {'예' if is_frozen() else '아니오 (소스)'}")
    lines.append(f"파이썬: {sys.version.split()[0]}")
    lines.append(f"모델 캐시: {model_cache_dir()}")
    lines.append("")

    ok_ffmpeg, msg = ffmpeg_status()
    lines.append(f"[{'OK ' if ok_ffmpeg else 'FAIL'}] ffmpeg — {msg}")
    if not ok_ffmpeg:
        fatal.append("ffmpeg")

    for name, desc, required in CHECKS:
        ok, detail = _probe(name)
        if ok:
            tag = "OK "
        elif required:
            tag = "FAIL"
            fatal.append(name)
        else:
            tag = "WARN"
            warn.append(name)
        lines.append(f"[{tag}] {name} — {desc} :: {detail}")

    lines.append("")
    if fatal:
        lines.append(f"치명적 누락 {len(fatal)}건: {', '.join(fatal)}")
        lines.append("번들에 빠진 모듈이 있습니다. 스펙의 hiddenimports를 확인하세요.")
    else:
        lines.append("필수 항목 전부 정상.")
    if warn:
        lines.append(f"선택 항목 누락 {len(warn)}건: {', '.join(warn)} (동작은 합니다)")

    report = "\n".join(lines) + "\n"

    if out_path:
        try:
            path = Path(out_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report, encoding="utf-8")
        except OSError as exc:
            # 파일을 못 써도 진단 결과 자체는 종료 코드로 전달된다
            try:
                sys.stderr.write(f"보고서 저장 실패: {exc}\n")
            except Exception:
                pass

    try:
        sys.stdout.write(report)
        sys.stdout.flush()
    except Exception:
        pass  # 창 모드에서는 stdout이 없을 수 있다

    return 1 if fatal else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out = args[0] if args else None
    try:
        return run(out)
    except Exception:
        try:
            sys.stderr.write(traceback.format_exc())
        except Exception:
            pass
        return 2


if __name__ == "__main__":
    sys.exit(main())
