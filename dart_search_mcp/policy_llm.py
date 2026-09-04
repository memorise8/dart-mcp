"""규칙이 UNDETERMINED 로 남긴 블록만 소형 LLM 에 묻는다.

R18 실측: gpt-5.4-mini 는 조건문에 속지 않고(55/60 을 허용만으로), 양성 9/9 를 잡았으나
1건(영흥, "비교정보 재작성")을 조기적용으로 오판했다. 그래서 긍정값은 단정형 규칙 패턴과
evidence 가 맞을 때만 채택한다(llm_verified).
"""
from __future__ import annotations

import json
import re

from dart_search_mcp.policy_blocks import PolicyBlock
from dart_search_mcp.policy_rules import UNDETERMINED, Judgement, TopicSpec, evidence_sentence, positive_pattern_matches
from dart_search_mcp.tools.kam_tagger import DEFAULT_BASE_URL, DEFAULT_MODEL, CallFn, KamLlmError, call_llm

_WS_RE = re.compile(r"\s+")
_ANCHOR_PREFIX_LEN = 20

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
        '반드시 JSON 한 줄로만 답하세요: '
        '{"label": "...", "evidence": "판정 근거가 된 원문 구절을 원문 그대로 40자 이내로 인용"}\n\n'
        f"지문:\n{block.text[:_BLOCK_MAX]}"
    )


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """`_normalize`와 같은 규칙(연속 공백→한 칸, 양끝 strip)으로 text 를 정규화하되,
    정규화된 각 글자가 원문 text 의 어느 인덱스에서 왔는지 같이 돌려준다 — 정규화된 문자열
    안에서 찾은 위치를 원문 pos 로 되돌리기 위해서다."""
    out: list[str] = []
    idx_map: list[int] = []
    prev_was_space = True  # 처음에 공백으로 취급 — 앞쪽 공백을 건너뛴다(strip 효과)
    for i, c in enumerate(text):
        if c.isspace():
            if not prev_was_space:
                out.append(" ")
                idx_map.append(i)
            prev_was_space = True
        else:
            out.append(c)
            idx_map.append(i)
            prev_was_space = False
    while out and out[-1] == " ":
        out.pop()
        idx_map.pop()
    return "".join(out), idx_map


