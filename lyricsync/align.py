"""정렬 코어.

두 가지 모드를 담당한다.

1. 가사 텍스트가 있을 때 (`align_lyrics`)
   ASR이 받아쓴 단어 시퀀스와, 사용자가 준 정식 가사의 단어 시퀀스를
   Needleman-Wunsch 전역 정렬로 맞춰서, 가사 줄마다 시작/끝 시각을 얻는다.
   출력 글자는 항상 사용자 가사 쪽을 쓴다. ASR에서는 '시간'만 빌려온다.

2. 가사 텍스트가 없을 때 (`segment_words`)
   ASR 단어를 쉼/길이 기준으로 끊어서 자막 줄을 만든다.

ASR은 반드시 틀린다는 전제로 설계했다. 못 맞춘 줄은 버리지 않고
앞뒤 줄 사이에 비례 배분해서 채워 넣고, confidence를 낮게 매겨
나중에 사람이 손볼 지점을 알려준다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache

from .normalize import decompose_hangul, is_section_header, tokenize

# --- 정렬 파라미터 -------------------------------------------------------
# 이 값들은 한국어 가요 기준으로 적당히 잡은 것. 결과가 이상하면 여기부터 만진다.
GAP_PENALTY = -0.5          # 한쪽을 건너뛸 때 비용
SUB_BIAS = -0.8             # 유사도 0인 단어끼리 억지로 묶일 때의 벌점
MIN_MATCH_SIM = 0.55        # 이 아래 유사도는 "맞춘 것"으로 치지 않는다
MIN_LINE_DURATION = 0.4     # 한 줄이 가질 수 있는 최소 길이(초)
DEFAULT_TAIL = 3.0          # 마지막 줄을 끝에서 얼마나 더 끌지(초)


@dataclass
class Word:
    """ASR이 뱉은 단어 하나.

    text는 비교용으로 정규화된 표기, raw는 ASR 원문 표기다.
    정렬은 text로 하고, 가사 없이 출력할 때는 raw를 쓴다.
    """

    text: str
    start: float
    end: float
    score: float = 1.0
    raw: str = ""

    @property
    def display(self) -> str:
        """사람이 읽을 표기. raw가 없으면 정규화된 쪽이라도 쓴다."""
        return self.raw or self.text


@dataclass
class TimedLine:
    """시간이 붙은 최종 출력 한 줄."""

    index: int
    text: str
    start: float
    end: float
    confidence: float = 1.0
    interpolated: bool = False
    words: list[Word] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@lru_cache(maxsize=1 << 16)
def token_similarity(a: str, b: str) -> float:
    """단어 두 개의 유사도 0.0~1.0.

    자모 단위로 비교해서 받침 오인식("있어" vs "이써") 같은 흔한 실수에
    관대하게 만든다.
    """
    if a == b:
        return 1.0
    da, db = decompose_hangul(a), decompose_hangul(b)
    if da == db:
        return 0.97
    # 길이 차가 너무 크면 비교할 것도 없다
    if abs(len(da) - len(db)) > max(len(da), len(db)):
        return 0.0
    return SequenceMatcher(None, da, db).ratio()


# --- 가사 파싱 -----------------------------------------------------------


@dataclass
class LyricLine:
    index: int
    text: str
    tokens: list[str]


def parse_lyrics(raw: str) -> list[LyricLine]:
    """가사 원문을 줄 단위로 파싱.

    빈 줄과 '[Chorus]' 같은 섹션 표기는 출력에서 제외한다.
    토큰이 하나도 안 남는 줄(기호만 있는 줄)도 뺀다.
    """
    lines: list[LyricLine] = []
    for raw_line in raw.splitlines():
        text = raw_line.strip()
        if not text or is_section_header(text):
            continue
        tokens = tokenize(text)
        if not tokens:
            continue
        lines.append(LyricLine(index=len(lines), text=text, tokens=tokens))
    return lines


# --- Needleman-Wunsch ----------------------------------------------------

# traceback 방향
_DIAG, _UP, _LEFT = 0, 1, 2


def _needleman_wunsch(a: list[str], b: list[str]) -> list[tuple[int | None, int | None]]:
    """전역 정렬. (a_index, b_index) 쌍 목록을 반환하며 건너뛴 쪽은 None."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return [(i, None) for i in range(n)] + [(None, j) for j in range(m)]

    # 한 행씩만 들고 간다. traceback은 bytearray로 전체 보관(1칸 1바이트).
    prev = [GAP_PENALTY * j for j in range(m + 1)]
    trace = bytearray((n + 1) * (m + 1))

    for j in range(1, m + 1):
        trace[j] = _LEFT

    for i in range(1, n + 1):
        cur = [0.0] * (m + 1)
        cur[0] = GAP_PENALTY * i
        row_off = i * (m + 1)
        trace[row_off] = _UP
        ai = a[i - 1]
        for j in range(1, m + 1):
            diag = prev[j - 1] + (2.0 * token_similarity(ai, b[j - 1]) + SUB_BIAS)
            up = prev[j] + GAP_PENALTY
            left = cur[j - 1] + GAP_PENALTY
            if diag >= up and diag >= left:
                cur[j] = diag
                trace[row_off + j] = _DIAG
            elif up >= left:
                cur[j] = up
                trace[row_off + j] = _UP
            else:
                cur[j] = left
                trace[row_off + j] = _LEFT
        prev = cur

    # traceback
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i == 0:
            pairs.append((None, j - 1))
            j -= 1
            continue
        if j == 0:
            pairs.append((i - 1, None))
            i -= 1
            continue
        move = trace[i * (m + 1) + j]
        if move == _DIAG:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif move == _UP:
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    return pairs


