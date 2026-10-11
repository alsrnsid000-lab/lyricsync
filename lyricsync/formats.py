"""출력 포맷: LRC, 확장 LRC(단어 단위), SRT, VTT, JSON, TSV."""

from __future__ import annotations

import json
from datetime import datetime

from .align import TimedLine

# LRC는 1/100초까지만 표현한다. 그보다 정밀한 값은 어차피 버려진다.
LRC_PRECISION = 0.01


def _clamp(seconds: float) -> float:
    return max(0.0, float(seconds))


def lrc_time(seconds: float) -> str:
    """[mm:ss.xx] 안에 들어갈 시각. 60분 넘으면 분이 계속 커진다."""
    seconds = _clamp(seconds)
    total_cs = int(round(seconds * 100))
    minutes, rem_cs = divmod(total_cs, 6000)
    secs, cs = divmod(rem_cs, 100)
    return f"{minutes:02d}:{secs:02d}.{cs:02d}"


def srt_time(seconds: float) -> str:
    """HH:MM:SS,mmm"""
    seconds = _clamp(seconds)
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def vtt_time(seconds: float) -> str:
    """HH:MM:SS.mmm"""
    return srt_time(seconds).replace(",", ".")


def to_lrc(
    lines: list[TimedLine],
    meta: dict[str, str] | None = None,
    word_level: bool = False,
    offset_ms: int = 0,
) -> str:
    """LRC 생성.

    word_level=True면 확장 LRC로 각 단어 앞에 <mm:ss.xx>를 붙인다
    (가라오케식 하이라이트를 지원하는 플레이어용).
    """
    out: list[str] = []
    meta = meta or {}
    for tag, key in (
        ("ti", "title"), ("ar", "artist"), ("al", "album"), ("by", "by"),
    ):
        value = meta.get(key)
        if value:
            out.append(f"[{tag}:{value}]")
    out.append("[re:lyricsync]")
    if offset_ms:
        # LRC의 offset은 부호가 거꾸로다: 양수면 가사를 '빠르게' 당긴다.
        out.append(f"[offset:{-int(offset_ms)}]")
    if out:
        out.append("")

    for ln in lines:
        stamp = f"[{lrc_time(ln.start)}]"
        if word_level and ln.words:
            parts = [f"<{lrc_time(w.start)}>{w.display}" for w in ln.words]
            out.append(stamp + " ".join(parts))
        else:
            out.append(stamp + ln.text)

    # 마지막 줄이 언제 사라져야 하는지 알려주는 빈 태그. 없으면 끝까지 남는다.
    if lines:
        out.append(f"[{lrc_time(lines[-1].end)}]")
    return "\n".join(out) + "\n"


def to_srt(lines: list[TimedLine]) -> str:
    blocks: list[str] = []
    for i, ln in enumerate(lines, 1):
        blocks.append(
            f"{i}\n{srt_time(ln.start)} --> {srt_time(ln.end)}\n{ln.text}\n"
        )
    return "\n".join(blocks)


def to_vtt(lines: list[TimedLine]) -> str:
    out = ["WEBVTT", ""]
    for i, ln in enumerate(lines, 1):
        out.append(str(i))
        out.append(f"{vtt_time(ln.start)} --> {vtt_time(ln.end)}")
        out.append(ln.text)
        out.append("")
    return "\n".join(out)


def to_json(
    lines: list[TimedLine],
    meta: dict[str, str] | None = None,
) -> str:
    """편집기에 물리거나 직접 가공할 때 쓰는 전체 덤프."""
    payload = {
        "generator": "lyricsync",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "meta": meta or {},
        "lines": [
            {
                "index": ln.index,
                "text": ln.text,
                "start": round(ln.start, 3),
                "end": round(ln.end, 3),
                "duration": round(ln.duration, 3),
                "confidence": round(ln.confidence, 3),
                "interpolated": ln.interpolated,
                "words": [
                    {
                        "text": w.display,
                        "start": round(w.start, 3),
                        "end": round(w.end, 3),
                        "score": round(w.score, 3),
                    }
                    for w in ln.words
                ],
            }
            for ln in lines
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def to_tsv(lines: list[TimedLine]) -> str:
    """스프레드시트에 붙여넣고 눈으로 훑을 때."""
    rows = ["start\tend\tconfidence\tinterpolated\ttext"]
    for ln in lines:
        flag = "Y" if ln.interpolated else ""
        rows.append(
            f"{ln.start:.3f}\t{ln.end:.3f}\t{ln.confidence:.2f}\t{flag}\t{ln.text}"
        )
    return "\n".join(rows) + "\n"


WRITERS = {
    "lrc": to_lrc,
    "srt": to_srt,
    "vtt": to_vtt,
    "json": to_json,
    "tsv": to_tsv,
}


def render(
    fmt: str,
    lines: list[TimedLine],
    meta: dict[str, str] | None = None,
    word_level: bool = False,
    offset_ms: int = 0,
) -> str:
    """포맷 이름으로 렌더링. 포맷마다 받는 인자가 달라서 여기서 분기한다."""
    fmt = fmt.lower()
    if fmt == "lrc":
        return to_lrc(lines, meta=meta, word_level=word_level, offset_ms=offset_ms)
    if fmt == "json":
        return to_json(lines, meta=meta)
    if fmt in WRITERS:
        return WRITERS[fmt](lines)
    raise ValueError(f"모르는 포맷: {fmt} (가능: {', '.join(sorted(WRITERS))})")
