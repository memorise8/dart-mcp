"""주제 어휘와 규칙 판정. 어휘 정본은 docs/superpowers/specs/2026-09-04-policy-stance-vocabulary-design.md.

판정 순서(어휘 §3.4): 부정·부재 → 조건문 제거 → 긍정 단정 → 미적용/기타 → 허용만 → UNDETERMINED.
긍정값은 단정형 패턴으로만 나온다 — LLM 판정도 같은 패턴으로 검증한다(policy_llm.py).
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from dart_search_mcp.policy_blocks import PolicyBlock

UNDETERMINED = "UNDETERMINED"
_EVIDENCE_MAX = 400
_SENT_SPLIT_RE = re.compile(r"(?<=다\.)\s+|(?<=니다\.)\s+|(?<=음\.)\s+")


@dataclass(frozen=True, slots=True)
class Judgement:
    topic: str
    subject: str | None
    value: str
    evidence: str
    method: str  # "rule" | "llm_verified" | "llm" | "none" — llm* 은 policy_llm.py 가 붙인다


@dataclass(frozen=True, slots=True)
class TopicSpec:
    code: str
    values: tuple[str, ...]
    positive_values: frozenset[str]
    applies: Callable[[PolicyBlock], bool]
    subject_of: Callable[[PolicyBlock], str | None]
    negative: tuple[tuple[re.Pattern[str], str], ...]
    strip: tuple[re.Pattern[str], ...]
    positive: tuple[tuple[re.Pattern[str], str], ...]
    fallback: tuple[tuple[re.Pattern[str], str], ...]
    combo_value: str | None = None


def evidence_sentence(text: str, pos: int) -> str:
    """pos 를 포함한 문장 하나. 문장 경계를 못 찾으면 pos 앞뒤 200자.

    표(숫자 나열)처럼 문장 경계가 없어 "문장"이 400자를 넘으면, 문장 전체를 그대로 자르는 대신
    pos 를 중심으로 400자 창을 잡는다 — 그래야 매치 구절(pos)이 창 밖으로 밀려나지 않는다(R1).
    반환값은 항상 pos 위치의 글자를 포함한다."""
    start = 0
    end = len(text)
    for m in _SENT_SPLIT_RE.finditer(text):
        if m.end() <= pos:
            start = m.end()
        else:
            end = m.start()
            break
    sent = text[start:end]
    if len(sent) <= _EVIDENCE_MAX:
        stripped = sent.strip()
        return stripped or text[max(0, pos - 200) : pos + 200].strip()
    lo = max(start, pos - 150)
    hi = min(end, lo + _EVIDENCE_MAX)
    if hi - lo < _EVIDENCE_MAX:
        lo = max(start, hi - _EVIDENCE_MAX)
    return text[lo:hi].strip()


def _first_in_order(patterns: tuple[tuple[re.Pattern[str], str], ...], text: str) -> tuple[str, int] | None:
    """목록 순서가 우선순위 — 먼저 나열된 패턴이 어디에 있든 먼저 이긴다."""
    for pat, value in patterns:
        m = pat.search(text)
        if m:
            return (value, m.start())
    return None


def judge(spec: TopicSpec, block: PolicyBlock) -> Judgement:
    subject = spec.subject_of(block)
    text = block.text
    hit = _first_in_order(spec.negative, text)
    if hit is None:
        stripped = text
        for pat in spec.strip:
            stripped = pat.sub(" ", stripped)
        for group in (spec.positive, spec.fallback):
            hit = _first_in_order(group, stripped)
            if hit is not None:
                text = stripped
                break
    if hit is None:
        return Judgement(spec.code, subject, UNDETERMINED, "", "none")
    value, pos = hit
    # 복수 긍정값 승격: 긍정 패턴이 둘 이상 단정되면 조합값으로(수익인식 BOTH, 유형자산 MIXED)
    if spec.combo_value:
        found = {v for pat, v in spec.positive if pat.search(text)}
        if len(found) >= 2:
            value = spec.combo_value
    return Judgement(spec.code, subject, value, evidence_sentence(text, pos), "rule")


def positive_pattern_matches(spec: TopicSpec, value: str, evidence: str) -> bool:
    if value not in spec.positive_values:
        return False
    return any(v == value and pat.search(evidence) for pat, v in spec.positive)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# ---- T1 기준서 조기적용 -------------------------------------------------------
_T1_NEG = (
    (_p(r"조기\s*(?:적용|도입)\s*하지\s*(?:않|아니)"), "NOT_ADOPTED"),
    (_p(r"조기\s*(?:적용|도입)(?:하기로\s*한|한)\s*(?:기준서|사항|해석서)[^.]{0,40}없"), "NOT_ADOPTED"),
)
_T1_STRIP = (_p(r"조기\s*(?:적용|도입)\s*하기로\s*선택하는\s*경우[^.]*\."),)
_T1_POS = ((_p(r"조기\s*(?:적용|도입)\s*(?:하였|했|하고\s*있|하기로\s*(?:결정|하였|했))"), "EARLY_ADOPTED"),)
_T1_FALLBACK = (
    (_p(r"(?:채택|적용|도입)하지\s*(?:않|아니)|아직\s*채택|검토\s*중|평가\s*중|파악\s*중|영향(?:은|이)?\s*(?:중요하지|없)|적용할\s*예정|시행될\s*예정|부터\s*(?:적용|시행)(?:됩니다|되며|될|합니다)"), "NOT_ADOPTED"),
    (_p(r"조기\s*(?:적용|도입)이?\s*(?:허용|가능)"), "PERMITTED_ONLY"),
)

# ---- T2 유형자산 측정모형 ------------------------------------------------------
_T2_POS = (
    (_p(r"재평가\s*(?:모형|모델)(?:을|를)?\s*(?:적용|채택|사용)"), "REVALUATION_MODEL"),
    (_p(r"유형자산.{0,60}원가\s*(?:모형|모델)(?:을|를)?\s*(?:적용|채택|사용)"), "COST_MODEL"),
)

# ---- T4 수익인식 방법 ---------------------------------------------------------
# 평서체("~한다.")로 끝나는 문장은 기준서 인용·정의문으로 본다 — 회사 진술문은 합쇼체("~합니다.")다.
_T4_STRIP = (_p(r"[^.]*(?:한다|된다|있다|없다)\."),)
_T4_POS = (
    (_p(r"진행(?:기준|률)(?:에|을|로)?\s*(?:따라|의해|적용|기준으로)|투입법|기간에\s*걸쳐\s*(?:수익을\s*)?인식"), "OVER_TIME"),
    (_p(r"인도(?:시점|기준)|통제(?:가|를)\s*이전(?:하는|되는)\s*시점에\s*(?:수익을\s*)?인식"), "POINT_IN_TIME"),
)

# ---- T3 투자부동산 측정모형 -----------------------------------------------------
_T3_POS = (
    (_p(r"투자부동산.{0,80}공정가치\s*(?:모형|모델)(?:을|를|으로)?\s*(?:적용|채택|측정)"), "FAIR_VALUE_MODEL"),
    (_p(r"투자부동산.{0,80}원가\s*(?:모형|모델)(?:을|를|으로)?\s*(?:적용|채택|측정)"), "COST_MODEL"),
)


def _by_std(b: PolicyBlock) -> bool:
    return b.standard_no is not None


# 항목 기호(있으면) 뒤 표제 첫 토큰. "(6) 투자부동산 …" 처럼 본문 어딘가에 다른 주제 이름(유형자산)이
# 나열되더라도, 표제 자체가 그 이름으로 시작하지 않으면 그 주제로 오판하지 않는다.
_HEADING_PREFIX_RE = re.compile(
    r"^\s*(?:\(\s*\d+\s*\)|\(\s*[가-힣]\s*\)|\d+\s*\)|[①-⑳㉠-㉭]|[-·ㆍ•○■□▶▪◦]|[가-힣]\.|\d+\.)?\s*"
)


def _heading_starts_with(b: PolicyBlock, name: str) -> bool:
    m = _HEADING_PREFIX_RE.match(b.text)
    rest = b.text[m.end() :] if m else b.text
    return rest.startswith(name)


def _by_topic(name: str) -> Callable[[PolicyBlock], bool]:
    return lambda b: b.topic_heading == name or (b.standard_no is None and _heading_starts_with(b, name))


TOPICS: dict[str, TopicSpec] = {
    "STD_EARLY_ADOPTION": TopicSpec(
        code="STD_EARLY_ADOPTION",
        values=("EARLY_ADOPTED", "NOT_ADOPTED", "PERMITTED_ONLY", UNDETERMINED),
        positive_values=frozenset({"EARLY_ADOPTED"}),
        applies=_by_std,
        subject_of=lambda b: b.standard_no,
        negative=_T1_NEG, strip=_T1_STRIP, positive=_T1_POS, fallback=_T1_FALLBACK,
    ),
    "PPE_MEASUREMENT": TopicSpec(
        code="PPE_MEASUREMENT",
        values=("COST_MODEL", "REVALUATION_MODEL", "MIXED", UNDETERMINED),
        positive_values=frozenset({"REVALUATION_MODEL", "COST_MODEL"}),
        applies=_by_topic("유형자산"),
        subject_of=lambda b: None,
        negative=(), strip=(_p(r"[^.]*(?:원가|재평가)\s*(?:모형|모델)\s*(?:또는|이나|중)[^.]*\."),),
        positive=_T2_POS, fallback=(),
        combo_value="MIXED",
    ),
    "REVENUE_METHOD": TopicSpec(
        code="REVENUE_METHOD",
        values=("OVER_TIME", "POINT_IN_TIME", "BOTH", UNDETERMINED),
        positive_values=frozenset({"OVER_TIME", "POINT_IN_TIME", "BOTH"}),
        applies=_by_topic("수익인식"),
        subject_of=lambda b: None,
        negative=(), strip=_T4_STRIP, positive=_T4_POS, fallback=(),
        combo_value="BOTH",
    ),
    "IP_MEASUREMENT": TopicSpec(
        code="IP_MEASUREMENT",
        values=("COST_MODEL", "FAIR_VALUE_MODEL", UNDETERMINED),
        positive_values=frozenset({"FAIR_VALUE_MODEL", "COST_MODEL"}),
        applies=_by_topic("투자부동산"),
        subject_of=lambda b: None,
        negative=(), strip=(_p(r"[^.]*(?:원가|공정가치)\s*(?:모형|모델)\s*(?:또는|이나|중)[^.]*\."),),
        positive=_T3_POS, fallback=(),
    ),
}
