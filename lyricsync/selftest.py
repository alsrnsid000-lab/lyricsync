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
# numpy를 맨 앞에 둔다. 이게 깨지면 torch·faster_whisper·onnxruntime이
# 전부 연쇄로 실패하는데, 그러면 원인이 어디인지 헷갈린다.
CHECKS: list[tuple[str, str, bool]] = [
    # 필수 — 하나라도 없으면 프로그램이 제 일을 못 한다
    ("numpy", "수치 연산 — 아래 대부분이 이것에 의존한다", True),
    ("faster_whisper", "음성 인식", True),
    ("ctranslate2", "faster-whisper 백엔드", True),
    ("av", "음원 디코딩", True),
    ("tkinter", "창 UI", True),
    # 선택 — 없어도 동작한다
    ("onnxruntime", "음성 구간 검출(VAD)", False),
    ("soundfile", "오디오 입출력", False),
    # 보컬 분리용. 배포 exe에는 일부러 넣지 않는다 (torch가 numpy·OpenMP와
    # 충돌해 앱이 아예 뜨지 않았다). 소스로 돌릴 때만 쓴다.
    ("torch", "PyTorch — 보컬 분리에만 필요", False),
    ("demucs.separate", "보컬 분리", False),
]


def _probe(name: str) -> tuple[bool, str, str]:
    """모듈을 실제로 임포트해 본다. 존재 확인만으로는 부족하다.

    반환: (성공 여부, 한 줄 요약, 실패 시 트레이스백)

    트레이스백까지 돌려주는 이유: "cannot load module more than once"
    같은 오류는 메시지만으로는 어느 확장 모듈이 중복 적재됐는지 알 수 없다.
    한 번 추측으로 고치려다 사이클을 낭비했다.
    """
    try:
        module = importlib.import_module(name)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}", traceback.format_exc()
    version = getattr(module, "__version__", "")
    where = getattr(module, "__file__", "") or "(내장)"
    return True, f"{version or 'ok'}  @ {where}", ""


def _boot_info() -> list[str]:
    """시작 시점 기록을 읽는다 (rthook_numpy.py, entry.py 참고)."""
    out: list[str] = []

    # 런타임 훅이 다른 훅보다 먼저 돌았는지 — 이게 핵심 단서다
    hook = getattr(sys, "_lyricsync_rthook", None)
    if hook:
        pre = hook.get("before") or []
        if pre:
            out.append(f"  런타임 훅 시점에 이미 적재됨: {', '.join(pre)}")
            out.append("  → 우리 훅이 첫 번째가 아니었습니다.")
        else:
            out.append("  런타임 훅이 가장 먼저 돌았습니다 (선점 없음)")
        if "numpy" in hook:
            out.append(f"  훅에서 numpy {hook['numpy']} 적재 성공")
        if "error" in hook:
            out.append(f"  훅에서 numpy 적재 실패: {hook['error']}")
            tb = (hook.get("traceback") or "").rstrip().splitlines()
            out.extend("    " + ln for ln in tb[-8:])
    else:
        out.append("  (런타임 훅 기록 없음 — 번들이 아니거나 훅이 빠졌습니다)")

    info = getattr(sys, "_lyricsync_boot", None)
    if not info:
        out.append("  (진입점 기록 없음)")
        return out
    out.append("")
    pre = info.get("already_loaded") or []
    if pre:
        out.append(f"  우리 코드가 돌기 전에 이미 적재됨: {', '.join(pre)}")
        out.append("  → 이게 numpy 중복 초기화의 원인입니다.")
    else:
        out.append("  우리 코드가 첫 번째 적재자였습니다 (선점한 모듈 없음)")

    if "numpy_version" in info:
        out.append(f"  시작 시 numpy {info['numpy_version']} 적재 성공")
        out.append(f"    {info.get('numpy_file', '?')}")
    if "numpy_error" in info:
        out.append(f"  시작 시 numpy 적재 실패: {info['numpy_error']}")
        tb = (info.get("numpy_traceback") or "").rstrip().splitlines()
        out.extend("    " + ln for ln in tb[-10:])
    return out


