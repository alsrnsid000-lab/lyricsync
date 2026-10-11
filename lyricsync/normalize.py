"""텍스트 정규화 유틸.

가사 텍스트와 ASR 결과는 표기가 다를 수밖에 없다("사랑해" vs "사랑 해",
"OK" vs "오케이"). 비교 전에 양쪽을 같은 형태로 깎아내기 위한 모듈.
"""

from __future__ import annotations

import re
import unicodedata

# 한글 유니코드 상수
HANGUL_BASE = 0xAC00
HANGUL_LAST = 0xD7A3

CHOSUNG = [
    "ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]
JUNGSUNG = [
    "ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ",
    "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ",
]
JONGSUNG = [
    "", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ",
    "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ",
    "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ",
]

# 괄호 안 지시문: (x3), (Rap), [Chorus], (후렴) 등
_BRACKET_RE = re.compile(r"[\(\[\{][^\)\]\}]{0,40}[\)\]\}]")
# 비교에 쓸 글자만 남긴다: 한글/영문/숫자
_KEEP_RE = re.compile(r"[^0-9a-z\uac00-\ud7a3\u3131-\u318e]")

# LRC 등에서 흔한 섹션 표기 — 가사 줄로 치지 않는다
_SECTION_RE = re.compile(
    r"^[\(\[]?\s*(verse|chorus|bridge|intro|outro|hook|pre[-\s]?chorus|refrain|rap|"
    r"후렴|간주|전주|후주|도입|브릿지|벌스|코러스|랩)\s*\d*\s*[\)\]]?$",
    re.IGNORECASE,
)


def decompose_hangul(text: str) -> str:
    """한글 음절을 자모로 분해. 그 외 문자는 그대로 둔다.

    '강' -> 'ㄱㅏㅇ'. 발음이 비슷한데 받침만 다른 경우(ASR이 자주 틀리는 지점)
    유사도를 글자 단위가 아니라 자모 단위로 재면 훨씬 관대해진다.
    """
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if HANGUL_BASE <= code <= HANGUL_LAST:
            idx = code - HANGUL_BASE
            out.append(CHOSUNG[idx // 588])
            out.append(JUNGSUNG[(idx % 588) // 28])
            jong = JONGSUNG[idx % 28]
            if jong:
                out.append(jong)
        else:
            out.append(ch)
    return "".join(out)


def normalize_token(token: str) -> str:
    """단어 하나를 비교용 형태로 정규화. 비교 가치가 없으면 빈 문자열."""
    token = unicodedata.normalize("NFC", token).lower()
    token = _KEEP_RE.sub("", token)
    return token


def tokenize(text: str) -> list[str]:
    """한 줄을 비교용 토큰 리스트로. 빈 토큰은 버린다."""
    raw = _BRACKET_RE.sub(" ", text)
    tokens = [normalize_token(t) for t in raw.split()]
    return [t for t in tokens if t]


def is_section_header(line: str) -> bool:
    """'[Chorus]', '(후렴)' 같은 구조 표기인지."""
    return bool(_SECTION_RE.match(line.strip()))
