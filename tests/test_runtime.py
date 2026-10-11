"""번들 런타임 검증.

exe로 묶였을 때의 경로 해석을 흉내 내서 확인한다. 실제 PyInstaller 번들은
윈도우에서만 만들 수 있으므로, sys.frozen/_MEIPASS를 직접 세워 재현한다.

시스템에 진짜 ffmpeg가 깔려 있는지에 기대지 않는다. 가짜 실행 파일을 만들어
PATH에 끼우므로 리눅스/윈도우/CI 어디서든 같은 결과가 나온다.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness
from lyricsync import runtime


class fake_bundle:
    """sys.frozen과 sys._MEIPASS를 세워 번들 실행을 흉내 낸다."""

    def __init__(self, path: Path):
        self.path = path

    def __enter__(self):
        sys.frozen = True
        sys._MEIPASS = str(self.path)
        return self.path

    def __exit__(self, *exc):
        for attr in ("frozen", "_MEIPASS"):
            if hasattr(sys, attr):
                delattr(sys, attr)


class fake_path:
    """PATH를 임시 폴더 하나로 갈아끼운다. 진짜 시스템 도구를 가린다."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.saved = None

    def __enter__(self):
        self.saved = os.environ.get("PATH", "")
        os.environ["PATH"] = str(self.folder)
        return self.folder

    def __exit__(self, *exc):
        os.environ["PATH"] = self.saved or ""


def make_tool(folder: Path, name: str) -> Path:
    """실행 가능한 가짜 도구를 만든다.

    윈도우의 shutil.which는 PATHEXT를 보므로 확장자가 .exe여야 하고,
    POSIX에서는 실행 권한이 있어야 한다.
    """
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / (f"{name}.exe" if sys.platform == "win32" else name)
    exe.write_text("", encoding="utf-8")
    if sys.platform != "win32":
        exe.chmod(0o755)
    return exe


def test_not_frozen_by_default():
    assert runtime.is_frozen() is False
    assert runtime.bundle_dir() is None
    print("  소스 실행 판별 OK")


def test_frozen_detection():
    with tempfile.TemporaryDirectory() as tmp:
        with fake_bundle(Path(tmp)) as bundle:
            assert runtime.is_frozen() is True
            assert runtime.bundle_dir() == bundle
    assert runtime.is_frozen() is False, "컨텍스트를 나오면 원복되어야 한다"
    print("  번들 실행 판별 OK")


def test_bundled_ffmpeg_wins_over_system():
    """번들에 ffmpeg가 있으면 시스템 것보다 먼저 써야 한다.

    받는 사람 PC의 PATH에 다른 ffmpeg가 있어도 번들 쪽이 쓰여야 한다.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        system_exe = make_tool(tmp / "system", "ffmpeg")
        bundle_exe = make_tool(tmp / "bundle" / "ffmpeg", "ffmpeg")

        with fake_path(tmp / "system"):
            # 번들 밖에서는 시스템 것이 보인다
            assert runtime.find_binary("ffmpeg") == str(system_exe)

            with fake_bundle(tmp / "bundle"):
                found, origin = runtime.find_binary_with_origin("ffmpeg")
                assert found == str(bundle_exe), f"{found} != {bundle_exe}"
                assert origin == "bundle", origin

            # 번들을 나오면 다시 시스템 것
            assert runtime.find_binary("ffmpeg") == str(system_exe)
    print("  번들 ffmpeg 우선 OK")


def test_falls_back_to_system_when_not_bundled():
    """번들에 ffmpeg를 못 넣었어도 시스템에 있으면 동작해야 한다."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        system_exe = make_tool(tmp / "system", "ffmpeg")
        empty_bundle = tmp / "bundle"
        empty_bundle.mkdir()

        with fake_path(tmp / "system"), fake_bundle(empty_bundle):
            found, origin = runtime.find_binary_with_origin("ffmpeg")
            assert found == str(system_exe), found
            assert origin == "system", origin

            ok, msg = runtime.ffmpeg_status()
            assert ok and "번들 포함" not in msg, msg
    print("  시스템 폴백 OK")


def test_missing_binary_reports_false():
    """어디에도 없으면 없다고 해야 한다. 거짓 양성이 더 나쁘다."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        empty = tmp / "empty"
        empty.mkdir()
        with fake_path(empty), fake_bundle(tmp):
            found, origin = runtime.find_binary_with_origin("ffmpeg")
            assert found is None, found
            assert origin == "none", origin
            ok, msg = runtime.ffmpeg_status()
            assert not ok and "없음" in msg, msg
    print("  없는 도구 보고 OK")


def test_app_data_dir_is_writable_and_outside_bundle():
    """모델 캐시는 번들 바깥이어야 한다. 안에 쓰면 재실행 때 날아간다."""
    with tempfile.TemporaryDirectory() as tmp:
        with fake_bundle(Path(tmp)) as bundle:
            cache = runtime.model_cache_dir()
            assert cache.is_dir()
            try:
                cache.relative_to(bundle)
                raise AssertionError(f"캐시가 번들 안에 있다: {cache}")
            except ValueError:
                pass  # 바깥 — 정상
            probe = cache / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            assert probe.read_text(encoding="utf-8") == "ok"
            probe.unlink()
    print("  캐시 위치 OK — 번들 바깥, 쓰기 가능")


def test_prepare_model_env_respects_user_setting():
    """사용자가 이미 지정한 캐시 위치는 덮어쓰지 않는다."""
    saved = {k: os.environ.get(k) for k in ("HF_HOME", "TORCH_HOME")}
    try:
        os.environ["HF_HOME"] = "/내가/정한/위치"
        os.environ.pop("TORCH_HOME", None)
        runtime.prepare_model_env()
        assert os.environ["HF_HOME"] == "/내가/정한/위치"
        assert os.environ["TORCH_HOME"] == str(runtime.model_cache_dir())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("  사용자 설정 보존 OK")


def test_windows_cache_path():
    """윈도우에서는 %LOCALAPPDATA% 아래로 가야 한다."""
    real_platform = sys.platform
    saved = os.environ.get("LOCALAPPDATA")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOCALAPPDATA"] = tmp
            sys.platform = "win32"
            path = runtime.app_data_dir()
            assert path == Path(tmp) / "lyricsync", path
            assert path.is_dir()
    finally:
        sys.platform = real_platform
        if saved is None:
            os.environ.pop("LOCALAPPDATA", None)
        else:
            os.environ["LOCALAPPDATA"] = saved
    print("  윈도우 캐시 경로 OK")


def test_posix_cache_path():
    """리눅스/맥에서는 캐시 디렉터리 아래로 가야 한다."""
    real_platform = sys.platform
    saved = os.environ.get("XDG_CACHE_HOME")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["XDG_CACHE_HOME"] = tmp
            sys.platform = "linux"
            assert runtime.app_data_dir() == Path(tmp) / "lyricsync"
    finally:
        sys.platform = real_platform
        if saved is None:
            os.environ.pop("XDG_CACHE_HOME", None)
        else:
            os.environ["XDG_CACHE_HOME"] = saved
    print("  POSIX 캐시 경로 OK")


if __name__ == "__main__":
    _harness.main(globals(), "번들 런타임 테스트")
