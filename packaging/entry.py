"""exe 진입점. 창 모드(console=False)에서 생기는 함정들을 먼저 막는다.

세 가지가 문제가 된다.

1. stdout/stderr가 None이다. 라이브러리 내부의 print 한 줄이
   AttributeError로 앱을 죽인다 (tqdm, demucs가 실제로 그렇게 한다).
2. multiprocessing이 자기 자신을 다시 실행한다. freeze_support()를 부르지
   않으면 창이 무한히 복제된다.
3. 예외가 아무 데도 안 보인다. 로그 파일로 떨어뜨려야 원인을 알 수 있다.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


class _Sink:
    """stdout/stderr가 None일 때 꽂아둘 대역. 조용히 받아서 버린다."""

    encoding = "utf-8"

    def write(self, text):  # noqa: D102
        return len(text) if text else 0

    def flush(self): pass
    def isatty(self): return False
    def fileno(self): raise OSError("stub stream")
    def writelines(self, lines): pass
    def close(self): pass


def _guard_streams() -> None:
    if sys.stdout is None:
        sys.stdout = _Sink()
    if sys.stderr is None:
        sys.stderr = _Sink()
    if sys.stdin is None:
        sys.stdin = _Sink()


def _log_path() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = Path(base) / "lyricsync"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "error.log"


def _report_crash(exc: BaseException) -> None:
    """마지막 방어선. 로그로 남기고 가능하면 창으로 알린다."""
    import traceback

    text = "".join(traceback.format_exception(exc))
    try:
        path = _log_path()
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n" + "=" * 60 + "\n")
            fh.write(text)
        where = str(path)
    except OSError:
        where = "(로그를 쓸 수 없었습니다)"

    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showerror(
            "lyricsync 오류",
            f"{type(exc).__name__}: {exc}\n\n자세한 내용:\n{where}",
        )
        root.destroy()
    except Exception:
        pass


def _tame_openmp() -> None:
    """OpenMP 런타임이 두 벌일 때 죽지 않게 한다.

    번들에 libiomp5md.dll이 ctranslate2와 torch 양쪽에 들어간다. 둘 다
    적재되면 Intel OpenMP가 중복을 감지하고 프로세스를 중단시킨다.
    한쪽을 빼면 그쪽 라이브러리가 DLL을 못 찾으므로, 중복을 허용하는
    공식 환경변수를 쓴다.
    """
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    # 스레드 폭주 방지. 번들에서 BLAS가 코어 수만큼 스레드를 띄우면
    # 초기화 단계에서 교착이 생기는 사례가 있다.
    os.environ.setdefault("OMP_NUM_THREADS", "4")


def _boot_numpy() -> None:
    """numpy를 가장 먼저, 한 번만 적재하고 그 과정을 기록한다.

    "CPU dispatcher tracer already initlized"는 numpy의 C 확장이 한
    프로세스에서 두 번 초기화될 때 나온다. 우리 코드가 첫 import를 하는데도
    그 오류가 났다면, 그 전에 누군가 이미 적재했다는 뜻이다. 그게 누구인지
    알아야 고칠 수 있어서, 여기서 전후 상태를 남긴다.
    """
    import traceback as _tb

    watched = (
        "numpy", "numpy._core", "numpy._core._multiarray_umath",
        "numpy.core", "torch", "ctranslate2", "pkg_resources",
    )
    info = {"already_loaded": [m for m in watched if m in sys.modules]}
    try:
        import numpy

        info["numpy_version"] = getattr(numpy, "__version__", "?")
        info["numpy_file"] = getattr(numpy, "__file__", "?")
    except BaseException as exc:
        info["numpy_error"] = f"{type(exc).__name__}: {exc}"
        info["numpy_traceback"] = _tb.format_exc()
    sys._lyricsync_boot = info  # type: ignore[attr-defined]


def main() -> int:
    # 반드시 다른 무엇보다 먼저. 아래 import들이 print를 할 수 있다.
    multiprocessing.freeze_support()
    _guard_streams()
    _tame_openmp()
    _boot_numpy()

    try:
        from lyricsync.cli import main as cli_main

        return cli_main()
    except SystemExit as exc:
        return int(exc.code or 0)
    except BaseException as exc:  # noqa: BLE001 — 창 모드에선 여기가 마지막
        _report_crash(exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
