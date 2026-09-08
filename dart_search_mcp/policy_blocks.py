"""회계정책 주석 정제 텍스트를 기준서 항목·주제 표제 단위 블록으로 자른다.

왜 문장 창이 아니라 블록인가 — R18 연구(2026-09-04)에서 판정 문장이 이웃 기준서 항목에
붙는 사례(고려아연: 제1109호 개정 조기적용 문장이 "(4) 제1118호" 직전)가 나왔다.
본문 안의 번호("제1001호를 대체", "및 제1007호")는 헤더가 아니다.

Phase 1 T4(2026-09-08): 두 가지를 더 고친다.
(1) 문장 한가운데의 인용("…는 현행 기업회계기준서 제1001호 '재무제표 표시'를 대체할 예정이며")이
    따옴표 제목 규칙(`_TITLE_AFTER_RE`)만으로 항목 시작이 돼 앞 항목 블록을 84자에서 끊었다
    (실측: 이마트 20260318001024·삼성전자 20260310002820 — 제1118호 항목이 회사 문장에
    닿기 전에 잘려 판단 보류로 남았다). 따옴표 제목 규칙은 그 제목이 격조사로 문장에 묶여
    있지 않을 때만 쓴다(`_is_inline_citation`).
(2) 절 번호 표제("2.3 연결", "2.2.2 회사가 적용하지 않은 …")를 블록 종료 경계로 인정한다 —
    항목 블록이 다음 절까지 흘러 들어가 다른 절의 서문·본문을 근거로 삼는 것을 막는다.
그리고 항목이 속한 목록의 서문(lead)과 상위 절 번호(section)를 블록에 실어 준다 —
`policy_rules` 가 "이 항목은 당기 신규 적용 목록에 있다/조기 적용하지 않은 목록에 있다"를
판정할 수 있어야 하기 때문이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from dart_search_mcp.policy_sentences import SECTION_RE, find_lead, find_section

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
# Phase 1 T5(2026-09-08): 주제 표제 기호에 두 가지를 고친다.
# (1) 절 번호 표제("2.2.8 유형자산", "2.15 수익인식")를 항목 기호로 인정한다. 이 형태가 곧
#     회계정책 주석의 표준 표기인데(1,486 보고서 표본 중 293건) 기호 목록에 없어 그 절이 통째로
#     블록이 되지 못했다 — 실측 디와이디 20260430001895 는 「2.2.8 유형자산 … 감가상각누계액과
#     손상차손누계액을 차감한 잔액으로 표시」라는 원가모형 단정이 어느 블록에도 안 들어가
#     UNDETERMINED 로 남았다. 절 표제는 이미 `_SECTION_HDR_RE` 로 블록 종료 경계이므로,
#     시작으로도 인정하는 것은 경계 계산을 바꾸지 않는다(같은 자리에서 끝나고 시작한다).
#     lookbehind 는 `SECTION_RE` 와 같은 이유로 중첩 번호("1.2.2.8")의 안쪽 조각을 막는다.
# (2) 한글+마침표 기호(`[가-힣]\.`)는 종결 어미("…있습니다.")와 생김새가 같다 — 평문화로 마침표
#     뒤 공백이 사라지면 "…포함하고 있습니다.유형자산 중 토지는 …" 의 "다." 가 항목 기호로 잡혀
#     문장 한가운데서 새 주제 블록이 열린다(표본 1,486건 중 356블록). 앞 글자가 한글이면
#     낱말의 끝이지 항목 기호가 아니다(`policy_sentences._heading_start_ok` 와 같은 판단).
#     다만 이런 가짜 시작을 **무조건** 버리면 그 뒤 본문이 어느 블록에도 안 들어가 사라진다
#     (실측 1,486 보고서에서 진짜 재평가 단정 2건 손실 — 20260406000956·20260410000483).
#     그래서 바로 앞 주제 시작이 **같은 주제**일 때만 버린다 — 그때는 같은 절이 이어지는
#     것이라 앞 블록이 그 본문을 그대로 덮는다(실측 디와이디 20260430001895 2.2.8).
# (3) 표제 뒤 경계: 원문 XML 을 평문화하면 표제 태그와 본문 태그 사이의 공백이 사라져
#     "2.2.8 유형자산연결회사는 …" 처럼 표제와 본문 첫 낱말이 붙는다(실측 디와이디
#     20260430001895). 기존 `(?![가-힣])` 만으로는 이런 표제를 통째로 놓친다 — 뒤에 이어지는
#     것이 회사 주어면(연결회사는·회사는 …) 합성어가 아니라 본문의 시작이므로 표제로 본다.
#     "유형자산의 손상" 같은 진짜 합성 표제는 여전히 배제된다.
_TOPIC_BODY_SUBJECT = r"(?:연결회사|연결실체|연결기업|연결집단|당기업|당사|회사|그룹)(?:은|는|의|가)"
_TOPIC_HDR_RE = re.compile(
    r"(?:(?<!\d)(?<!\d\.)\d{1,2}(?:\.\d{1,2}){1,3}|\(\s*\d+\s*\)|\d+\s*\)|[①-⑳]|[가-힣]\.|\d+\.)"
    r"\s*(유형자산|투자부동산|수익\s*인식|수익)"
    rf"(?:(?![가-힣])|(?={_TOPIC_BODY_SUBJECT}))"
)
# 한글+마침표 기호가 낱말 끝(앞 글자가 한글)이면 종결 어미일 뿐 항목 기호가 아니다 — 위 (2).
# (`^` 를 쓰지 않는다 — `Pattern.match(s, pos)` 에서 `^` 는 pos 가 아니라 문자열 맨 앞에서만 맞는다.)
_MID_SENTENCE_HANGUL_MARK_RE = re.compile(r"[가-힣]\.")
_HANGUL_RE = re.compile(r"[가-힣]")
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
_SENTENCE_END_RE = re.compile(r"(?:다|니다|음|함|(?<!\d)\.)\s*$")
# 블록 자신의 양식을 못 찾을 때(예: 따옴표 제목 헤더처럼 항목 기호 없이 시작하는 기준서 블록)만 쓰는
# 대체 탐색 오프셋 — 시작 직후 텍스트가 자기 자신을 경계로 오인하지 않도록 여유를 둔다.
_GENERIC_FALLBACK_OFFSET = 60
# 절 번호 표제("2.3 연결", "2.2.2 회사가 …")는 블록 종료 경계다. 항목 기호와 달리 앞이 문장
# 끝일 것을 요구하지 않는다 — 평문화된 원문에서는 "…제정 기준서 2.3 연결"처럼 앞 문장에 붙어
# 나오는 쪽이 오히려 흔하다(실측: 에스텍 20260316001663).
# Fix round 2: 패턴을 policy_sentences 의 정본(SECTION_RE)과 **같은 것**으로 통일한다 —
# 경계 판단(여기)과 절 조회(find_section)가 서로 다른 패턴을 쓰면, 한쪽만 표제를 놓치고도 두
# 절 번호가 "일치"해 서문 귀속 검사가 조용히 통과한다(1차 리뷰 잔여 결함).
_SECTION_HDR_RE = SECTION_RE


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
    r"""i(후보 경계의 시작) 바로 앞이 문장 끝인지 — 단어 중간의 "물." 같은 우연한 매치를 걸러낸다.
    숫자 바로 뒤의 "."(점 날짜 "2027. 1. 1." 등)는 문장 끝으로 보지 않는다 — 여유 있게 앞쪽까지
    포함해 잘라(-8) `(?<!\d)` 룩비하인드가 윈도 경계에서 숫자를 놓치지 않게 한다."""
    return _SENTENCE_END_RE.search(text[max(0, i - 8) : i]) is not None


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
    """기준서 항목·주제 표제 하나에 해당하는 본문 조각.

    `lead`/`lead_polarity` 는 이 항목이 속한 목록의 서문이다(기준서 블록에만 붙는다) —
    항목 자체가 아무 말도 하지 않을 때 "당기 신규 적용 목록에 있다"/"조기 적용하지 않은
    목록에 있다"를 읽어내는 근거다. `section` 은 상위 절 번호("2.2.2")로 근거의 위치를
    사람이 확인할 수 있게 하는 표시용 값이다. 셋 다 기본값이 있어 기존 생성자 호출
    (테스트 픽스처 등)은 그대로 동작한다."""

    heading: str
    standard_no: str | None
    topic_heading: str | None
    start: int
    text: str
    capped: bool
    lead: str = ""
    lead_polarity: str = ""
    section: str = ""


def _at_sentence_start(text: str, pos: int) -> bool:
    """pos 가 문장 첫머리인가 — 앞이 텍스트 시작이거나 문장 끝(어미+마침표)이다."""
    pre = text[max(0, pos - 12) : pos]
    if not pre.strip():
        return True
    return _SENTENCE_END_RE.search(pre) is not None


# 헤더(+따옴표 제목) 바로 뒤에 붙는 격조사 — 그 기준서가 문장의 성분으로 묶여 있다는 신호다.
_BOUND_PARTICLE_RE = re.compile(r"^(?:를|을|은|는|이|가|의|와|과|에|로|으로)(?![가-힣])")
_QUOTE_CHARS = "'‘’“”\""
_QUOTED_TITLE_RE = re.compile(rf"^\s*[{_QUOTE_CHARS}][^{_QUOTE_CHARS}]{{0,80}}[{_QUOTE_CHARS}]")


def _is_inline_citation(post: str) -> bool:
    """헤더 뒤(따옴표 제목이 있으면 그 뒤)가 격조사로 이어지면 문장 안의 인용이다.

    T4: 항목 기호 없이 따옴표 제목만으로 항목 시작을 인정하던 규칙(`_TITLE_AFTER_RE`)이
    "…'재무제표 표시와 공시'는 현행 기업회계기준서 제1001호 '재무제표 표시'를 대체할
    예정이며 …" 의 인용까지 항목 시작으로 삼아, 앞 항목(제1118호) 블록을 84자에서 끊었다
    (실측: 이마트 20260318001024·삼성전자 20260310002820). 인용은 제목 뒤가 "…를/…는"
    처럼 격조사로 이어지고, 진짜 항목 표제는 그렇지 않다(제정/개정/시행일/줄바꿈이 온다)."""
    m = _QUOTED_TITLE_RE.match(post)
    return _BOUND_PARTICLE_RE.match(post[m.end() :] if m else post) is not None


def _standard_starts(text: str) -> list[tuple[int, str, None]]:
    out: list[tuple[int, str, None]] = []
    for m in _STD_HDR_RE.finditer(text):
        pre = text[max(0, m.start() - 12) : m.start()]
        post = text[m.end() : m.end() + 35]
        if _CONJ_TAIL_RE.search(pre.strip()[-6:]):
            continue
        title_start = _TITLE_AFTER_RE.search(post) and (
            _at_sentence_start(text, m.start()) or not _is_inline_citation(post)
        )
        if _ITEM_MARK_RE.search(pre) or title_start:
            out.append((m.start(), m.group(1), None))
    return out


def _topic_starts(text: str) -> list[tuple[int, None, str]]:
    out: list[tuple[int, None, str]] = []
    for m in _TOPIC_HDR_RE.finditer(text):
        name = re.sub(r"\s+", " ", m.group(1))
        topic = _TOPIC_CANON.get(name, name)
        mid_sentence = (
            m.start() > 0
            and _MID_SENTENCE_HANGUL_MARK_RE.match(text, m.start()) is not None
            and _HANGUL_RE.match(text, m.start() - 1) is not None
        )
        # 앞 주제 시작이 같은 주제이고 그 블록이 여기까지 닿을 수 있을 때(BLOCK_CAP 안)만 버린다 —
        # 더 멀면 앞 블록은 이미 끝났으므로 버리면 본문이 통째로 사라진다(실측 20260410000483:
        # 재무상태표 줄 「(2)유형자산(주석 5,7,…)」이 10,259자 앞에 있었다).
        if mid_sentence and out and out[-1][2] == topic and m.start() - out[-1][0] < BLOCK_CAP:
            continue
        out.append((m.start(), None, topic))
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
            search_from = own_end
            boundary = _earliest_boundary(text, (own_pat,), own_end, known_end)
        else:
            search_from = pos + _GENERIC_FALLBACK_OFFSET
            boundary = _earliest_boundary(text, _MARKER_PATTERNS, search_from, known_end)
        section_hit = _SECTION_HDR_RE.search(text, search_from, known_end) if search_from < known_end else None
        if section_hit is not None and (boundary is None or section_hit.start() < boundary):
            boundary = section_hit.start()
        natural_end = boundary if boundary is not None else known_end
        end = min(natural_end, pos + BLOCK_CAP)
        capped = natural_end > pos + BLOCK_CAP
        body = text[pos:end]
        # 서문·절 번호는 기준서 항목 블록에만 붙인다 — 주제 블록(유형자산·수익인식 등)의
        # 판정은 목록 귀속과 무관하고, 괜히 붙이면 그쪽 판정 규칙이 쓰지도 않을 값을 들고 다닌다.
        # 절 번호를 먼저 구해 서문 탐색에 넘긴다 — 서문은 항목과 같은 절 안에 있어야 한다.
        section = find_section(text, pos) if std is not None else ""
        lead = find_lead(text, pos, section=section) if std is not None else None
        blocks.append(
            PolicyBlock(
                heading=body[:60],
                standard_no=std,
                topic_heading=topic,
                start=pos,
                text=body,
                capped=capped,
                lead=lead[0] if lead else "",
                lead_polarity=lead[1] if lead else "",
                section=section,
            )
        )
    return blocks
