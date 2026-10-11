"""GUI 로직 검증 (디스플레이 없이).

tkinter를 가짜로 끼워서 창 조립과 입력 검증, 워커 스레드 ↔ 메인 스레드
메시지 전달을 실제로 실행한다.

외관은 여기서 검증되지 않는다 — 윈도우에서 직접 띄워 봐야 한다.
"""

import subprocess
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import stub_tk

MB, FD = stub_tk.install()  # lyricsync.gui 임포트 전에 심어야 한다

from lyricsync import asr, gui, pipeline
from lyricsync.align import Word

WORDS = [
    ("어두운", 2.0, 2.5), ("방", 2.5, 2.8), ("아네", 2.8, 3.2),
    ("혼자", 4.0, 4.4), ("안자", 4.4, 4.8), ("이써써", 4.8, 5.5),
    ("너는", 12.0, 12.4), ("오지", 12.4, 12.8), ("안았어", 12.8, 13.5),
]
LYRICS = "어두운 방 안에\n혼자 앉아 있었어\n창밖은 아직 밤이고\n너는 오지 않았어\n"


def fake_asr(audio, **kwargs):
    return asr.ASRResult(
        [Word(text=t, start=s, end=e, score=0.9, raw=t) for t, s, e in WORDS],
        "ko", "fake",
    )


def make_audio(path: Path, seconds: float = 20.0) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=stereo", "-t", str(seconds), str(path)],
        check=True, capture_output=True,
    )
    return path


def fresh_app():
    """환경 점검을 통과한 상태의 App을 만든다."""
    MB.reset()
    stub_tk._Widget.created.clear()
    # 환경 점검이 실패하면 실행 버튼이 잠겨서 이후 검증을 못 한다
    orig_ffmpeg, orig_backend = gui.ffmpeg_status, gui.detect_backend
    gui.ffmpeg_status = lambda: (True, "ffmpeg OK (stub)")
    gui.detect_backend = lambda: "fake"
    try:
        root = stub_tk.Tk()
        app = gui.App(root)
    finally:
        gui.ffmpeg_status, gui.detect_backend = orig_ffmpeg, orig_backend
    return app


def test_window_builds():
    """_build()가 예외 없이 전체 위젯 트리를 만든다."""
    app = fresh_app()
    made = stub_tk._Widget.created
    assert made.count("LabelFrame") == 4, made.count("LabelFrame")  # 입력/포맷/설정/로그
    assert made.count("Checkbutton") == 7, made.count("Checkbutton")  # 포맷5+단어별+분리
    assert made.count("Combobox") == 2, made.count("Combobox")        # 모델/언어
    assert "Progressbar" in made and "Text" in made
    assert app.run_btn.cget("state") != "disabled"
    print(f"  창 조립 OK — 위젯 {len(made)}개")


def test_default_format_is_lrc():
    app = fresh_app()
    on = [f for f, v in app.fmt_vars.items() if v.get()]
    assert on == ["lrc"], on
    print("  기본 포맷 OK — lrc")


def test_environment_check_disables_run():
    """ffmpeg나 백엔드가 없으면 실행 버튼이 잠겨야 한다."""
    orig_ffmpeg, orig_backend = gui.ffmpeg_status, gui.detect_backend
    gui.ffmpeg_status = lambda: (False, "ffmpeg 없음")
    gui.detect_backend = lambda: None
    try:
        app = gui.App(stub_tk.Tk())
        assert app.run_btn.cget("state") == "disabled"
        assert "해결" in app.status_var.get()
    finally:
        gui.ffmpeg_status, gui.detect_backend = orig_ffmpeg, orig_backend
    print("  환경 점검 차단 OK")


def test_collect_rejects_empty_audio():
    app = fresh_app()
    assert app._collect() is None
    assert MB.kinds() == ["warning"], MB.calls
    print("  빈 음원 거부 OK")


def test_collect_rejects_missing_file():
    app = fresh_app()
    app.audio_var.set("/없는/경로/song.mp3")
    assert app._collect() is None
    assert MB.last[0] == "error", MB.calls
    print("  없는 파일 거부 OK")


