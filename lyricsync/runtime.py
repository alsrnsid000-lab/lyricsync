"""실행 환경 판별: 소스로 돌 때와 exe로 묶였을 때의 차이를 여기서 흡수한다.

PyInstaller로 묶으면 두 가지가 달라진다.

  1. ffmpeg를 시스템에서 찾을 수 없다. 받는 사람 PC에 ffmpeg가 깔려 있을
     거라고 가정할 수 없으므로 exe에 같이 넣고 그쪽을 먼저 본다.
  2. 작업 폴더가 임시 디렉터리다. 모델 가중치를 거기 받으면 매번 다시
     받게 되므로 사용자 AppData 아래 고정 위치를 쓴다.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "lyricsync"


def is_frozen() -> bool:
    """PyInstaller 번들로 실행 중인지."""
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def bundle_dir() -> Path | None:
    """번들에 포함된 리소스가 풀려 있는 디렉터리. 소스 실행이면 None."""
    if is_frozen():
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return None


def app_data_dir() -> Path:
    """설정과 모델 캐시를 둘 곳.

    윈도우는 %LOCALAPPDATA%\\lyricsync, 그 외는 ~/.cache/lyricsync.
    번들 안에 쓰면 재실행 때 날아가므로 반드시 바깥이어야 한다.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = Path(base) / APP_NAME
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def model_cache_dir() -> Path:
    """Whisper/Demucs 가중치를 받아둘 곳."""
    path = app_data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_binary_with_origin(name: str) -> tuple[str | None, str]:
    """실행 파일을 찾고 어디서 왔는지까지 돌려준다.

    반환: (경로 또는 None, "bundle" | "system" | "none")

    출처를 경로에서 역추적하면 틀린다. 시스템 ffmpeg가 sys.executable과
    같은 폴더(/usr/bin 등)에 있으면 번들로 오판하기 때문에, 찾은 시점에
    기록해서 넘긴다.

    번들 레이아웃: <bundle>/ffmpeg/ffmpeg.exe (스펙에서 그렇게 넣는다)
    """
    exe = f"{name}.exe" if sys.platform == "win32" else name

    if is_frozen():
        # 스펙은 항상 ffmpeg/ 하위에 넣는다. 실행 파일 바로 옆까지 뒤지면
        # 시스템 경로를 번들로 오판할 수 있어서 이 위치만 본다.
        for root in (bundle_dir(), Path(sys.executable).parent):
            if not root:
                continue
            candidate = root / "ffmpeg" / exe
            if candidate.is_file():
                return str(candidate), "bundle"

    # 소스 실행이거나 번들에 없으면 시스템 PATH
    found = shutil.which(name)
    return (found, "system") if found else (None, "none")


def find_binary(name: str) -> str | None:
    """실행 파일 경로. 번들에 들어온 것을 시스템보다 먼저 본다."""
    return find_binary_with_origin(name)[0]


def prepare_model_env() -> None:
    """모델 다운로드 위치를 AppData로 고정한다.

    번들로 돌 때 기본 캐시는 임시 폴더가 될 수 있어서, 매 실행마다 수 GB를
    다시 받게 된다. 사용자가 이미 지정한 값이 있으면 건드리지 않는다.

      HF_HOME    faster-whisper / whisperx 가 모델을 받는 곳
      TORCH_HOME Demucs가 torch.hub로 가중치를 받는 곳
    """
    cache = model_cache_dir()
    for var in ("HF_HOME", "TORCH_HOME"):
        os.environ.setdefault(var, str(cache))


def ffmpeg_status() -> tuple[bool, str]:
    """ffmpeg 사용 가능 여부와 설명 문구. GUI에서 띄워주기 위한 것."""
    path, origin = find_binary_with_origin("ffmpeg")
    if not path:
        return False, "ffmpeg 없음 — 음원을 읽을 수 없습니다"
    return True, f"ffmpeg OK ({'번들 포함' if origin == 'bundle' else path})"