# --- 메인 정렬 -----------------------------------------------------------


def align_lyrics(
    lyrics: list[LyricLine],
    words: list[Word],
    audio_duration: float | None = None,
) -> list[TimedLine]:
    """가사 줄들에 ASR 단어의 시각을 입힌다."""
    if not lyrics:
        return []
    if not words:
        # ASR이 아무것도 못 건졌으면 균등 분배라도 해준다
        total = audio_duration or float(len(lyrics) * 4)
        step = total / len(lyrics)
        return [
            TimedLine(
                index=ln.index,
                text=ln.text,
                start=i * step,
                end=(i + 1) * step,
                confidence=0.0,
                interpolated=True,
            )
            for i, ln in enumerate(lyrics)
        ]

    # 가사 토큰을 평탄화하면서 각 토큰이 몇 번째 줄 소속인지 기록
    flat: list[str] = []
    owner: list[int] = []
    for ln in lyrics:
        for tok in ln.tokens:
            flat.append(tok)
            owner.append(ln.index)

    asr_tokens = [w.text for w in words]
    pairs = _needleman_wunsch(flat, asr_tokens)

    # 줄별로 매칭된 ASR 단어 모으기
    hits: dict[int, list[int]] = {ln.index: [] for ln in lyrics}
    matched_tokens: dict[int, int] = {ln.index: 0 for ln in lyrics}
    for li, wi in pairs:
        if li is None or wi is None:
            continue
        if token_similarity(flat[li], asr_tokens[wi]) < MIN_MATCH_SIM:
            continue
        line_idx = owner[li]
        hits[line_idx].append(wi)
        matched_tokens[line_idx] += 1

    results: list[TimedLine] = []
    for ln in lyrics:
        idx = hits[ln.index]
        if idx:
            lo, hi = min(idx), max(idx)
            conf = matched_tokens[ln.index] / max(1, len(ln.tokens))
            results.append(
                TimedLine(
                    index=ln.index,
                    text=ln.text,
                    start=words[lo].start,
                    end=words[hi].end,
                    confidence=min(1.0, conf),
                    words=[words[k] for k in sorted(idx)],
                )
            )
        else:
            results.append(
                TimedLine(
                    index=ln.index,
                    text=ln.text,
                    start=-1.0,
                    end=-1.0,
                    confidence=0.0,
                    interpolated=True,
                )
            )

    total = audio_duration or (words[-1].end + DEFAULT_TAIL)
    _fill_gaps(results, total)
    _enforce_monotonic(results, total)
    return results


def _fill_gaps(lines: list[TimedLine], total: float) -> None:
    """시간을 못 받은 줄을, 앞뒤로 확정된 줄 사이에 균등 배분한다."""
    n = len(lines)
    i = 0
    while i < n:
        if lines[i].start >= 0:
            i += 1
            continue
        j = i
        while j < n and lines[j].start < 0:
            j += 1
        # [i, j) 가 빈 구간
        left = lines[i - 1].end if i > 0 else 0.0
        right = lines[j].start if j < n else total
        if right < left:
            right = left
        span = (right - left) / (j - i)
        for k in range(i, j):
            lines[k].start = left + span * (k - i)
            lines[k].end = left + span * (k - i + 1)
        i = j


def _enforce_monotonic(lines: list[TimedLine], total: float) -> None:
    """시간이 뒤로 가거나 겹치지 않게 정리."""
    cursor = 0.0
    for ln in lines:
        if ln.start < cursor:
            ln.start = cursor
        if ln.end < ln.start + MIN_LINE_DURATION:
            ln.end = ln.start + MIN_LINE_DURATION
        cursor = ln.start
    # 끝 시각이 다음 줄 시작을 침범하지 않게
    for a, b in zip(lines, lines[1:]):
        if a.end > b.start:
            a.end = max(a.start + 0.05, b.start)
    if lines and total > 0:
        lines[-1].end = min(max(lines[-1].end, lines[-1].start + MIN_LINE_DURATION), total)


# --- 가사 없이 ASR만 쓸 때 ----------------------------------------------


def segment_words(
    words: list[Word],
    max_chars: int = 38,
    max_gap: float = 0.9,
    max_duration: float = 7.0,
) -> list[TimedLine]:
    """ASR 단어를 쉼/길이 기준으로 끊어 자막 줄을 만든다."""
    lines: list[TimedLine] = []
    buf: list[Word] = []

    def flush() -> None:
        if not buf:
            return
        text = " ".join(w.display for w in buf).strip()
        if text:
            scores = [w.score for w in buf if w.score is not None]
            lines.append(
                TimedLine(
                    index=len(lines),
                    text=text,
                    start=buf[0].start,
                    end=buf[-1].end,
                    confidence=sum(scores) / len(scores) if scores else 0.5,
                    words=list(buf),
                )
            )
        buf.clear()

    for w in words:
        if buf:
            gap = w.start - buf[-1].end
            length = len(" ".join(x.display for x in buf)) + 1 + len(w.display)
            dur = w.end - buf[0].start
            if gap > max_gap or length > max_chars or dur > max_duration:
                flush()
        buf.append(w)
    flush()
    return lines


def shift(lines: list[TimedLine], offset: float) -> list[TimedLine]:
    """전체 타임스탬프를 offset(초)만큼 민다. 음수면 당긴다."""
    for ln in lines:
        ln.start = max(0.0, ln.start + offset)
        ln.end = max(ln.start, ln.end + offset)
        for w in ln.words:
            w.start = max(0.0, w.start + offset)
            w.end = max(w.start, w.end + offset)
    return lines