def test_collect_rejects_no_format():
    with tempfile.TemporaryDirectory() as tmp:
        audio = make_audio(Path(tmp) / "s.wav")
        app = fresh_app()
        app.audio_var.set(str(audio))
        for v in app.fmt_vars.values():
            v.set(False)
        assert app._collect() is None
        assert MB.last[0] == "warning", MB.calls
    print("  포맷 미선택 거부 OK")


def test_collect_rejects_bad_offset():
    with tempfile.TemporaryDirectory() as tmp:
        audio = make_audio(Path(tmp) / "s.wav")
        app = fresh_app()
        app.audio_var.set(str(audio))
        app.offset_var.set("빠르게")
        assert app._collect() is None
        assert MB.last[0] == "error" and "숫자" in MB.last[2], MB.calls
    print("  잘못된 보정값 거부 OK")


def test_collect_strips_quotes():
    """윈도우에서 '경로 복사'하면 따옴표가 붙는다. 그대로 쓰면 파일을 못 찾는다."""
    with tempfile.TemporaryDirectory() as tmp:
        audio = make_audio(Path(tmp) / "s.wav")
        app = fresh_app()
        app.audio_var.set(f'"{audio}"')
        opts = app._collect()
        assert opts is not None, MB.calls
        assert opts.audio == audio
    print("  따옴표 제거 OK")


def test_collect_builds_options():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio = make_audio(tmp / "song.wav")
        (tmp / "lyrics.txt").write_text(LYRICS, encoding="utf-8")
        app = fresh_app()
        app.audio_var.set(str(audio))
        app.lyrics_var.set(str(tmp / "lyrics.txt"))
        app.fmt_vars["srt"].set(True)
        app.model_var.set("small")
        app.lang_var.set("영어")
        app.offset_var.set("-0.4")
        app.word_var.set(True)

        opts = app._collect()
        assert opts is not None, MB.calls
        assert set(opts.outputs) == {"lrc", "srt"}, opts.outputs
        assert opts.outputs["lrc"] == tmp / "song.lrc"
        assert opts.model == "small"
        assert opts.language == "en", opts.language   # 표시 이름 -> 코드
        assert opts.offset == -0.4
        assert opts.word_level is True
        assert opts.separate is True
    print("  옵션 수집 OK")


def test_collect_asks_before_overwrite():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio = make_audio(tmp / "song.wav")
        (tmp / "song.lrc").write_text("기존 파일", encoding="utf-8")
        app = fresh_app()
        app.audio_var.set(str(audio))

        MB.yesno_answer = False
        assert app._collect() is None, "거절했으면 진행하지 않아야 한다"
        assert MB.last[0] == "askyesno"

        MB.reset()
        MB.yesno_answer = True
        assert app._collect() is not None
    print("  덮어쓰기 확인 OK")


def test_outdir_override():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio = make_audio(tmp / "song.wav")
        target = tmp / "out"
        app = fresh_app()
        app.audio_var.set(str(audio))
        app.outdir_var.set(str(target))
        opts = app._collect()
        assert opts.outputs["lrc"] == target / "song.lrc", opts.outputs
    print("  저장 폴더 지정 OK")


