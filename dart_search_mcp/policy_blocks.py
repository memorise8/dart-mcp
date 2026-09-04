"""회계정책 주석 정제 텍스트를 기준서 항목·주제 표제 단위 블록으로 자른다.

왜 문장 창이 아니라 블록인가 — R18 연구(2026-09-04)에서 판정 문장이 이웃 기준서 항목에
붙는 사례(고려아연: 제1109호 개정 조기적용 문장이 "(4) 제1118호" 직전)가 나왔다.
본문 안의 번호("제1001호를 대체", "및 제1007호")는 헤더가 아니다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BLOCK_CAP = 3000

_STD_HDR_RE = re.compile(
    r"(?:기업회계기준서|기업회계기준해석서|한국채택국제회계기준)\s*제\s*(\d{4})\s*호"
)
# 헤더 직전 12자에 항목 기호가 있으면 항목 시작이다
_ITEM_MARK_RE = re.compile(
    r"(?:\(\s*\d+\s*\)|\(\s*[가-힣]\s*\)|\d+\s*\)|[①-⑳㉠-㉭]|[-·ㆍ•○■□▶▪◦]|[가-힣]\.|\d+\.)\s*$"
)
# 헤더 직전이 접속·목적격이면 본문 안의 인용이다("및 제1007호", "제1001호를 대체")
_CONJ_TAIL_RE = re.compile(r"(?:및|과|와|,|를|을|는|은|의|대체하며|대체합니다|대체)\s*$")
# 헤더 직후 35자 안의 제정/개정 표시 또는 따옴표 제목도 항목 시작으로 본다
_TITLE_AFTER_RE = re.compile(r"^\s*['\"]|(?:제정|개정)")
_TOPIC_HDR_RE = re.compile(
    r"(?:\(\s*\d+\s*\)|\d+\s*\)|[①-⑳]|[가-힣]\.|\d+\.)\s*(유형자산|투자부동산|수익\s*인식|수익)(?![가-힣])"
)
_TOPIC_CANON = {"수익 인식": "수익인식", "수익": "수익인식"}


@dataclass(frozen=True, slots=True)
class PolicyBlock:
    heading: str
    standard_no: str | None
    topic_heading: str | None
    start: int
    text: str
    capped: bool


def _standard_starts(text: str) -> list[tuple[int, str, None]]:
    out: list[tuple[int, str, None]] = []
    for m in _STD_HDR_RE.finditer(text):
        pre = text[max(0, m.start() - 12) : m.start()]
        post = text[m.end() : m.end() + 35]
        if _CONJ_TAIL_RE.search(pre.strip()[-6:]):
            continue
        if _ITEM_MARK_RE.search(pre) or _TITLE_AFTER_RE.search(post):
            out.append((m.start(), m.group(1), None))
    return out


def _topic_starts(text: str) -> list[tuple[int, None, str]]:
    out: list[tuple[int, None, str]] = []
    for m in _TOPIC_HDR_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1))
        out.append((m.start(), None, _TOPIC_CANON.get(name, name)))
    return out


def split_policy_blocks(text: str) -> list[PolicyBlock]:
    starts = sorted(_standard_starts(text) + _topic_starts(text), key=lambda s: s[0])
    blocks: list[PolicyBlock] = []
    for i, (pos, std, topic) in enumerate(starts):
        # 같은 기준서가 800자 안에서 다시 나오면 같은 블록(제목 반복 관행)
        if i > 0 and std is not None and starts[i - 1][1] == std and pos - starts[i - 1][0] < 800:
            continue
        j = i + 1
        while j < len(starts) and std is not None and starts[j][1] == std:
            j += 1
        end = starts[j][0] if j < len(starts) else len(text)
        capped = end - pos > BLOCK_CAP
        body = text[pos : min(end, pos + BLOCK_CAP)]
        blocks.append(
            PolicyBlock(
                heading=body[:60],
                standard_no=std,
                topic_heading=topic,
                start=pos,
                text=body,
                capped=capped,
            )
        )
    return blocks
