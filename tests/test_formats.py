"""출력 포맷 검증.

타임코드 반올림/자리수, 60분 넘는 음원, 플레이어가 요구하는 구조를 본다.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lyricsync.align import TimedLine, Word
from lyricsync.formats import (
    lrc_time,
    render,
    srt_time,
    to_json,
    to_lrc,
    to_srt,
    to_tsv,
    to_vtt,
    vtt_time,
)


def sample_lines():
    return [
        TimedLine(
            index=0, text="첫번째 줄이야", start=12.34, end=15.0, confidence=1.0,
            words=[
                Word(text="첫번째", start=12.34, end=13.0, raw="첫번째"),
                Word(text="줄이야", start=13.0, end=15.0, raw="줄이야"),
            ],
        ),
        TimedLine(
            index=1, text="두번째 줄이야", start=15.5, end=18.0,
            confidence=0.3, interpolated=True,
        ),
    ]


def test_lrc_time():
    assert lrc_time(0) == "00:00.00"
    assert lrc_time(12.34) == "00:12.34"
    assert lrc_time(59.999) == "01:00.00", lrc_time(59.999)   # 반올림 자리올림
    assert lrc_time(61.5) == "01:01.50"
    assert lrc_time(3671.25) == "61:11.25", lrc_time(3671.25)  # 60분 초과
    assert lrc_time(-5) == "00:00.00"                          # 음수 방어
    print("  LRC 타임코드 OK")


def test_srt_vtt_time():
    assert srt_time(0) == "00:00:00,000"
    assert srt_time(12.345) == "00:00:12,345"
    assert srt_time(3671.25) == "01:01:11,250", srt_time(3671.25)
    assert srt_time(0.9999) == "00:00:01,000"
    assert vtt_time(12.345) == "00:00:12.345"
    print("  SRT/VTT 타임코드 OK")


def test_lrc_structure():
    out = to_lrc(sample_lines(), meta={"title": "테스트", "artist": "누군가"})
    assert "[ti:테스트]" in out
    assert "[ar:누군가]" in out
    assert "[00:12.34]첫번째 줄이야" in out, out
    assert "[00:15.50]두번째 줄이야" in out, out
    # 마지막 줄을 끝내주는 빈 태그가 있어야 가사가 끝까지 남지 않는다
    assert out.rstrip().endswith("[00:18.00]"), out.rstrip()[-40:]
    print("  LRC 구조 OK")


def test_lrc_word_level():
    out = to_lrc(sample_lines(), word_level=True)
    assert "[00:12.34]<00:12.34>첫번째 <00:13.00>줄이야" in out, out
    # 단어 정보가 없는 줄은 통째로 떨어져야 한다
    assert "[00:15.50]두번째 줄이야" in out, out
    print("  확장 LRC OK")


def test_lrc_offset_sign():
    """LRC의 offset 태그는 부호가 거꾸로다. 그대로 넣으면 싱크가 반대로 틀어진다."""
    out = to_lrc(sample_lines(), offset_ms=250)
    assert "[offset:-250]" in out, out
    assert "[offset:" not in to_lrc(sample_lines(), offset_ms=0)
    print("  LRC offset 부호 OK")


def test_srt_structure():
    out = to_srt(sample_lines())
    blocks = out.strip().split("\n\n")
    assert len(blocks) == 2, blocks
    first = blocks[0].splitlines()
    assert first[0] == "1"                                   # 1부터 시작
    assert first[1] == "00:00:12,340 --> 00:00:15,000", first[1]
    assert first[2] == "첫번째 줄이야"
    print("  SRT 구조 OK")


def test_vtt_structure():
    out = to_vtt(sample_lines())
    assert out.startswith("WEBVTT\n"), out[:20]
    assert "00:00:12.340 --> 00:00:15.000" in out
    print("  VTT 구조 OK")


def test_json_roundtrip():
    data = json.loads(to_json(sample_lines(), meta={"title": "T"}))
    assert data["meta"]["title"] == "T"
    assert len(data["lines"]) == 2
    assert data["lines"][0]["start"] == 12.34
    assert data["lines"][0]["words"][0]["text"] == "첫번째"
    assert data["lines"][1]["interpolated"] is True
    assert data["lines"][0]["duration"] == 2.66
    print("  JSON OK")


def test_tsv():
    out = to_tsv(sample_lines())
    rows = out.strip().splitlines()
    assert rows[0].startswith("start\tend\t")
    assert rows[2].split("\t")[3] == "Y", rows[2]  # 보간 표시
    assert rows[1].split("\t")[3] == ""
    print("  TSV OK")


def test_render_dispatch():
    lines = sample_lines()
    for fmt in ("lrc", "srt", "vtt", "json", "tsv"):
        assert render(fmt, lines, meta={"title": "x"}).strip(), fmt
    try:
        render("mp3", lines)
    except ValueError as exc:
        assert "모르는 포맷" in str(exc)
    else:
        raise AssertionError("알 수 없는 포맷인데 통과했음")
    print("  포맷 분기 OK")


def test_empty_lines():
    for fmt in ("lrc", "srt", "vtt", "json", "tsv"):
        render(fmt, [])  # 예외 없이 지나가야 한다
    print("  빈 입력 OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    print(f"출력 포맷 테스트 {len(tests)}건\n")
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