def test_auto_finds_lyrics_next_to_audio():
    """음원과 같은 이름의 .txt가 있으면 자동으로 집어준다."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio = make_audio(tmp / "song.wav")
        (tmp / "song.txt").write_text(LYRICS, encoding="utf-8")
        app = fresh_app()
        FD.next_open = str(audio)
        app._pick_audio()
        assert app.lyrics_var.get() == str(tmp / "song.txt"), app.lyrics_var.get()
    print("  가사 자동 탐색 OK")


def test_worker_reports_through_queue():
    """워커가 큐로 보고하고, 메인 스레드가 비우면서 화면을 갱신한다."""
    orig = pipeline.transcribe
    pipeline.transcribe = fake_asr
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            audio = make_audio(tmp / "song.wav")
            (tmp / "lyrics.txt").write_text(LYRICS, encoding="utf-8")

            app = fresh_app()
            app.audio_var.set(str(audio))
            app.lyrics_var.set(str(tmp / "lyrics.txt"))
            app.sep_var.set(False)
            opts = app._collect()
            assert opts is not None, MB.calls

            app._work(opts)  # 동기 실행 (스레드 없이)

            kinds = []
            while not app.queue.empty():
                kinds.append(app.queue.get_nowait()[0])
            assert "done" in kinds, kinds
            assert kinds.count("stage") == 5, kinds  # 5단계 전부 보고
            assert (tmp / "song.lrc").exists()

            text = (tmp / "song.lrc").read_text(encoding="utf-8")
            assert "혼자 앉아 있었어" in text   # 가사 글자 그대로
            assert "이써써" not in text          # ASR 오인식 안 새어나옴
    finally:
        pipeline.transcribe = orig
    print("  워커 보고 OK — 5단계 + 완료")


def test_worker_reports_error_not_crash():
    """작업이 터져도 창이 죽지 않고 오류를 보고해야 한다."""
    orig = pipeline.transcribe
    pipeline.transcribe = lambda *a, **k: (_ for _ in ()).throw(
        asr.ASRError("모델을 못 불러왔습니다")
    )
    try:
        with tempfile.TemporaryDirectory() as tmp:
            audio = make_audio(Path(tmp) / "s.wav")
            app = fresh_app()
            app.audio_var.set(str(audio))
            app.sep_var.set(False)
            opts = app._collect()
            app._work(opts)

            msgs = []
            while not app.queue.empty():
                msgs.append(app.queue.get_nowait())
            kinds = [m[0] for m in msgs]
            assert "error" in kinds, kinds
            err = next(m for m in msgs if m[0] == "error")
            assert "모델을 못 불러왔습니다" in str(err[1])
            assert err[2] and "Traceback" in err[2]  # 트레이스백도 같이 온다
    finally:
        pipeline.transcribe = orig
    print("  오류 보고 OK")


def test_cancel_stops_before_asr():
    """취소를 걸면 단계 경계에서 멈춘다."""
    orig = pipeline.transcribe
    called = []

    def should_not_run(*a, **k):
        called.append(1)
        return fake_asr(*a, **k)

    pipeline.transcribe = should_not_run
    try:
        with tempfile.TemporaryDirectory() as tmp:
            audio = make_audio(Path(tmp) / "s.wav")
            app = fresh_app()
            app.audio_var.set(str(audio))
            app.sep_var.set(False)
            opts = app._collect()
            app.cancel.set()           # 시작 전에 취소
            app._work(opts)

            kinds = []
            while not app.queue.empty():
                kinds.append(app.queue.get_nowait()[0])
            assert "cancelled" in kinds, kinds
            assert not called, "취소했는데 음성 인식이 돌았다"
    finally:
        pipeline.transcribe = orig
    print("  취소 처리 OK")


def test_drain_updates_widgets():
    """큐 비우기가 로그와 진행바를 실제로 갱신한다."""
    app = fresh_app()
    app.root.scheduled.clear()
    app._put(("log", "테스트 줄", None))
    app._put(("stage", "음성 인식", 3))
    app._drain()

    assert "테스트 줄" in "".join(app.log._kw.get("_text", []))
    assert app.progress.cget("value") == 3
    assert "음성 인식" in app.stage_var.get()
    assert app.root.scheduled, "_drain은 다음 호출을 다시 예약해야 한다"
    print("  화면 갱신 OK")


def test_close_while_running_asks():
    app = fresh_app()
    app.worker = threading.Thread(target=lambda: threading.Event().wait(0.2))
    app.worker.start()
    MB.yesno_answer = False
    app._on_close()
    assert not app.root.destroyed, "거절했으면 닫히지 않아야 한다"
    MB.yesno_answer = True
    app._on_close()
    assert app.root.destroyed and app.cancel.is_set()
    app.worker.join(timeout=1)
    print("  작업 중 종료 확인 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"GUI 로직 테스트 {len(tests)}건 (가짜 tkinter)\n")
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
