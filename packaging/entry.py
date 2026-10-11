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


def main() -> int:
    # 반드시 다른 무엇보다 먼저. 아래 import들이 print를 할 수 있다.
    multiprocessing.freeze_support()
    _guard_streams()

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
