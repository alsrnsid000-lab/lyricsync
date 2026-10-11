"""번들 런타임 검증.

exe로 묶였을 때의 경로 해석을 흉내 내서 확인한다. 실제 PyInstaller 번들은
윈도우에서만 만들 수 있으므로, sys.frozen/_MEIPASS를 직접 세워 재현한다.
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    받는 사람 PC의 PATH에 깨진 ffmpeg가 있어도 번들 쪽이 쓰여야 한다.
    """
    system = runtime.find_binary("ffmpeg")
    assert system and "/usr" in system, f"테스트 전제: 시스템 ffmpeg 필요 ({system})"

    with tempfile.TemporaryDirectory() as tmp:
        bundled = Path(tmp) / "ffmpeg"
        bundled.mkdir()
        exe = bundled / ("ffmpeg.exe" if sys.platform == "win32" else "ffmpeg")
        exe.write_text("#!/bin/sh\n")
        with fake_bundle(Path(tmp)):
            found = runtime.find_binary("ffmpeg")
            assert found == str(exe), f"{found} != {exe}"
            assert runtime.find_binary_with_origin("ffmpeg")[1] == "bundle"
        # 번들을 나오면 다시 시스템 것
        assert runtime.find_binary("ffmpeg") == system
    print("  번들 ffmpeg 우선 OK")


def test_falls_back_to_system_when_not_bundled():
    """번들에 ffmpeg를 못 넣었어도 시스템에 있으면 동작해야 한다."""
    with tempfile.TemporaryDirectory() as tmp:
        with fake_bundle(Path(tmp)):
            found = runtime.find_binary("ffmpeg")
            assert found and runtime.find_binary_with_origin("ffmpeg")[1] == "system"
            ok, msg = runtime.ffmpeg_status()
            assert ok and "번들 포함" not in msg, msg
    print("  시스템 폴백 OK")


def test_missing_binary_reports_false():
    with tempfile.TemporaryDirectory() as tmp:
        with fake_bundle(Path(tmp)):
            assert runtime.find_binary("존재하지않는도구") is None
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
    print(f"  캐시 위치 OK — 번들 바깥, 쓰기 가능")


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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"번들 런타임 테스트 {len(tests)}건\n")
    failed = 0
    for fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed += 1
            print(f"  [FAIL] {fn.__name__}: {exc}")
    print()
    print("전부 통과" if not failed else f"{failed}건 실패")
    sys.exit(1 if failed else 0)