def _numpy_layout() -> list[str]:
    """번들 안 numpy가 어떻게 놓였는지 본다.

    numpy 2.x는 구버전 호환용 numpy/core/ 와 실제 numpy/_core/ 를 둘 다
    갖고 있다. 양쪽에 같은 확장 모듈이 들어가면 두 번 적재된다.
    """
    from .runtime import bundle_dir

    root = bundle_dir()
    if root is None:
        return ["(번들 실행이 아니라 생략)"]

    out = []
    for sub in ("numpy", "numpy/core", "numpy/_core"):
        path = root / sub
        if not path.is_dir():
            out.append(f"  {sub}/ — 없음")
            continue
        try:
            exts = sorted(p.name for p in path.glob("*.pyd")) + \
                   sorted(p.name for p in path.glob("*.so"))
        except OSError:
            exts = []
        out.append(f"  {sub}/ — 확장 모듈 {len(exts)}개 {exts[:6]}")
    return out


def _duplicate_extensions(limit: int = 15) -> list[str]:
    """번들 안에 같은 이름의 확장 모듈이 두 벌 이상 있는지 찾는다.

    "cannot load module more than once per process"의 전형적 원인이
    같은 .pyd가 두 경로에 들어간 것이라, 그걸 직접 확인한다.
    """
    from .runtime import bundle_dir

    root = bundle_dir()
    if root is None:
        return ["(번들 실행이 아니라 생략)"]

    seen: dict[str, list[str]] = {}
    try:
        for path in root.rglob("*"):
            if path.suffix.lower() not in (".pyd", ".so", ".dll"):
                continue
            seen.setdefault(path.name.lower(), []).append(
                str(path.relative_to(root))
            )
    except OSError as exc:
        return [f"(검사 실패: {exc})"]

    dupes = {k: v for k, v in seen.items() if len(v) > 1}
    if not dupes:
        return [f"중복 없음 (확장 모듈 {len(seen)}개 확인)"]

    out = [f"중복 {len(dupes)}건 발견 (확장 모듈 {len(seen)}개 중):"]
    for name, paths in sorted(dupes.items())[:limit]:
        out.append(f"  {name}")
        for p in paths[:4]:
            out.append(f"    - {p}")
    if len(dupes) > limit:
        out.append(f"  … 외 {len(dupes) - limit}건")
    return out


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

    from .audio import separation_available

    if separation_available():
        lines.append("[OK ] 보컬 분리 — 사용 가능 (Demucs)")
    else:
        lines.append("[    ] 보컬 분리 — 없음 (이 빌드는 의도적으로 제외)")

    traces: list[tuple[str, str]] = []
    for name, desc, required in CHECKS:
        ok, detail, tb = _probe(name)
        if ok:
            tag = "OK "
        elif required:
            tag = "FAIL"
            fatal.append(name)
        else:
            tag = "WARN"
            warn.append(name)
        lines.append(f"[{tag}] {name} — {desc} :: {detail}")
        if tb:
            traces.append((name, tb))

    # 실패가 있으면 원인을 짚을 재료를 남긴다. 메시지만으로는 부족하다.
    if traces:
        lines.append("")
        lines.append("=" * 60)
        lines.append("진단 정보")
        lines.append("=" * 60)
        lines.append("")
        lines.append("[시작 시점 상태 — numpy를 누가 먼저 적재했나]")
        lines.extend(_boot_info())
        lines.append("")
        lines.append("[번들 내 numpy 배치]")
        lines.extend(_numpy_layout())
        lines.append("")
        lines.append("[번들 내 중복 확장 모듈]")
        lines.extend(_duplicate_extensions())
        lines.append("")
        lines.append("[sys.path]")
        for entry in sys.path[:12]:
            lines.append(f"  {entry}")
        lines.append("")
        for name, tb in traces[:4]:
            lines.append(f"[{name} 실패 트레이스백]")
            lines.extend("  " + ln for ln in tb.rstrip().splitlines()[-14:])
            lines.append("")

    lines.append("")
    if fatal:
        lines.append(f"치명적 누락 {len(fatal)}건: {', '.join(fatal)}")
        lines.append("번들에 빠진 모듈이 있습니다. 스펙의 hiddenimports를 확인하세요.")
    else:
        lines.append("필수 항목 전부 정상 — 이 번들은 쓸 수 있습니다.")
    if warn:
        lines.append(f"선택 항목 없음 {len(warn)}건: {', '.join(warn)} (동작에 지장 없음)")

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
