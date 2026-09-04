"""규칙이 UNDETERMINED 로 남긴 블록만 소형 LLM 에 묻는다.

R18 실측: gpt-5.4-mini 는 조건문에 속지 않고(55/60 을 허용만으로), 양성 9/9 를 잡았으나
1건(영흥, "비교정보 재작성")을 조기적용으로 오판했다. 그래서 긍정값은 단정형 규칙 패턴과
evidence 가 맞을 때만 채택한다(llm_verified).
"""
from __future__ import annotations

import json

from dart_search_mcp.policy_blocks import PolicyBlock
from dart_search_mcp.policy_rules import UNDETERMINED, Judgement, TopicSpec, positive_pattern_matches
from dart_search_mcp.tools.kam_tagger import DEFAULT_BASE_URL, DEFAULT_MODEL, CallFn, KamLlmError, call_llm

_BLOCK_MAX = 2500
_LABEL_HELP = {
    "STD_EARLY_ADOPTION": (
        "- EARLY_ADOPTED: 회사가 의무 시행일 이전에 이 기준서(또는 그 개정)를 실제로 조기 적용했다고 명시함. "
        "'조기적용하기로 선택하는 경우 …' 같은 조건문이나 '조기 적용이 허용된다'는 설명은 해당하지 않음\n"
        "- NOT_ADOPTED: 아직 적용·채택하지 않았음을 명시하거나 그렇게 읽힘(시행일부터 적용 예정, 영향 검토·평가·파악 중, 중요한 영향 없음 등)\n"
        "- PERMITTED_ONLY: 기준서 설명과 조기적용 허용 여부만 있고 회사 자신의 적용 여부를 읽어낼 수 없음"
    ),
    "PPE_MEASUREMENT": "- COST_MODEL: 유형자산에 원가모형 적용\n- REVALUATION_MODEL: 재평가모형 적용\n- MIXED: 자산군별로 둘 다",
    "REVENUE_METHOD": "- OVER_TIME: 진행기준·진행률·투입법으로 기간에 걸쳐 인식\n- POINT_IN_TIME: 인도·통제이전 시점에 인식\n- BOTH: 둘 다",
    "IP_MEASUREMENT": "- COST_MODEL: 투자부동산에 원가모형\n- FAIR_VALUE_MODEL: 공정가치모형",
}
_SUBJECT_LINE = {"STD_EARLY_ADOPTION": "기업회계기준서 제{subject}호를 다룬 항목입니다. 다른 기준서에 대한 서술은 무시하세요."}


def build_prompt(spec: TopicSpec, block: PolicyBlock, subject: str | None) -> str:
    head = _SUBJECT_LINE.get(spec.code, "").format(subject=subject or "?")
    return (
        "당신은 한국 감사보고서 주석을 읽는 회계 전문가입니다. 아래 지문은 회사의 회계정책 주석 항목입니다. "
        f"{head}\n회사의 상태를 다음 중 하나로 판정하세요.\n{_LABEL_HELP[spec.code]}\n"
        "- UNDETERMINED: 위 어디에도 해당하지 않아 판단 불가\n"
        '반드시 JSON 한 줄로만 답하세요: {"label": "...", "evidence": "판정 근거가 된 원문 구절 40자 이내"}\n\n'
        f"지문:\n{block.text[:_BLOCK_MAX]}"
    )


def _parse(reply: str) -> tuple[str, str] | None:
    s = reply[reply.find("{") : reply.rfind("}") + 1]
    try:
        d = json.loads(s)
    except (ValueError, TypeError):
        return None
    label = d.get("label")
    return (str(label), str(d.get("evidence") or "")) if isinstance(label, str) else None


def judge_with_llm(
    spec: TopicSpec,
    block: PolicyBlock,
    *,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    call_fn: CallFn = call_llm,
) -> Judgement:
    subject = spec.subject_of(block)
    none = Judgement(spec.code, subject, UNDETERMINED, "", "none")
    try:
        reply = call_fn([{"role": "user", "content": build_prompt(spec, block, subject)}], model=model, base_url=base_url)
    except KamLlmError:
        return none
    parsed = _parse(reply)
    if parsed is None:
        return none
    value, evidence = parsed
    if value not in spec.values or value == UNDETERMINED:
        return none
    if value in spec.positive_values:
        if not positive_pattern_matches(spec, value, evidence):
            return none
        return Judgement(spec.code, subject, value, evidence[:400], "llm_verified")
    return Judgement(spec.code, subject, value, evidence[:400], "llm")
