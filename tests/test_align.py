"""정렬 코어 검증.

ASR이 틀리는 상황(오인식/누락/삽입)을 합성 데이터로 재현해서,
가사 줄에 붙는 시각이 의도대로 나오는지 본다.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness
from lyricsync.align import Word, align_lyrics, parse_lyrics, segment_words, shift
from lyricsync.normalize import decompose_hangul, tokenize


def make_words(spec):
    """[(텍스트, 시작, 끝), ...] -> [Word]"""
    return [Word(text=t, start=s, end=e) for t, s, e in spec]


def test_hangul_decompose():
    assert decompose_hangul("강") == "ㄱㅏㅇ"
    assert decompose_hangul("가") == "ㄱㅏ"
    assert decompose_hangul("a강") == "aㄱㅏㅇ"
    print("  자모 분해 OK")


def test_tokenize():
    assert tokenize("사랑해, 너를!") == ["사랑해", "너를"]
    assert tokenize("Oh yeah (x3)") == ["oh", "yeah"]
    assert tokenize("   ") == []
    print("  토큰화 OK")


def test_perfect_match():
    """ASR이 완벽할 때 — 줄 경계가 정확히 맞아야 한다."""
    lyrics = parse_lyrics("첫번째 줄이야\n두번째 줄이야")
    words = make_words([
        ("첫번째", 1.0, 1.5), ("줄이야", 1.5, 2.0),
        ("두번째", 3.0, 3.5), ("줄이야", 3.5, 4.0),
    ])
    out = align_lyrics(lyrics, words, audio_duration=10.0)
    assert len(out) == 2
    assert abs(out[0].start - 1.0) < 1e-6, out[0].start
    assert abs(out[1].start - 3.0) < 1e-6, out[1].start
    assert out[0].confidence == 1.0
    assert not out[0].interpolated
    print(f"  완전일치 OK — L1 {out[0].start:.2f}s, L2 {out[1].start:.2f}s")


def test_misrecognition():
    """받침/표기가 틀려도 같은 줄로 붙어야 한다."""
    lyrics = parse_lyrics("있잖아 그대여\n보고 싶어")
    words = make_words([
        ("이짜나", 1.0, 1.6), ("그대여", 1.6, 2.2),
        ("보고", 4.0, 4.4), ("시퍼", 4.4, 5.0),
    ])
    out = align_lyrics(lyrics, words, audio_duration=10.0)
    assert abs(out[0].start - 1.0) < 1e-6, out[0].start
    assert abs(out[1].start - 4.0) < 1e-6, out[1].start
    print(f"  오인식 흡수 OK — conf {out[0].confidence:.2f} / {out[1].confidence:.2f}")


def test_dropped_line_is_interpolated():
    """ASR이 한 줄을 통째로 놓쳤을 때 앞뒤 사이로 보간되어야 한다."""
    lyrics = parse_lyrics("가나다 라마바\n완전히 다른 내용\n사아자 차카타")
    words = make_words([
        ("가나다", 1.0, 1.5), ("라마바", 1.5, 2.0),
        # 가운데 줄 없음
        ("사아자", 8.0, 8.5), ("차카타", 8.5, 9.0),
    ])
    out = align_lyrics(lyrics, words, audio_duration=12.0)
    assert out[1].interpolated, "놓친 줄은 interpolated 표시되어야 함"
    assert out[1].confidence == 0.0
    assert out[0].end <= out[1].start <= out[2].start, (
        out[0].end, out[1].start, out[2].start
    )
    print(f"  누락 보간 OK — 보간된 줄 {out[1].start:.2f}s~{out[1].end:.2f}s")


def test_extra_asr_noise():
    """ASR이 없는 말을 지어내도(애드립/반주 잡음) 정렬이 안 깨져야 한다."""
    lyrics = parse_lyrics("하나 둘 셋\n넷 다섯 여섯")
    words = make_words([
        ("음", 0.5, 0.7), ("어", 0.7, 0.9),
        ("하나", 1.0, 1.3), ("둘", 1.3, 1.6), ("셋", 1.6, 1.9),
        ("예", 2.0, 2.2),
        ("넷", 3.0, 3.3), ("다섯", 3.3, 3.7), ("여섯", 3.7, 4.1),
    ])
    out = align_lyrics(lyrics, words, audio_duration=8.0)
    assert abs(out[0].start - 1.0) < 1e-6, out[0].start
    assert abs(out[1].start - 3.0) < 1e-6, out[1].start
    print("  잡음 무시 OK")


def test_monotonic_and_no_overlap():
    """어떤 경우에도 시간은 뒤로 가지 않고 줄끼리 겹치지 않는다."""
    lyrics = parse_lyrics("\n".join(f"줄 번호 {i}" for i in range(12)))
    words = make_words([("줄", 2.0, 2.2), ("번호", 2.2, 2.5), ("3", 2.5, 2.7)])
    out = align_lyrics(lyrics, words, audio_duration=30.0)
    for a, b in zip(out, out[1:]):
        assert a.start <= b.start, (a.start, b.start)
        assert a.end <= b.start + 1e-9, (a.end, b.start)
        assert a.end >= a.start
    assert out[-1].end <= 30.0 + 1e-9
    print("  단조성/비중첩 OK")


def test_section_headers_dropped():
    lyrics = parse_lyrics("[Verse 1]\n첫 줄\n\n(후렴)\n둘째 줄\n")
    assert [ln.text for ln in lyrics] == ["첫 줄", "둘째 줄"], [l.text for l in lyrics]
    print("  섹션 표기 제거 OK")


def test_segment_without_lyrics():
    """가사 없이 ASR만으로 줄을 끊는 경우."""
    words = make_words([
        ("오늘도", 1.0, 1.4), ("하루가", 1.4, 1.9), ("간다", 1.9, 2.3),
        # 1.5초 쉼 -> 줄바꿈
        ("내일은", 3.8, 4.2), ("다를까", 4.2, 4.8),
    ])
    out = segment_words(words, max_gap=0.9)
    assert len(out) == 2, [l.text for l in out]
    assert out[0].text == "오늘도 하루가 간다"
    assert abs(out[1].start - 3.8) < 1e-6
    print(f"  무가사 분할 OK — {len(out)}줄")


def test_shift():
    lyrics = parse_lyrics("한 줄")
    out = align_lyrics(lyrics, make_words([("한", 5.0, 5.2), ("줄", 5.2, 5.5)]), 10.0)
    shift(out, -0.3)
    assert abs(out[0].start - 4.7) < 1e-6, out[0].start
    shift(out, -99.0)  # 0 아래로 안 내려가야 함
    assert out[0].start == 0.0
    print("  오프셋 보정 OK")


def test_empty_inputs():
    assert align_lyrics([], make_words([("a", 0, 1)])) == []
    out = align_lyrics(parse_lyrics("가\n나\n다"), [], audio_duration=9.0)
    assert len(out) == 3 and all(l.interpolated for l in out)
    assert abs(out[1].start - 3.0) < 1e-6
    print("  빈 입력 처리 OK")


if __name__ == "__main__":
    _harness.main(globals(), "정렬 코어 테스트")