def _locate(text: str, evidence: str) -> tuple[int, int] | None:
    """evidence(공백 정규화)를 block.text(같은 정규화) 안에서 찾아, 원문 text 기준
    (시작 위치, 매치된 구간의 원문 길이) 를 돌려준다. 못 찾으면 evidence 앞 20자로 한 번 더
    찾는다(폴백) — 이때 길이는 그 20자에 대응하는 원문 길이일 뿐이다. 그래도 없으면 None.

    호출자는 `text[index:index+matched_len]`(quoted)로 "원문에서 실제로 확인된 구간"을 얻는다.
    폴백으로 잡혔으면 quoted 는 evidence 의 앞부분일 뿐이라 `_normalize(quoted) != _normalize
    (evidence)` — 이 차이로 호출자가 전체 인용 대 폴백을 구분한다(N1: 긍정값 검증은 폴백을
    받아들이면 안 된다 — 자세한 이유는 judge_with_llm 의 검증 지점 주석 참고)."""
    norm_evidence = _normalize(evidence)
    if not norm_evidence:
        return None
    norm_text, idx_map = _normalize_with_map(text)
    idx = norm_text.find(norm_evidence)
    match_len = len(norm_evidence)
    if idx == -1 and len(norm_evidence) > _ANCHOR_PREFIX_LEN:
        idx = norm_text.find(norm_evidence[:_ANCHOR_PREFIX_LEN])
        match_len = _ANCHOR_PREFIX_LEN
    if idx == -1:
        return None
    start = idx_map[idx]
    end = idx_map[idx + match_len - 1] + 1
    return start, end - start


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
    trace: dict[str, str] | None = None,
) -> Judgement:
    """`trace` 가 주어지면 UNDETERMINED/"none" 으로 낮춘 사유를 `trace["reason"]` 에 남긴다
    ("gateway"|"parse"|"unanchored"|"unverified") — 성공하면 "ok". `Judgement` 에는 필드를
    더하지 않는다(호출당 하나씩 쓰는 out-param 이라 스레드 안전하다). 다섯 실패 경로가 전부
    evidence=""·method="none" 인 같은 값 객체로 붕괴돼 호출자가 구분할 수 없던 문제(S-I1/Q-I1)를
    이걸로 푼다 — 예: 게이트웨이 다운(KamLlmError)과 R18 가드 거부(unverified)를 "앵커 실패"
    하나로 잘못 세면, 게이트웨이 장애를 앵커링 결함으로 오인하게 된다."""
    subject = spec.subject_of(block)
    none = Judgement(spec.code, subject, UNDETERMINED, "", "none")

    def _none(reason: str) -> Judgement:
        if trace is not None:
            trace["reason"] = reason
        return none

    try:
        reply = call_fn([{"role": "user", "content": build_prompt(spec, block, subject)}], model=model, base_url=base_url)
    except KamLlmError:
        return _none("gateway")
    parsed = _parse(reply)
    if parsed is None:
        return _none("parse")
    value, evidence = parsed
    if value not in spec.values or value == UNDETERMINED:
        return _none("parse")
    # 근거 앵커링(R2) — LLM 이 돌려준 구절이 블록 원문 어디에 있는지 찾아 문장 전체로 넓힌다.
    # 원문에서 찾을 수 없는 근거는 어휘 §1("근거 없는 행은 만들지 않는다")에 따라 채택하지 않는다.
    # _locate 탐색은 프롬프트가 실제로 실은 구간(block.text[:_BLOCK_MAX])으로 좁힌다(Q-M5) —
    # 모델이 보지 않은 2,500~3,000자 구간에서 우연히 일치해 "확인됨"으로 오채택되는 것을 막는다.
    anchor = _locate(block.text[:_BLOCK_MAX], evidence)
    if anchor is None:
        return _none("unanchored")
    index, matched_len = anchor
    # 검증(positive_pattern_matches)은 앵커된 "원문" 구절(quoted = block.text[index:index+matched_len])
    # 로 한다 — LLM 응답 문자열(evidence) 그대로가 아니다. 재리뷰 N1: quoted 를 evidence 그대로
    # 쓰면, 20자 폴백 앵커(앞 20자만 원문 일치)로 잡힌 경우 21자 이후는 LLM 이 지어내도(원문에
    # 없어도) 단정 패턴 검사를 통과할 수 있었다 — 값과 모순되는 근거가 llm_verified 로 채택되는
    # 위양성이었다. 지금은 quoted 가 항상 block.text 의 실제 부분문자열이다.
    quoted = block.text[index : index + matched_len]
    if value in spec.positive_values:
        # 긍정값은 폴백(부분 일치) 앵커를 받지 않는다 — quoted 가 evidence 전체와 정규화 기준으로
        # 같아야("완전 일치") 한다. 폴백이면 quoted 는 evidence 의 앞 20자뿐이라 그 뒤가 원문에
        # 있는지 알 수 없으므로, R18 가드(단정 패턴 검증)가 원문이 아니라 LLM 의 의역·날조를
        # 검증하게 될 위험이 있다 — 그 경우 근거 없는 행을 만들지 않는다(앵커 실패로 낮춘다).
        if _normalize(quoted) != _normalize(evidence):
            return _none("unanchored")
        if not positive_pattern_matches(spec, value, quoted):
            return _none("unverified")
        evidence = evidence_sentence(block.text, index)
        if trace is not None:
            trace["reason"] = "ok"
        return Judgement(spec.code, subject, value, evidence[:400], "llm_verified")
    evidence = evidence_sentence(block.text, index)
    if trace is not None:
        trace["reason"] = "ok"
    return Judgement(spec.code, subject, value, evidence[:400], "llm")
