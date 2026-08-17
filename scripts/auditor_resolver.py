"""감사인 복구 — 제출인(flr_nm) 오염 7.3% 를 섹션 최하단 서명에서 되찾는다.

감사인 확인 위치 (사용자 제보, DART_AGENT_INTEGRATION_STATE.md §13.4):
  1. 제출인(flr_nm) — 회사가 직접 제출하면 회사명이 들어간다. 현재 파서가 여기서 깨졌다.
  2. 감사보고서 섹션 최하단 서명
  3. 독립된 감사인의 감사보고서 섹션 최하단 서명
정책: flr_nm 이 이미 회계법인/감사반이면 그대로(92.7%), 아니면 3→2→문서끝 순으로 복구.
"""

from __future__ import annotations

import re

_TITLE_RE = re.compile(r"<TITLE[^>]*>(.*?)</TITLE>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# 평문화 시 문단/행 경계에 심는 구분자 — 공백이 아니므로 _FIRM_RE 의 \s* 가
# 건너뛰지 못한다. TD(표 셀)는 제외한다 — 서명이 셀마다 한 글자씩 흩어지는
# 경우(§13.4)가 많아 같은 행 안에서는 공백으로 계속 이어 붙어야 한다.
# 주의: U+001C~U+001F(정보 구분자 제어문자)는 파이썬 \s 에 매칭되므로 쓰면
# 안 된다 — NUL(\x00)은 \s 에 매칭되지 않고 실제 문서에도 나타나지 않는다.
_BOUNDARY = "\x00"
_PARA_BOUNDARY_RE = re.compile(r"</(P|TH|SUMMARY|TR|TABLE)\s*>", re.IGNORECASE)

# 서명란은 표 셀에 한 글자씩 흩어지는 경우가 많아 글자 사이 공백을 허용한다.
_FIRM_RE = re.compile(
    r"((?:[가-힣]\s*){0,12}회\s*계\s*법\s*인(?:\s*[가-힣]){0,10}"
    r"|(?:[가-힣]\s*){2,12}감\s*사\s*반(?:\s*\(\s*제?\s*\d+\s*호?\s*\))?)"
)
# 상호 앞에 눌어붙는 주소 잔재 / 뒤에 붙는 직함 꼬리
_HEAD_NOISE_RE = re.compile(r"^.*?(?:빌딩|타워|센터|플라자|씨티)")
_TAIL_NOISE_RE = re.compile(r"(?:대표이사|대표사원|이사장|대표|외\d*명).*$")

_SECTION_INDEP_RE = re.compile(r"독립된\s*감사인의\s*감사보고서")
_SECTION_AUDIT_RE = re.compile(r"^감\s*사\s*보\s*고\s*서$")

TAIL_CHARS = 900       # 섹션 끝에서 이만큼만 본다 (서명란은 항상 최하단)
DOC_TAIL_CHARS = 4000  # 섹션을 못 찾을 때 문서 끝 폴백


def is_firm(name: str) -> bool:
    """회계법인 또는 감사반이면 감사인으로 본다."""
    return bool(name) and ("회계법인" in name or "감사반" in name)


def clean_firm(raw: str) -> str:
    """자간 공백 제거 + 주소 잔재 머리 제거 + 직함 꼬리 제거."""
    name = _WS_RE.sub("", raw)
    name = _HEAD_NOISE_RE.sub("", name)
    if name.startswith("회계법인"):
        # '회계법인 ○○ 대표이사 김…' 어순 — 직함부터 꼬리를 잘라낸다
        name = _TAIL_NOISE_RE.sub("", name)
    else:
        # '○○회계법인 대표이사 김…' — 법인명 뒤 꼬리를 잘라낸다
        m = re.match(r"^(.*?(?:회계법인|감사반(?:\(제?\d+호?\))?))", name)
        if m:
            name = m.group(1)
    return name


def _flatten(xml_text: str) -> str:
    """태그를 지우고 평문으로 만들되, 문단/행 경계(P·TH·SUMMARY·TR·TABLE)에는
    구분자를 심어 서로 다른 문단의 한글이 상호명 정규식에 섞여 들어가지
    않게 막는다 (예: '본문' 문단 + '삼정회계법인' 문단이 '본문삼정회계법인'
    으로 오염되는 것을 방지). TD 끼리는 구분자 없이 이어 붙어 같은 행 안의
    글자 분산 서명은 그대로 인식된다."""
    marked = _PARA_BOUNDARY_RE.sub(lambda mm: mm.group(0) + _BOUNDARY, xml_text)
    marked = marked.replace("&cr;", " ")
    plain = _TAG_RE.sub(" ", marked)
    return _WS_RE.sub(" ", plain).strip()


def _section_text(xml_text: str, title_pattern: re.Pattern[str]) -> str | None:
    """제목이 패턴에 맞는 TITLE 부터 다음 TITLE 직전까지의 평문."""
    for m in _TITLE_RE.finditer(xml_text):
        title = _WS_RE.sub(" ", _TAG_RE.sub("", m.group(1))).strip()
        if title_pattern.search(title):
            rest = xml_text[m.end():]
            nxt = re.search(r"<TITLE[^>]*>", rest, re.IGNORECASE)
            segment = rest[: nxt.start()] if nxt else rest
            text = _flatten(segment)
            return text or None
    return None


def _tail_firm(text: str | None, tail: int = TAIL_CHARS) -> str:
    """구역 최하단에서 마지막 상호 매칭을 정리해 반환."""
    if not text:
        return ""
    hits = _FIRM_RE.findall(text[-tail:])
    if not hits:
        return ""
    return clean_firm(hits[-1])


def extract_tail_firm(xml_text: str) -> str:
    """독립된 감사인의 감사보고서 → 감사보고서 → 문서 끝 순으로 상호를 찾는다."""
    for pattern in (_SECTION_INDEP_RE, _SECTION_AUDIT_RE):
        firm = _tail_firm(_section_text(xml_text, pattern))
        if firm:
            return firm
    plain = _flatten(xml_text[-DOC_TAIL_CHARS:])
    return _tail_firm(plain, tail=len(plain))


def resolve_auditor(xml_text: str, flr_nm: str) -> tuple[str, str]:
    """(auditor_resolved, auditor_source). 원본 flr_nm 은 호출자가 별도 보존한다."""
    if is_firm(flr_nm):
        return flr_nm, "flr_nm"
    for pattern, source in ((_SECTION_INDEP_RE, "section_tail"), (_SECTION_AUDIT_RE, "section_tail")):
        firm = _tail_firm(_section_text(xml_text, pattern))
        if firm:
            return firm, source
    plain = _flatten(xml_text[-DOC_TAIL_CHARS:])
    firm = _tail_firm(plain, tail=len(plain))
    if firm:
        return firm, "doc_tail"
    return "", "unresolved"
