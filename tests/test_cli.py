"""CLI 통합 검증.

실제 Whisper는 여기서 돌릴 수 없으므로 ASR만 가짜로 바꿔 끼우고,
음원 입력 → 오디오 전처리 → 정렬 → 파일 출력까지 전 구간 배선을 확인한다.
ffmpeg는 진짜로 쓴다(합성 톤을 만들어 넣는다).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lyricsync import asr, cli, pipeline
from lyricsync.align import Word

LYRICS = """[Verse 1]
어두운 방 안에
혼자 앉아 있었어

창밖은 아직 밤이고
너는 오지 않았어
"""

# ASR이 뱉었다고 가정할 단어들. 일부러 틀리게 섞었다.
FAKE_WORDS = [
    ("어두운", 2.0, 2.5), ("방", 2.5, 2.8), ("아네", 2.8, 3.2),     # 안에 -> 아네
    ("혼자", 4.0, 4.4), ("안자", 4.4, 4.8), ("이써써", 4.8, 5.5),   # 앉아 있었어
    # '창밖은 아직 밤이고' 줄은 통째로 누락 — 보간돼야 한다
    ("너는", 12.0, 12.4), ("오지", 12.4, 12.8), ("안았어", 12.8, 13.5),
]


def make_audio(path: Path, seconds: float = 20.0) -> Path:
    """ffmpeg로 무음 wav 생성. 전처리 경로를 진짜로 통과시키기 위한 것."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
            "-t", str(seconds), str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def install_fake_asr(monkey_words=None):
    """asr.transcribe를 가짜로 교체하고, 원본을 돌려준다."""
    original = pipeline.transcribe
    words = monkey_words if monkey_words is not None else FAKE_WORDS

    def fake(audio, **kwargs):
        return asr.ASRResult(
            words=[
                Word(text=t, start=s, end=e, score=0.9, raw=t)
                for t, s, e in words
            ],
            language="ko",
            backend="fake",
        )

    pipeline.transcribe = fake
    return original


def test_end_to_end_with_lyrics():
    orig = install_fake_asr()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            audio = make_audio(tmp / "song.wav")
            (tmp / "lyrics.txt").write_text(LYRICS, encoding="utf-8")

            code = cli.main([
                str(audio),
                "--lyrics", str(tmp / "lyrics.txt"),
                "--no-separate",
                "-f", "lrc", "-f", "srt", "-f", "json",
                "-q",
            ])
            assert code == 0, f"exit {code}"

            lrc = (tmp / "song.lrc").read_text(encoding="utf-8")
            srt = (tmp / "song.srt").read_text(encoding="utf-8")
            data = json.loads((tmp / "song.json").read_text(encoding="utf-8"))

            # 섹션 표기는 빠지고 가사 4줄만 남는다
            assert len(data["lines"]) == 4, [l["text"] for l in data["lines"]]
            assert "[Verse 1]" not in lrc

            # 글자는 가사 파일 그대로 — ASR 오인식이 새어나오지 않아야 한다
            assert "혼자 앉아 있었어" in lrc
            assert "안자" not in lrc and "이써써" not in lrc

            # 첫 줄 시각은 ASR이 준 2.0초
            assert lrc.count("[00:02.00]어두운 방 안에") == 1, lrc

            # 누락된 3번째 줄은 보간 표시
            third = data["lines"][2]
            assert third["text"] == "창밖은 아직 밤이고"
            assert third["interpolated"] is True
            assert third["confidence"] == 0.0

            # 시간은 단조 증가하고 음원 길이를 넘지 않는다
            prev = -1.0
            for ln in data["lines"]:
                assert ln["start"] >= prev, (ln["start"], prev)
                prev = ln["start"]
                assert ln["end"] <= 20.01, ln["end"]

            assert srt.startswith("1\n00:00:02,000 --> ")
            print(f"  가사 모드 종단 OK — 4줄, 보간 1줄")
    finally:
        pipeline.transcribe = orig


def test_end_to_end_without_lyrics():
    orig = install_fake_asr()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            audio = make_audio(tmp / "track.wav")
            code = cli.main([str(audio), "--no-separate", "-f", "lrc", "-q"])
            assert code == 0, f"exit {code}"

            lrc = (tmp / "track.lrc").read_text(encoding="utf-8")
            # 받아쓴 글자가 그대로 나온다 (오인식 포함 — 초벌용)
            assert "어두운" in lrc
            # 1.5초 쉼 기준으로 줄이 갈라져야 한다
            stamps = [l for l in lrc.splitlines() if l.startswith("[0")]
            assert len(stamps) >= 3, stamps
            print(f"  무가사 모드 종단 OK — {len(stamps)}개 타임스탬프")
    finally:
        pipeline.transcribe = orig


def test_offset_applied():
    orig = install_fake_asr()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            audio = make_audio(tmp / "s.wav")
            (tmp / "l.txt").write_text("어두운 방 안에\n", encoding="utf-8")
            code = cli.main([
                str(audio), "--lyrics", str(tmp / "l.txt"),
                "--no-separate", "--offset", "-0.5", "-q",
                "-o", str(tmp / "out.lrc"),
            ])
            assert code == 0
            lrc = (tmp / "out.lrc").read_text(encoding="utf-8")
            assert "[00:01.50]어두운 방 안에" in lrc, lrc  # 2.0 - 0.5
            print("  오프셋 적용 OK")
    finally:
        pipeline.transcribe = orig


def test_single_output_path_respected():
    orig = install_fake_asr()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            audio = make_audio(tmp / "s.wav")
            target = tmp / "nested" / "custom.srt"
            code = cli.main([
                str(audio), "--no-separate", "-f", "srt",
                "-o", str(target), "-q",
            ])
            assert code == 0
            assert target.exists(), "중간 디렉터리까지 만들어야 한다"
            print("  출력 경로 지정 OK")
    finally:
        pipeline.transcribe = orig


def test_missing_files_exit_cleanly():
    code = cli.main(["/존재하지/않는/음원.mp3", "-q"])
    assert code == 2, code
    with tempfile.TemporaryDirectory() as tmp:
        audio = make_audio(Path(tmp) / "s.wav")
        code = cli.main([str(audio), "--lyrics", "/없는/가사.txt", "-q"])
        assert code == 2, code
    print("  누락 입력 종료코드 OK")


def test_empty_lyrics_file_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        audio = make_audio(tmp / "s.wav")
        (tmp / "empty.txt").write_text("[Chorus]\n\n   \n", encoding="utf-8")
        code = cli.main([str(audio), "--lyrics", str(tmp / "empty.txt"), "-q"])
        assert code == 2, code
    print("  빈 가사 파일 거부 OK")


def test_asr_failure_reports_cleanly():
    """ASR이 아무것도 못 건지면 설치 안내가 아니라 원인을 말해야 한다."""
    orig = install_fake_asr(monkey_words=[])
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            audio = make_audio(tmp / "s.wav")
            code = cli.main([str(audio), "--no-separate", "-q"])
            # 단어 0개 -> 출력할 줄 없음 -> 1
            assert code == 1, code
        print("  인식 실패 처리 OK")
    finally:
        pipeline.transcribe = orig


def test_help_runs():
    try:
        cli.main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    print("  --help OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"CLI 통합 테스트 {len(tests)}건\n")
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
