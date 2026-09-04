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
_TITLE_AFTER_RE = re.compile('^\\s*[\'‘’“”"]|(?:제정|개정)')
_TOPIC_HDR_RE = re.compile(
    r"(?:\(\s*\d+\s*\)|\d+\s*\)|[①-⑳]|[가-힣]\.|\d+\.)\s*(유형자산|투자부동산|수익\s*인식|수익)(?![가-힣])"
)
_TOPIC_CANON = {"수익 인식": "수익인식", "수익": "수익인식"}

# 알려진 시작점(기준서 헤더·주제 표제) 외에, "일반 항목 기호 + 한글 표제" 위치도 블록 종료 경계로 본다.
# 다만 본문 안의 나열("① 손익계산서 ② 현금흐름표")까지 경계로 잡으면 판정 문장이 잘려나가므로,
# (a) 블록 자신과 같은 양식의 기호이고, (b) 기호 뒤에 한글 표제가 오고, (c) 그 기호 앞이 문장 끝(마침표/
# "다"/"니다"/"음"/"함")일 때만 경계로 인정한다 — R18 review Fix round 1.
_MARKER_STYLES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("paren_num", re.compile(r"\(\s*\d{1,2}\s*\)")),
    ("num_paren", re.compile(r"\d{1,2}\s*\)")),
    ("circled", re.compile(r"[①-⑳]")),
    ("hangul_dot", re.compile(r"[가-힣]\.")),
    ("num_dot", re.compile(r"\d{1,2}\.")),
    ("paren_hangul", re.compile(r"\(\s*[가-힣]\s*\)")),
)
_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(pat for _, pat in _MARKER_STYLES)
_HEADING_AFTER_RE = re.compile(r"\s*[가-힣]")
_SENTENCE_END_RE = re.compile(r"(?:다|니다|음|함|\.)\s*$")
# 블록 자신의 양식을 못 찾을 때(예: 따옴표 제목 헤더처럼 항목 기호 없이 시작하는 기준서 블록)만 쓰는
# 대체 탐색 오프셋 — 시작 직후 텍스트가 자기 자신을 경계로 오인하지 않도록 여유를 둔다.
_GENERIC_FALLBACK_OFFSET = 60


def _own_marker(text: str, pos: int) -> tuple[re.Pattern[str], int] | None:
    """pos 에서 시작하는 블록 자신의 항목 기호 양식과 그 기호가 끝나는 위치.
    기호 뒤에 한글 표제가 와야 진짜 항목 기호로 본다. 기호가 없으면(예: 따옴표 제목 헤더) None."""
    ws = re.match(r"\s*", text[pos:])
    start = pos + (ws.end() if ws else 0)
    for _, pat in _MARKER_STYLES:
        m = pat.match(text, start)
        if m is not None and _HEADING_AFTER_RE.match(text, m.end()):
            return pat, m.end()
    return None


def _is_sentence_adjacent(text: str, i: int) -> bool:
    """i(후보 경계의 시작) 바로 앞이 문장 끝인지 — 단어 중간의 "물." 같은 우연한 매치를 걸러낸다."""
    return _SENTENCE_END_RE.search(text[max(0, i - 6) : i]) is not None


def _earliest_boundary(
    text: str, patterns: tuple[re.Pattern[str], ...], search_from: int, limit: int
) -> int | None:
    """search_from..limit 구간에서, 패턴별로 (기호 뒤 한글 표제 + 기호 앞 문장 끝)을 만족하는 첫 매치를
    찾고, 그중 가장 이른 위치를 돌려준다. 조건을 못 만족하는 매치(본문 안 나열·단어 중간 우연 일치)는
    건너뛰고 계속 찾는다."""
    if search_from >= limit:
        return None
    best: int | None = None
    for pat in patterns:
        i = search_from
        while i < limit:
            m = pat.search(text, i, limit)
            if m is None:
                break
            if _HEADING_AFTER_RE.match(text, m.end()) and _is_sentence_adjacent(text, m.start()):
                if best is None or m.start() < best:
                    best = m.start()
                break
            i = m.start() + 1
    return best


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
        known_end = starts[j][0] if j < len(starts) else len(text)
        own = _own_marker(text, pos)
        if own is not None:
            own_pat, own_end = own
            boundary = _earliest_boundary(text, (own_pat,), own_end, known_end)
        else:
            boundary = _earliest_boundary(text, _MARKER_PATTERNS, pos + _GENERIC_FALLBACK_OFFSET, known_end)
        natural_end = boundary if boundary is not None else known_end
        end = min(natural_end, pos + BLOCK_CAP)
        capped = natural_end > pos + BLOCK_CAP
        body = text[pos:end]
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
