"""주제 어휘와 규칙 판정. 어휘 정본은 docs/superpowers/specs/2026-09-04-policy-stance-vocabulary-design.md.

판정 순서(어휘 §3.4): 부정·부재 → 조건문 제거 → 긍정 단정 → 미적용/기타 → 허용만 → UNDETERMINED.
긍정값은 단정형 패턴으로만 나온다 — LLM 판정도 같은 패턴으로 검증한다(policy_llm.py).

T5(2026-09-08): 측정모형(T2 유형자산·T3 투자부동산)의 긍정 패턴과 자산군 어휘는
`policy_rules_measurement.py` 로 옮겼다(이 파일이 900줄을 넘었다). 여기 남은 것은 판정 순서와
그 순서를 타는 주제 표(TOPICS)다.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace

from dart_search_mcp.policy_blocks import PolicyBlock
from dart_search_mcp.policy_rules_measurement import (
    EVIDENCE_MAX as _EVIDENCE_MAX,
    T2_POSITIVE,
    T3_POSITIVE,
    asset_class_rows,
    clip_span,
    ppe_applies,
    strip_conditional_clauses,
)
from dart_search_mcp.policy_sentences import (
    KIND_COMPANY,
    KIND_CONDITIONAL,
    KIND_OTHER,
    KIND_PREAMBLE,
    KIND_STANDARD,
    PREAMBLE_ADOPTED,
    PREAMBLE_SECTION_MISMATCH,
    Sentence,
    classify_sentence,
    preamble_polarity,
    split_sentences,
)

UNDETERMINED = "UNDETERMINED"
# 판단 보류 사유(held_reason) — UNDETERMINED 행에만 채운다. 어느 단계에서 근거가 끊겼는지
# 알아야 다음 라운드에 무엇을 고칠지 정할 수 있다(지금까지는 보류 사유가 전부 공란이었다).
HELD_EVIDENCE_INSUFFICIENT = "EVIDENCE_INSUFFICIENT"
HELD_CONFLICT = "CONFLICT"
HELD_ATTRIBUTION_UNCLEAR = "ATTRIBUTION_UNCLEAR"
HELD_CONDITIONAL_ONLY = "CONDITIONAL_ONLY"
# 아래 둘은 열 어휘로는 정의돼 있으나 규칙 판정에서는 아직 나오지 않는다(소비자가 값 목록을
# 코드에서 확인할 수 있도록 이름을 남긴다). SUBJECT_UNCLEAR 는 주어를 못 가려 보류하는 경우,
# RULE_MODEL_DISAGREE 는 policy_llm 이 규칙값과 모델값을 대조하게 될 때 쓴다.
HELD_SUBJECT_UNCLEAR = "SUBJECT_UNCLEAR"
HELD_RULE_MODEL_DISAGREE = "RULE_MODEL_DISAGREE"
_SENT_SPLIT_RE = re.compile(r"(?<=다\.)\s+|(?<=니다\.)\s+|(?<=음\.)\s+")
# V6 R4: 표 조각(숫자 나열) 뒤에 항목이 이어지는 경우, "문장"의 시작이 그 표 조각까지 거슬러
# 올라간다(문장 경계가 없어서). 근거를 항목 기호부터 시작하도록 별도로 잘라낸다.
_ITEM_MARKER_RE = re.compile(r"\(\d{1,2}\)|\d{1,2}\)|\d{1,2}-\d{1,2}\.|[①-⑳]|[가-힣]\.\s|\d{1,2}\.\s(?=[가-힣])")
# 재리뷰 Minor #8: policy_blocks._HEADING_AFTER_RE(private, "_" 접두)를 모듈 간 직접 import 해
# 쓰면 그쪽 리팩터링이 조용히 이 모듈을 깰 수 있다 — 같은 조건("기호 뒤에 한글이 이어지는가")을
# 이 모듈 안에 독립적으로 정의한다(의도적 중복, policy_blocks._HEADING_AFTER_RE 와 동일 패턴).
_HEADING_AFTER_RE = re.compile(r"\s*[가-힣]")


def _last_item_marker_start(text: str, start: int, pos: int) -> int | None:
    """[start, pos) 구간에서 뒤에 한글이 이어지는(`_HEADING_AFTER_RE` 와 같은 조건) 항목 기호 중
    가장 마지막(=pos 에 가장 가까운) 것의 시작 위치. 없으면 None."""
    best: int | None = None
    for m in _ITEM_MARKER_RE.finditer(text, start, pos):
        if _HEADING_AFTER_RE.match(text, m.end()):
            best = m.start()
    return best


@dataclass(frozen=True, slots=True)
class Judgement:
    topic: str
    subject: str | None
    value: str
    evidence: str
    method: str  # "rule" | "llm_verified" | "llm" | "none" — llm* 은 policy_llm.py 가 붙인다
    # T4(2026-09-08) 근거 메타 — 근거로 고른 문장이 "누구의 어떤 문장"인지 행에 남긴다.
    # 전부 기본값이 있어 기존 생성자 호출(policy_llm 의 5인자 호출 등)은 그대로 동작한다.
    sentence_kind: str = ""  # company_statement|standard_description|conditional|list_preamble|""
    sentence_subject: str = ""  # 회사|연결실체|기준서|불명
    held_reason: str = ""  # UNDETERMINED 일 때만
    evidence_heading_path: str = ""  # "2.2.2 › (1) 기업회계기준서 제1109호 …"
    is_conditional: bool = False


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
    # 재리뷰 Important #3(I3): 일반 strip(spec.strip)은 매치 전체를 지운다 — "기간에 걸쳐 …
    # 시점에 수익을 인식" 처럼 앞쪽 구절(OVER_TIME 트리거)은 보존하고 뒤쪽 구절(POINT_IN_TIME
    # 트리거)만 지워야 하는 경우는 표현할 수 없다. 그런 "부분만 지우는" 전처리가 필요한 주제만
    # 이 훅을 쓴다(기본 None — 대부분 주제는 영향 없음).
    strip_fn: Callable[[str], str] | None = None
    # T4: 문장 단위로 근거를 고르는 주제는 자기 판정 함수를 단다(T1). 없으면 아래 `judge` 의
    # 기존 블록 단위 경로를 그대로 쓴다(T2·T3·T4 측정모형/수익인식).
    sentence_judge: Callable[["TopicSpec", PolicyBlock], "Judgement"] | None = None
    # T5: `strip_fn` 이 지운 구절이 그 블록의 유일한 근거였을 때 남길 보류 사유. T2·T3 의
    # 조건절 배제(R1)는 "조건절뿐이라 판단 못 함"(CONDITIONAL_ONLY)이 정확한 사유지만, T4 의
    # `strip_fn`(부수수익 보일러플레이트 제거)은 조건절과 무관하므로 빈 값으로 둔다.
    stripped_held_reason: str = ""


def evidence_sentence(text: str, pos: int) -> str:
    """pos 를 포함한 문장 하나. 문장 경계를 못 찾으면 pos 앞뒤 200자.

    표(숫자 나열)처럼 문장 경계가 없어 "문장"이 400자를 넘으면, 문장 전체를 그대로 자르는 대신
    pos 를 중심으로 400자 창을 잡는다 — 그래야 매치 구절(pos)이 창 밖으로 밀려나지 않는다(R1).
    전제: pos 는 비공백 글자를 가리킨다(호출자는 항상 그렇다 — 패턴 매치 시작 위치이거나
    `policy_llm._locate` 가 되돌린 비공백 인덱스). 그 전제 아래 반환값은 항상 pos 위치의 글자를
    포함한다(S-M1 — pos 가 문장 구분 공백 런 안이면 이 불변식은 보장되지 않는다)."""
    start = 0
    end = len(text)
    found_boundary = False
    for m in _SENT_SPLIT_RE.finditer(text):
        if m.end() <= pos:
            start = m.end()
        else:
            end = m.start()
            found_boundary = True
            break
    # V6 R4: 문장 시작(start)과 pos 사이에 항목 기호(표 조각 접두어 등)가 있으면, 그 마지막
    # 기호부터 근거를 시작한다 — 400자 창 규칙은 이 조정 이후에 적용한다.
    marker_start = _last_item_marker_start(text, start, pos)
    if marker_start is not None:
        start = marker_start
    sent = text[start:end]
    stripped = sent.strip()
    if len(stripped) <= _EVIDENCE_MAX:
        # 다음 경계를 찾아 잘라낸 경우(found_boundary)는 결과가 비어도 그대로 돌려준다(기존 동작
        # 그대로, S-M2) — pos 앞뒤 200자 폴백은 문장 경계를 아예 못 찾은 경우에만 쓴다.
        if found_boundary:
            return stripped
        return stripped or text[max(0, pos - 200) : pos + 200].strip()
    lo = max(start, pos - 150)
    hi = min(end, lo + _EVIDENCE_MAX)
    if hi - lo < _EVIDENCE_MAX:
        lo = max(start, hi - _EVIDENCE_MAX)
    return text[lo:hi].strip()


def _first_in_order(patterns: tuple[tuple[re.Pattern[str], str], ...], text: str) -> tuple[str, int] | None:
    """목록 순서가 우선순위 — 먼저 나열된 패턴이 어디에 있든 먼저 이긴다.

    근거용 위치는 매치의 끝(m.end() - 1)을 쓴다(컨트롤러 지시, V5+fix2 재리뷰) — 값 판정 자체는
    그대로 매치 여부만 본다. `투자부동산.{0,80}원가모형…적용`/`유형자산.{0,60}원가모형…적용` 처럼
    "." 로 임의 글자를 건너뛰는 간격 패턴은, 그 간격 안에 실제 문장 마침표가 있으면 매치 시작이
    이전 문장(예: 블록 표제)에 남는다 — evidence_sentence(text, m.start()) 가 트리거 구절이 아예
    없는 엉뚱한 문장을 뽑아버린다(실측: IP 66행 중 10행·PPE 2,924행 중 2행의 evidence 에 모형
    문구가 없었다). 매치는 트리거 구절("원가모형을 적용" 등) 자체로 끝나므로 끝 위치는 항상 그
    구절이 실제로 있는 문장 안에 있다. 짧은 패턴(간격이 없거나 좁은 경우)은 시작과 끝이 어차피
    같은 문장이라 결과가 바뀌지 않는다."""
    for pat, value in patterns:
        m = pat.search(text)
        if m:
            return (value, m.end() - 1)
    return None


def _judge_block(spec: TopicSpec, block: PolicyBlock) -> Judgement:
    if spec.sentence_judge is not None:
        return spec.sentence_judge(spec, block)
    subject = spec.subject_of(block)
    text = block.text
    hit = _first_in_order(spec.negative, text)
    pre_strip_fn: str | None = None
    if hit is None:
        stripped = text
        for pat in spec.strip:
            # 길이 보존 치환(Q-I2) — 지운 구절 자리에 같은 길이의 공백을 넣어 오프셋이 block.text
            # 와 정확히 같게 만든다. 보통 sub(" ", …)로 한 칸만 남기면 지워진 구절 뒤 위치들이
            # block.text 와 어긋나, 그 뒤에서 잡힌 pos 로 뽑은 근거가 원문에 없는 문자열이 된다
            # (예: T1_STRIP "조기적용하기로 선택하는 경우…" 가 문장 중간에 있는 실제 보일러플레이트).
            # N2(재리뷰, 보류): 이 방식은 지워진 구절만큼 간격을 넓혀 `유형자산.{0,60}…`/
            # `투자부동산.{0,80}…` 처럼 상한이 있는 긍정 패턴의 유효 도달범위를 줄인다(실측 A/B —
            # 3,929 문서·16,876 행 중 값이 바뀐 행 0건이라 컨트롤러가 이번 라운드에서는 보류하기로
            # 했다. 회귀 테스트도 이번 라운드 범위 밖).
            stripped = pat.sub(lambda m: " " * len(m.group(0)), stripped)
        if spec.strip_fn is not None:
            pre_strip_fn = stripped
            stripped = spec.strip_fn(stripped)
        for group in (spec.positive, spec.fallback):
            hit = _first_in_order(group, stripped)
            if hit is not None:
                text = stripped
                break
    if hit is None:
        # T5: strip_fn 이 지운 구절이 이 블록의 유일한 근거였는가 — 그렇다면 "근거가 아예
        # 없다"가 아니라 "조건절뿐이라 판단할 수 없다"가 정확한 보류 사유다(R1).
        held = ""
        if (
            spec.stripped_held_reason
            and pre_strip_fn is not None
            and _first_in_order(spec.positive, pre_strip_fn) is not None
        ):
            held = spec.stripped_held_reason
        return Judgement(
            spec.code, subject, UNDETERMINED, "", "none",
            held_reason=held, evidence_heading_path=_heading_path(block) if held else "",
        )
    value, pos = hit
    # 복수 긍정값 승격: 긍정 패턴이 둘 이상 단정되면 조합값으로(수익인식 BOTH, 유형자산 MIXED)
    if spec.combo_value:
        found = {v for pat, v in spec.positive if pat.search(text)}
        if len(found) >= 2:
            value = spec.combo_value
    # 근거는 항상 block.text(원문)에서 뽑는다 — stripped 텍스트는 매치 위치(pos)를 찾는 데만 쓰고,
    # 길이 보존 치환 덕에 그 pos 는 block.text 에서도 같은 글자를 가리킨다. 그래서 evidence 는
    # 항상 원문 축자(verbatim)다(Q-I2) — 그렇지 않으면 감사(policy_stance_audit)의
    # evidence_in_block 이 실데이터에서 상시 위반으로 잡힌다.
    evidence = evidence_sentence(block.text, pos)
    if spec.code == "REVENUE_METHOD":
        value, evidence = _revenue_finish(spec, block, text, value, pos, evidence)
    return Judgement(spec.code, subject, value, evidence, "rule")


def judge_all(spec: TopicSpec, block: PolicyBlock) -> list[Judgement]:
    """블록 하나가 낼 수 있는 모든 행. 첫 행은 언제나 회사 수준 행(subject 는 spec.subject_of).

    유형자산만 자산군별 행을 덧붙인다 — 「토지와 건물은 …공정가치로 측정」/「토지와 건물을
    제외한 유형자산을 역사적 원가에서 …」처럼 한 블록이 자산군마다 다른 모형을 말하는 것이
    유형자산의 표준 서술이고(회계사 확인 P4·P5: 온타이드·라닉스), 회사 수준 값 하나로는
    "토지는 재평가, 나머지는 원가"를 표현할 수 없기 때문이다.

    값이 다른 자산군 행이 둘 이상이면 회사 수준 행을 MIXED 로 맞춘다 — 1단계 화면은 아직
    subject 없는 행 하나만 읽으므로(v1 어휘는 MIXED 저장을 금지하지만 이번 단계는 유지),
    그 행이 "자산군마다 다르다"는 사실을 담고 있어야 한다."""
    base = _judge_block(spec, block)
    if spec.code != "PPE_MEASUREMENT":
        return [base]
    rows = asset_class_rows(block)
    if not rows:
        return [base]
    if len({value for _subject, value, _ev in rows}) >= 2 and base.value != "MIXED":
        base = replace(
            base, value="MIXED", method="rule", held_reason="",
            evidence=base.evidence or rows[0][2],
            evidence_heading_path=base.evidence_heading_path or _heading_path(block),
        )
    return [base] + [
        Judgement(
            spec.code, subject, value, evidence, "rule",
            evidence_heading_path=_heading_path(block),
        )
        for subject, value, evidence in rows
    ]


def judge(spec: TopicSpec, block: PolicyBlock) -> Judgement:
    """블록의 대표 행 하나 — `judge_all` 의 첫 행(회사 수준 행)이다."""
    return judge_all(spec, block)[0]


def positive_pattern_matches(spec: TopicSpec, value: str, evidence: str) -> bool:
    if value not in spec.positive_values:
        return False
    return any(v == value and pat.search(evidence) for pat, v in spec.positive)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# ---- T1 기준서 조기적용 -------------------------------------------------------
# 값 어휘: EARLY_ADOPTED(조기적용 단정) · ADOPTED(당기 신규 적용 목록에 있음, 조기 여부는
# 미판정) · NOT_ADOPTED(회사가 아직 적용하지 않았음) · PERMITTED_ONLY(기준서 설명·시행일·
# 조기적용 허용만 있어 회사 자신의 적용 여부를 읽을 수 없음) · UNDETERMINED.
_T1_NEG = (
    (_p(r"조기\s*(?:적용|도입)\s*하지\s*(?:않|아니)"), "NOT_ADOPTED"),
    (_p(r"조기\s*(?:적용|도입)(?:하기로\s*한|한)\s*(?:기준서|사항|해석서)[^.]{0,40}없"), "NOT_ADOPTED"),
)
_T1_STRIP = (_p(r"조기\s*(?:적용|도입)\s*하기로\s*선택하는\s*경우[^.]*\."),)
_T1_EARLY = ((_p(r"조기\s*(?:적용|도입)\s*(?:하였|했|하고\s*있|하기로\s*(?:결정|하였|했))"), "EARLY_ADOPTED"),)
# 당기 신규 적용 목록의 서문(2.2.1 류). 값 ADOPTED 의 근거 패턴이다 — 실제 판정은 블록에
# 붙은 lead 로 하지만(아래 `_judge_std_early`), `positive_pattern_matches`(policy_llm 의 긍정값
# 검증)와 policy_stance_audit 의 pattern_in_evidence 가 이 패턴으로 근거를 되짚는다.
_T1_ADOPTION_PREAMBLE = _p(
    r"(?:신규로|새로이|새로)\s*적용(?:하였|하고|한)|적용한\s*제\s*ㆍ?\s*개정|채택한\s*제\s*ㆍ?\s*개정"
    r"|당기(?:부터)?\s*(?:새로|신규로)\s*적용"
)
_T1_POS = (*_T1_EARLY, (_T1_ADOPTION_PREAMBLE, "ADOPTED"))

# (A) 회사·영향 진술 — 회사가 아직 적용하지 않았음을 뒷받침하는 문장들.
# T4: 「영향을 (검토|평가|분석) 하고 있」 계열을 더한다. 기존 목록은 "…중"만 잡아서, 실측에서
# 가장 흔한 "…영향을 평가하고 있으며"(삼성전자 20260310002820·에스텍 20260316001663)를
# 통째로 놓쳐 판단 보류로 남겼다.
_T1_COMPANY_STANCE = (
    (_p(
        r"(?:채택|적용|도입)하지\s*(?:않|아니)|아직\s*채택|(?:검토|평가|파\s*악)\s*중"
        r"|영향을\s*(?:검토|평가|분석)\s*(?:중|하고\s*있)|적용에\s*따른\s*영향을\s*(?:검토|평가)"
        r"|영향(?:은|이)?\s*(?:중요하지|없)"
        r"|영향을\s*미치지\s*않을\s*것으로\s*예상|영향이\s*없을\s*것으로\s*예상"
        r"|최초\s*적용\s*시|적용하여\s*재무제표를\s*작성하는\s*경우"
        r"|재작성(?:됩니다|될|되며)|차이를\s*발생시킬\s*것으로\s*예상|영향(?:은|이)?\s*광범위할\s*것으로\s*예상"
    ), "NOT_ADOPTED"),
)
# (B) 시행일 서술 — "언제부터 적용된다"는 기준서 쪽 사실일 뿐이라 회사의 미적용을 입증하지
# 못한다(v1 설계 §2.1). T4 에서 NOT_ADOPTED → PERMITTED_ONLY 로 옮겼다. 다만 이 서술이 회사가
# 주어인 문장 안에 있으면("연결회사는 … 2027년부터 적용될 예정입니다") 회사 진술이므로
# NOT_ADOPTED 로 승격한다(`_EFFECTIVE_DATE_IN_COMPANY_VALUE`).
# Fix round 1(minor): "적용할 예정"도 여기 있다 — 예전에는 (A) 에 있어 주어와 무관하게 언제나
# NOT_ADOPTED 였는데, 쌍둥이 표면형 "적용될 예정"(부터…적용될 로 여기서 잡힌다)과 갈렸다.
# 이제 둘 다 주어가 기준서면 PERMITTED_ONLY, 회사면 NOT_ADOPTED 로 같게 움직인다.
_T1_EFFECTIVE_DATE = (
    (_p(
        r"부터\s*(?:적용|시행)(?:됩니다|되며|될|합니다)"
        r"|(?:회계연도|보고기간)(?:에|부터)\s*적용(?:해야|하여야|됩니다|합니다|되며|될)"
        r"|(?:의무|소급)\s*적용(?:되며|됩니다|될|됨)"
        r"|부터\s*(?:의무|소급)?\s*적용(?:되며|되는|되고|됩니다|될|됨|하며|할)"
        r"|적용하되|시행될\s*예정|적용할\s*예정"
    ), "PERMITTED_ONLY"),
)
# (C) 조기적용 허용·가능 — 그 자체로는 언제나 PERMITTED_ONLY 다(회사 문장 안에 있어도
# "허용된다"는 기준서 쪽 사실이라 승격하지 않는다).
_T1_PERMISSION = ((_p(r"조기\s*(?:적용|도입)이?\s*(?:허용|가능)"), "PERMITTED_ONLY"),)
_T1_FALLBACK = (*_T1_COMPANY_STANCE, *_T1_EFFECTIVE_DATE, *_T1_PERMISSION)
_EFFECTIVE_DATE_IN_COMPANY_VALUE = "NOT_ADOPTED"

# 근거 문장 선택 순위 — 회계사 확인(P1·P2)의 핵심: 같은 값을 내는 문장이 여럿이면 회사가
# 주어인 문장을 먼저 고른다. 기준서 설명문은 조건절 바로 위(=가장 나중)다.
_KIND_RANK = {KIND_COMPANY: 0, KIND_OTHER: 1, KIND_PREAMBLE: 1, KIND_STANDARD: 2, KIND_CONDITIONAL: 3}


@dataclass(frozen=True, slots=True)
class _Hit:
    """패턴 하나가 문장 하나에서 맞은 자리."""

    value: str
    order: int  # 패턴 목록 순서(작을수록 우선)
    pos: int
    sentence: Sentence

    @property
    def sort_key(self) -> tuple[int, int, int]:
        return (_KIND_RANK.get(self.sentence.kind, 1), self.order, self.pos)


def _scan(
    rules: tuple[tuple[re.Pattern[str], str], ...], stripped: str, sentences: list[Sentence]
) -> list[_Hit]:
    """문장별로 규칙을 맞춰 본다 — 매치 위치가 아니라 매치가 **어느 문장에 있는지**가 중요하다.

    탐색은 strip 을 거친 텍스트(`stripped`)에서 하고 문장 경계는 원문 기준이다. strip 은 길이
    보존 치환이라 두 좌표계가 정확히 겹친다(Q-I2)."""
    hits: list[_Hit] = []
    for order, (pat, value) in enumerate(rules):
        for sent in sentences:
            m = pat.search(stripped, sent.start, sent.end)
            if m is not None:
                hits.append(_Hit(value, order, m.start(), sent))
    return hits


def _best(hits: list[_Hit]) -> _Hit | None:
    return min(hits, key=lambda h: h.sort_key) if hits else None


def _clip(raw: str, hit: _Hit) -> str:
    """근거 문자열 — 매치가 든 문장 하나.

    `evidence_sentence` 와 같은 두 가지 보정을 그대로 적용한다. (V6 R4) 문장 시작과 매치
    사이에 항목 기호가 있으면(표 조각·글머리 나열 뒤에 항목이 이어지는 모양) 그 마지막
    기호부터 시작한다 — 안 그러면 근거가 앞 표의 숫자 나열부터 시작한다. (R1) 그러고도
    상한을 넘으면 매치 자리를 중심으로 창을 잡아 매치 구절이 창 밖으로 밀려나지 않게 한다."""
    start = hit.sentence.start
    marker = _last_item_marker_start(raw, start, hit.pos)
    if marker is not None:
        start = marker
    return clip_span(raw, start, hit.sentence.end, hit.pos)


def _heading_path(block: PolicyBlock) -> str:
    head = re.sub(r"\s+", " ", block.heading).strip()[:60]
    return f"{block.section} › {head}" if block.section else head


def _held(spec: TopicSpec, block: PolicyBlock, reason: str) -> Judgement:
    return Judgement(
        spec.code, spec.subject_of(block), UNDETERMINED, "", "none",
        held_reason=reason, evidence_heading_path=_heading_path(block),
    )


def _decide(spec: TopicSpec, block: PolicyBlock, raw: str, hit: _Hit, value: str) -> Judgement:
    return Judgement(
        spec.code, spec.subject_of(block), value, _clip(raw, hit), "rule",
        sentence_kind=hit.sentence.kind,
        sentence_subject=hit.sentence.subject,
        evidence_heading_path=_heading_path(block),
        is_conditional=hit.sentence.kind == KIND_CONDITIONAL,
    )


def _judge_std_early(spec: TopicSpec, block: PolicyBlock) -> Judgement:
    """T1 판정 — 문장 단위로 근거를 고르고, 항목이 침묵하면 그 항목이 속한 목록의 서문에 묻는다.

    순서: (조기적용 부정 ↔ 단정 충돌) → 부정 → 단정(조기적용) → 회사의 미적용 진술 →
    시행일·허용 서술(PERMITTED_ONLY) → 목록 서문 귀속(ADOPTED/NOT_ADOPTED) → 보류.

    극성 있는 목록 서문("… 조기 적용하지 아니한 기준서는 다음과 같습니다")은 **뒤 항목**의
    것이므로 이 블록의 근거 후보에서 뺀다 — 블록이 다음 절까지 흘러들어 그 서문을 삼켜도
    앞 항목이 미적용으로 뒤집히지 않는다(올래디오 20260327000721 제1117호)."""
    raw = block.text
    sentences = split_sentences(raw)
    usable = [s for s in sentences if not preamble_polarity(s.text)]
    dropped_preamble = len(usable) != len(sentences)
    stripped = raw
    for pat in spec.strip:
        stripped = pat.sub(lambda m: " " * len(m.group(0)), stripped)

    neg = _best(_scan(_T1_NEG, stripped, usable))
    early_hits = _scan(_T1_EARLY, stripped, usable)
    # 조건절 취급이 비대칭인 것은 의도다. 조기적용 **단정**(EARLY_ADOPTED)만 조건절 문장을
    # 버린다 — "조기적용하기로 선택하는 경우 …" 류가 회사가 실제로 조기적용했다는 진술로
    # 둔갑하면 긍정 오탐(R18 이 지적한 그 실패)이 되기 때문이다. 부정(_T1_NEG)과 회사의 미적용
    # 진술(_T1_COMPANY_STANCE)은 조건절 안에 있어도 값이 "아직 적용하지 않았다" 쪽이라 같은
    # 위험이 없고, 오히려 버리면 판단 보류만 늘어난다 — 대신 순위에서 가장 뒤로 밀고
    # `is_conditional` 로 표시해 소비자가 걸러낼 수 있게 한다.
    early_asserted = [h for h in early_hits if h.sentence.kind != KIND_CONDITIONAL]
    early = _best(early_asserted)
    if neg is not None and early is not None and neg.sentence.start == early.sentence.start:
        # 한 문장이 조기적용을 단정하면서 동시에 부정한다 — 규칙으로는 가릴 수 없다.
        return _held(spec, block, HELD_CONFLICT)
    if neg is not None:
        return _decide(spec, block, raw, neg, neg.value)
    if early is not None:
        return _decide(spec, block, raw, early, early.value)

    effective = _scan(_T1_EFFECTIVE_DATE, stripped, usable)
    # 시행일 서술이 회사 문장 안에 있으면 회사의 미적용 진술로 본다(브리프 §T1 근거 선택 순서).
    stance = _scan(_T1_COMPANY_STANCE, stripped, usable) + [
        _Hit(_EFFECTIVE_DATE_IN_COMPANY_VALUE, len(_T1_COMPANY_STANCE) + h.order, h.pos, h.sentence)
        for h in effective
        if h.sentence.kind == KIND_COMPANY
    ]
    best_stance = _best(stance)
    if best_stance is not None:
        return _decide(spec, block, raw, best_stance, "NOT_ADOPTED")

    permitted = [h for h in effective if h.sentence.kind != KIND_COMPANY] + _scan(
        _T1_PERMISSION, stripped, usable
    )
    best_permitted = _best(permitted)
    if best_permitted is not None:
        return _decide(spec, block, raw, best_permitted, "PERMITTED_ONLY")

    if block.lead_polarity == PREAMBLE_SECTION_MISMATCH:
        # 가까운 서문이 있긴 한데 다른 절의 것이다 — 이 항목이 어느 목록에 실렸는지 확정할 수 없다.
        return _held(spec, block, HELD_ATTRIBUTION_UNCLEAR)
    if block.lead_polarity:
        # 항목 자체는 아무 말도 하지 않는다 — 이 항목이 실린 목록의 서문이 값을 정한다.
        kind, subject = classify_sentence(block.lead)
        return Judgement(
            spec.code, spec.subject_of(block),
            "ADOPTED" if block.lead_polarity == PREAMBLE_ADOPTED else "NOT_ADOPTED",
            block.lead[:_EVIDENCE_MAX], "rule",
            sentence_kind=KIND_PREAMBLE, sentence_subject=subject,
            evidence_heading_path=_heading_path(block),
            is_conditional=kind == KIND_CONDITIONAL,
        )
    if early_hits:
        return _held(spec, block, HELD_CONDITIONAL_ONLY)
    if dropped_preamble:
        # 이 블록에서 값이 될 만한 문장은 뒤 항목의 서문뿐이었다 — 귀속을 확정할 수 없다.
        return _held(spec, block, HELD_ATTRIBUTION_UNCLEAR)
    return _held(spec, block, HELD_EVIDENCE_INSUFFICIENT)


# ---- T4 수익인식 방법 ---------------------------------------------------------
# 평서체("~한다.")로 끝나는 문장은 기준서 인용·정의문으로 본다 — 회사 진술문은 합쇼체("~합니다.")다.
_T4_STRIP = (
    _p(r"[^.]*(?:한다|된다|있다|없다)\."),
    # 재리뷰 Important #3(a)(I3): "시점에 수익을 인식" 은 V6 R3 에서 추가됐는데 부정 가드가
    # 없다 — "…시점에 수익을 인식하지 않습니다"(부정)도 그대로 매치돼 OVER_TIME 단독 회사를
    # BOTH 로 잘못 승격시킨다(합성 재현: "공사수익은 진행률에 따라 기간에 걸쳐 인식합니다.
    # 통제가 이전되지 않는 시점에 수익을 인식하지 않습니다." → 정답 OVER_TIME). 부정형 구절만
    # 지운다.
    _p(r"시점에\s*수익(?:을|으로)\s*인식하지\s*(?:않|아니)"),
    # V8: "진행기준을 적용하지 않고 …시점(완성기준)에 수익을 인식합니다." 처럼 부정형
    # "진행기준을 적용하지 않"도 "진행…적용" 이 리터럴로 들어 있어 OVER_TIME 이 그대로
    # 발화했다(뒤의 시점기준 진술과 합쳐져 BOTH 로 오판). 부정형 구절만 지운다.
    _p(r"진행기준(?:을|를)?\s*적용하지\s*(?:않|아니)"),
    # V8: 5단계 수익인식모형 정의문의 일반 어구("…수행의무를 이행할 때 한 시점 또는 기간에
    # 걸쳐 수익을 인식합니다")는 시점기준·진행기준 둘 다를 포괄하는 일반 서술이지 회사의
    # 확정된 선택이 아니다 — "수행의무를 이행(할|하는) 때"(POINT_IN_TIME 신규 트리거) 뒤에
    # 바로 이 일반 어구가 이어지면 앞의 트리거 구절까지 함께 지운다(그렇지 않고 이 어구가
    # 단독으로 나오는 경우도 여전히 지운다 — 앞 구절은 선택적).
    _p(r"(?:수행의무를\s*이행(?:할|하는)\s*때\s*)?한\s*시점\s*또는\s*기간에\s*걸쳐"),
    # V10(리뷰 Critical #1): 실데이터 48,373건 전수 스캔에서 "수행의무를 이행(할|하는) 때"
    # POINT_IN_TIME 트리거가 514개 블록에서 일반/인용문 보일러플레이트만으로 발화했다 —
    # 그 트리거 자체를 제거한다(아래 _T4_POS). 두 보일러플레이트 표면형을 strip 으로 막아
    # 다른 트리거에도 새지 않게 한다. (1) "…수행의무를 이행할 때 수익을 인식할지 또는
    # 수행의무를 이행하는 기간에 걸쳐 수익을 인식할지 여부를 판단하고 있습니다" — 시점기준·
    # 진행기준 중 어느 쪽인지 계약별로 "판단한다"는 서술일 뿐 확정된 선택이 아닌데, 안의
    # "기간에 걸쳐 …인식"이 OVER_TIME 트리거로 리터럴 매치돼 BOTH 로 오판됐다.
    _p(r"수익을\s*인식할지[^.]{0,80}?판단하고\s*있습니다"),
    # (2) "수행의무를 이행할 때 또는 (기간에 걸쳐) 이행하는 대로/데로 수익을 인식(합니다|하고
    # 있습니다)" — 기준서 인용·정의문(1115호가 규정하는 두 방식을 그대로 나열)이지 회사의
    # 확정된 선택이 아니다.
    _p(r"수행의무를\s*이행(?:할|하는)\s*때[^.]{0,40}?이행하는\s*(?:대로|데로)"),
)
# 재리뷰 N2: 앵커가 맨 문자열 "기간에 걸쳐" 였는데, 코퍼스에 흔한 "리스기간에 걸쳐"·
# "보증기간에 걸쳐"·"상각기간에 걸쳐"·"내용연수기간에 걸쳐" 처럼 OVER_TIME 트리거가 아닌
# 복합어에서도 발화해, 같은 문장(마침표 없는 80자 이내)의 진짜 시점기준 진술을 지웠다.
# `_T4_POS` 의 실제 OVER_TIME 트리거("기간에 걸쳐" 뒤에 수익·수행의무·인식·이행이 이어지는
# 형태)로 앵커를 좁힌다.
# 재리뷰 X3(Minor): N2 가 앵커를 좁히면서 동시에 `진행률|진행기준|투입법` 을 **넓혀** 새 오탐
# 경로를 만들었다 — "공사수익은 진행률에 따라 인식하며, 상품매출은 인도되는 시점에 수익을
# 인식합니다." 처럼 한 문장 안에 진행기준 서술과 진짜 시점기준 서술이 함께 오면 뒤쪽이
# 지워져 BOTH 가 OVER_TIME 으로 강등됐다. N2(b)(group2 가 "시점" 토큰까지 가져가 다른 POINT
# 대안을 무력화하는 문제)를 닫기 전까지는 "기간에 걸쳐…" 형태만 남기고 `진행률|진행기준|
# 투입법` 은 뺀다.
# T5(회계사 확인 P5, HC보광산업 20260323001053): 이 blanket strip 은 X3 가 좁힌 뒤에도 교과서적
# 병행 진술을 지웠다 — 「용역수익은 …기간에 걸쳐 수익을 인식하고 있으며, 상품매출은 …통제가
# 고객에게 이전되는 시점에 수익을 인식하고 있습니다」는 두 수익 흐름을 **각각 다른 주어로**
# 나눠 말하는 문장이라 뒤 구절이 앞 구절의 부연이 아니다. 두 구절 사이에 새 수익 주어
# (「…매출은」「…수익은」「…판매는」)가 끼면 지우지 않는다 — 부수수익 정형구(배당·이자·로열티)는
# 아래 `_INCOME_BOILERPLATE_POINT_RE` 가 주어를 직접 지목해 따로 막으므로 구멍이 나지 않는다.
_NEW_REVENUE_SUBJECT = r"(?:매출|수익|판매)(?:은|는)\s"
_POINT_AFTER_OVER_TIME_RE = re.compile(
    r"(기간에\s*걸쳐\s*(?:수익|수행의무|인식|이행)"
    rf"(?:(?!{_NEW_REVENUE_SUBJECT})[^.]){{0,80}}?)"
    r"(시점에\s*수익(?:을|으로)\s*인식)"
)
# Fix round 2(리뷰 I3(a) 실측, 표본 A OVER_TIME→BOTH 9건 중 4건): 부수수익(K-IFRS 1115 수익이
# 아니라 기타수익인 배당·이자·로열티 수익) 보일러플레이트가 "…확정되는 시점에 수익을 인식하고
# 있습니다"로 끝나면, 그 자체가 회사의 (진행기준과 대비되는) 시점기준 수익인식 방침 선언이
# 아니다 — 진행기준으로 공사수익을 인식하는 회사가 부수수익 문장 하나 때문에 BOTH 로 잘못
# 라벨링됐다.
# 재리뷰 F1: 첫 버전은 두 가지가 겹쳐 있었다. (1) 키워드 목록이 "기타수익"과 "1115 수익
# 항목명"을 섞었다 — 임대수익·수수료수익은 부동산·리스·중개·금융 회사에서는 주된 1115
# 수익이지 기타수익이 아니다(실측 20260430000055: 대리인 회계의 정석적 시점기준 진술
# "…수수료수익으로 인식하고 있으며, 상품이 …배송완료된 시점에 수익으로 인식"이 지워져
# POINT_IN_TIME 을 UNDETERMINED 로 잃었다). 임대수익·수수료수익을 목록에서 뺀다.
# (2) 근접만 봐서 문장의 주어 관계를 확인하지 않았다 — 키워드가 문장 시작(또는 앞 문장 경계
# 직후)에서 조사(은|는|의|에 대한)와 함께 나올 때만(=그 절의 진짜 주어일 때만) 인정한다.
# 재리뷰 X1(Critical): 위 문장-시작 앵커(`^`/마침표 직후)가 실데이터에서 가드를 통째로
# 무력화했다 — 코퍼스의 배당·이자 정형구는 거의 언제나 "…이자수익은 …하고, 배당수익은
# …합니다." 처럼 **쉼표로 이어지는 등위절**이라 앞이 마침표가 아니다(2,000 보고서 표본에서
# 가드 발화가 63건 → 0건으로 붕괴, round 2 가 닫았던 배당 정형구 8건이 되돌아왔다 — 실측
# 20260401001583·20260403000040·20260303000854). 목표(F1 의 20260430000055 복구)는 키워드
# 목록 축소(임대·수수료 제거)만으로 이미 달성되므로, 문장-시작 앵커를 완전히 제거한다.
_INCOME_BOILERPLATE_POINT_RE = re.compile(
    r"((?:배당\s*수익|배당금|이자\s*수익|로열티\s*수익)(?:은|는|의|에\s*대한)[^.]{0,60}?)"
    r"(시점에\s*수익(?:을|으로)\s*인식)"
)


def _strip_point_after_over_time(text: str) -> str:
    """재리뷰 Important #3(b): "기간에 걸쳐 … 시점에 수익을 인식" 처럼 OVER_TIME 트리거
    ("기간에 걸쳐")가 먼저 나오고 같은 문장 안에서 뒤이어 "시점에 수익을 인식" 이 나오면, 그
    POINT_IN_TIME 구절만 지운다(OVER_TIME 트리거는 보존) — 실측(표본 A `OVER_TIME → BOTH`
    9건 중 4건)은 동일 부수수익(배당수익) 보일러플레이트가 "…확정되는 시점에 수익을 인식"
    으로 끝나 OVER_TIME 단독 회사를 잘못 BOTH 로 승격시켰다. 일반 spec.strip(매치 전체를
    지움)으로는 "기간에 걸쳐"(앞쪽, 보존해야 함)와 "시점에 수익을 인식"(뒤쪽, 지워야 함)을
    분리할 수 없어 별도 전처리 함수로 둔다 — 길이 보존(Q-I2, group 2 만 같은 길이 공백으로).

    Fix round 2: "기간에 걸쳐"가 근처에 아예 없어도(OVER_TIME 트리거가 다른 문장에 있거나
    없어도) 부수수익 보일러플레이트 자체가 "시점에 수익을 인식" 구절을 무의미하게 만든다 —
    같은 문장·60자 이내에 배당/이자/로열티/임대/수수료 수익 키워드가 먼저 나오면 그 뒤의
    "시점에 수익을 인식" 구절만 지운다. "인도 시점"·"통제가 … 이전되는 시점" 계열 구 패턴은
    이번 라운드에서 실데이터로 부수수익 오탐을 재현하지 못해 그대로 둔다(브리프 지시).

    재리뷰 N2: `_POINT_AFTER_OVER_TIME_RE` 의 group1 앵커가 맨 문자열 "기간에 걸쳐" 였다 —
    "리스기간에 걸쳐"·"보증기간에 걸쳐"·"상각기간에 걸쳐" 처럼 OVER_TIME 트리거가 아닌
    복합어에서도 발화해 같은 문장의 진짜 시점기준 진술을 지웠다(합성 재현, 실데이터 0건).
    앵커를 `_T4_POS` 의 실제 OVER_TIME 트리거로 좁힌다. (N2(b) — group2 가 "시점" 토큰을
    같이 지워 "인도(된|되는|한) 시점"·"통제가 … 이전되는 시점" 같은 다른 POINT 대안까지
    무력화하는 문제는 이번 라운드 지시 범위 밖이라 손대지 않았다 — v6-report.md 에 기록.)"""
    text = _POINT_AFTER_OVER_TIME_RE.sub(lambda m: m.group(1) + " " * len(m.group(2)), text)
    text = _INCOME_BOILERPLATE_POINT_RE.sub(lambda m: m.group(1) + " " * len(m.group(2)), text)
    return text


_T4_POS = (
    (_p(
        # 재리뷰 Y1(Important): "의해"는 있는데 "의하여"가 없어, 코퍼스에 흔한 "진행기준에
        # 의하여 수익을 인식하고 있습니다"가 매치되지 않았다 — 시점기준·진행기준을 병기하는
        # 회사가 POINT_IN_TIME 단독으로 잘못 라벨링됐다(2,000 보고서 표본 12건, 실측
        # 20260219000703 등). 어미를 넓힌다.
        r"진행(?:기준|률)(?:에|을|로)?\s*(?:따라|의해|의하여|적용|기준으로)|투입법|기간에\s*걸쳐\s*(?:수익을\s*)?인식"
        r"|제공하는\s*기간에\s*걸쳐|수행의무를\s*이행하는\s*기간|계약\s*기간에\s*걸쳐"
        # V6 R3: "진행률을 곱하여"는 위 "진행률에 따라/의해/적용/기준으로" 목록에 없던 동사다.
        r"|진행률(?:을|에)\s*(?:곱하여|따라|기초로)"
    ), "OVER_TIME"),
    (_p(
        r"인도(?:시점|기준)|통제(?:가|를)\s*이전(?:하는|되는)\s*시점에\s*(?:수익을\s*)?인식"
        # 재리뷰 Minor #4: 부정 가드 없이는 "위험과 보상이 …이전되지 않"(리스 분류·수익 미인식
        # 서술)도 매치돼 OVER_TIME 단독 회사를 BOTH 로 잘못 승격시켰다(실측 1건). 부정만 배제.
        r"|위험과\s*보상이[^.]{0,40}이전(?!되지\s*(?:않|아니))|인도(?:된|되는|한)\s*시점|(?:용역의\s*)?제공\s*시점"
        # V6 R3: "통제가 고객에게 이전되는 시점"처럼 "통제가"와 "이전" 사이에 "고객에게"가 끼는
        # 변형, "인도/이전 시점에 수익(을|으로) 인식", 통제·인도 언급 없이 "시점에 수익을 인식"
        # 만 있는 변형도 시점기준(POINT_IN_TIME) 트리거다.
        r"|통제가\s*(?:고객에게\s*)?이전되는\s*시점|(?:인도|이전)\s*시점에\s*수익(?:을|으로)?\s*인식"
        r"|시점에\s*수익(?:을|으로)\s*인식"
        # V8: K-GAAP 식 재화 문형("위험과 효익의 대부분이 구매자에게 이전된 때") 과 K-IFRS
        # 1115 식 표면형("고객이 인수한 때", "제품이 고객에게 이전되어") — 전부 시점기준
        # 트리거인데 기존 목록에 없었다. ("수행의무를 이행할 때"는 V10 에서 제거 — 아래 참고.)
        r"|위험과\s*(?:보상|효익)(?:이|의)?[^.]{0,40}?(?:구매자|고객)에게\s*이전(?:된|되어|한|되는)\s*(?:때|경우|시점)"
        r"|고객이\s*인수한\s*때|제품이\s*고객에게\s*이전되어"
        # V10(리뷰 Critical #1): "수행의무를 이행(할|하는) 때"는 K-IFRS 1115 의 "두 방식
        # 중 하나" 정의문 자체에 그대로 들어 있는 표현이라(예: "수행의무를 이행할 때 또는
        # 이행하는 대로 수익을 인식한다"), 실데이터 48,373건 전수 스캔에서 514개 REVENUE
        # 블록이 (1) 계약별 판단 서술("…인식할지 …여부를 판단하고 있습니다"), (2) 거래가격
        # 배분 조항, (3) 기준서 인용문, (4) 5단계 수익인식모형 전문(前文) 등 회사의 확정된
        # 선택이 아닌 보일러플레이트만으로 발화했다 — 진짜 실측(예: "…수행의무를 이행할 때
        # 인식하고 있으며, 한 시점에 이행하는 수행의무는 …통제를 이전하는 시점에 이행되고
        # 있습니다")은 위 다른 트리거들(시점에 수익을 인식 등)이 이미 잡는다. 트리거를
        # 완전히 없앤다(strip 은 여전히 이 표면형을 대비해 남겨 둔다 — 위 _T4_STRIP).
        # V9(3차 판독, 잔여 OVER_TIME 오류 8건 중 6건이 같은 문형): 위 변형은 "이전" 뒤에
        # 곧바로 때/경우/시점 어미를 요구하는데, 실제로 가장 흔한 표면형은 "…구매자에게
        # 이전되고, 판매한 재화에 대하여 …인식하고 있습니다."처럼 이전 뒤에 다른 인식조건
        # 나열이 이어지고 어미 없이 문장이 끝난다. 어미 요구를 없앤다 — 평서체 정의문("…
        # 이전된다.")은 기존 _T4_STRIP(`[^.]*(?:한다|된다|있다|없다)\.`)이 여전히 지운다.
        # V10(리뷰 Critical #2): 어미 요구를 없애면서, 형제 패턴(`위험과\s*보상이…이전
        # (?!되지…)`, 재리뷰 Minor #4)이 이미 막았던 부정 가드까지 같이 없어졌다 —
        # "위험과 효익의 대부분이 구매자에게 이전되지 않는 경우에는 수익을 인식하지
        # 않습니다"(수익 미인식 서술, 위탁판매 등)가 그대로 매치돼 OVER_TIME 단독 회사를
        # BOTH 로, 부정문 단독을 POINT_IN_TIME 으로 정반대로 오판했다. 형제 패턴과 같은
        # 부정 가드를 붙인다.
        r"|위험과\s*(?:보상|효익)(?:이|의)?[^.]{0,40}?(?:구매자|고객)에게\s*이전(?!되지\s*(?:않|아니))"
        # V8: "진행기준을 적용하지 않고 …완료된 시점(완성기준)에 수익을 인식합니다." 처럼
        # "시점"과 "에" 사이에 괄호 안 방식 이름(완성기준 등)이 끼는 변형 — 위 부정 가드
        # strip 과 짝을 이룬다.
        r"|시점\s*\([^)]{0,20}\)\s*에\s*수익(?:을|으로)\s*인식"
    ), "POINT_IN_TIME"),
)

# T5: 「일부(의) 수익·매출은 진행기준을 …」 — 진행기준을 쓰는 수익이 **일부**라고 명시하면
# 나머지 수익은 시점기준이라는 뜻이다. 회계사 확인(P4, 엠앤씨솔루션 20260318000447: 「당사에서
# 발생하는 일부의 수익은 진행기준을 적용하여 수익을 인식하고 있습니다」)에서 OVER_TIME 단독은
# 오답으로 지목됐다 — 병행(BOTH)이 맞다.
_PARTIAL_REVENUE_RE = re.compile(r"일부(?:의)?\s*(?:수익|매출)")


def _first_pos_of_value(spec: TopicSpec, value: str, text: str) -> int | None:
    hit = _first_in_order(tuple((p, v) for p, v in spec.positive if v == value), text)
    return hit[1] if hit is not None else None


def _both_evidence(block: PolicyBlock, over: int, point: int) -> str | None:
    """BOTH 행의 근거 — 두 인식 방식 문장을 **모두** 담는 구간.

    회계사 확인(P5, HC보광산업 20260323001053)의 지적: BOTH 인데 근거에는 진행기준 문장만
    있어, 읽는 사람이 "왜 병행인가"를 확인할 수 없었다. 두 트리거가 든 문장의 바깥 경계까지
    넓힌다.

    Fix round 1(리뷰 I2): 400자를 넘을 때 원시 400자 창을 그대로 쓰면 근거가 낱말 한가운데서
    끝난다(실측 엑시나 20260401000853: "…진행률에 따라 용"). 계획이 요구한 것은 「400자 상한
    **안에서 앞뒤 문장 경계까지만**」이다 — 창의 시작은 앞 문장 첫머리로, 끝은 창 안의 마지막
    문장 끝으로 당긴다. 그렇게 잘라도 두 트리거를 다 담지 못하면 넓히지 않는다(None →
    호출자가 결정 패턴이 든 문장 하나를 그대로 쓴다). 잘림 표시는 붙이지 않는다 — 근거는
    원문 축자여야 하기 때문이다."""
    lo, hi = min(over, point), max(over, point)
    sentences = split_sentences(block.text)
    start = next((s.start for s in sentences if s.start <= lo < s.end), None)
    end = next((s.end for s in sentences if s.start <= hi < s.end), None)
    if start is None or end is None:
        return None
    marker = _last_item_marker_start(block.text, start, lo)
    if marker is not None:
        start = marker
    span = block.text[start:end]
    if len(span.strip()) <= _EVIDENCE_MAX:
        return span.strip()
    if hi - lo >= _EVIDENCE_MAX:
        return None
    # 창 시작: 400자 안에 들어오는 가장 이른 **문장 첫머리**(트리거 lo 를 넘지 않는 선에서).
    win_lo = max(start, min(lo, hi + 1 - _EVIDENCE_MAX))
    snapped = [s.start for s in sentences if win_lo <= s.start <= lo]
    if snapped:
        win_lo = min(snapped)
    # 창 끝: 창(win_lo + 400) 안에서 뒤 트리거(hi)를 지나 끝나는 마지막 **문장 끝**.
    limit = win_lo + _EVIDENCE_MAX
    ends = [s.end for s in sentences if hi < s.end <= limit]
    if not ends:
        return None
    return block.text[win_lo : max(ends)].strip()


def _revenue_finish(
    spec: TopicSpec, block: PolicyBlock, text: str, value: str, pos: int, evidence: str
) -> tuple[str, str]:
    """T4 마무리 — 「일부 수익」 승격과 BOTH 근거 확장. (값, 근거)."""
    if value == "OVER_TIME":
        sentences = split_sentences(text)
        sent = next((s for s in sentences if s.start <= pos < s.end), None)
        if sent is not None and _PARTIAL_REVENUE_RE.search(sent.text) is not None:
            value = "BOTH"
    if value != "BOTH":
        return value, evidence
    over = _first_pos_of_value(spec, "OVER_TIME", text)
    point = _first_pos_of_value(spec, "POINT_IN_TIME", text)
    if over is None or point is None:
        return value, evidence
    widened = _both_evidence(block, over, point)
    return value, widened if widened is not None else evidence


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
        values=("EARLY_ADOPTED", "ADOPTED", "NOT_ADOPTED", "PERMITTED_ONLY", UNDETERMINED),
        positive_values=frozenset({"EARLY_ADOPTED", "ADOPTED"}),
        applies=_by_std,
        subject_of=lambda b: b.standard_no,
        negative=_T1_NEG, strip=_T1_STRIP, positive=_T1_POS, fallback=_T1_FALLBACK,
        sentence_judge=_judge_std_early,
    ),
    "PPE_MEASUREMENT": TopicSpec(
        code="PPE_MEASUREMENT",
        values=("COST_MODEL", "REVALUATION_MODEL", "MIXED", UNDETERMINED),
        positive_values=frozenset({"REVALUATION_MODEL", "COST_MODEL"}),
        # T5(R1): 「유형자산」 주제 표제 블록만, 그중에서도 손상 표제 블록은 뺀다
        # (`policy_rules_measurement.ppe_applies`).
        applies=ppe_applies,
        subject_of=lambda b: None,
        negative=(), strip=(
            _p(r"[^.]*(?:원가|재평가)\s*(?:모형|모델)\s*(?:또는|이나|중)[^.]*\."),
            # V5-R1 narrowing(재리뷰): 앞뒤 [^.]* 로 감싼 "문장 확장형" strip 은 재평가 비교표처럼
            # 마침표가 없는 텍스트에서 트리거 구절 뒤에 이어지는 진짜 긍정 문장까지 통째로
            # 삼켜버린다(실측: 표 뒤에 "…재평가모형을 적용하고 있습니다." 를 붙이면 그 문장까지
            # 지워져 REVALUATION_MODEL 이 UNDETERMINED 로 뒤집힌다). 구절 자체만 지우는
            # phrase-only strip 으로 좁혀 COST 트리거만 제거하고 뒤따르는 문장은 건드리지 않는다.
            # V6 R2: "적용하여 측정하였을 경우"처럼 동사와 어미 사이에 "하여 측정"이 끼는 변형도
            # 가정형이다 — 기존 패턴(동사 바로 뒤에 어미)을 확장한다(대체, 상위호환).
            # 리뷰 Critical #1(C1): R1 이 COST 긍정 동사 집합에 "채택|사용|평가|측정"을 추가했는데
            # 이 strip 의 동사 집합은 "적용|평가" 뿐이라 "원가모형으로 측정하는 경우"가 strip 을
            # 통과해 COST 트리거로 살아남았다(재평가 회사의 IAS 16.77(e) 의무 공시표 — 실측
            # 20260409002974·20260213000544). strip 동사 집합을 positive(policy_rules_measurement.T2_POSITIVE 의 COST 확장
            # 패턴, `유형자산.{0,60}원가…(적용|채택|사용|평가|측정)`)와 정확히 동기화한다 — 이
            # 불변식은 `PpeCostVerbSyncInvariantTests`(C1/I4)로 고정한다.
            _p(
                r"원가\s*(?:모형|모델)\s*(?:을|를|으로)?\s*(?:적용|채택|사용|평가|측정)(?:하여\s*측정)?"
                r"\s*(?:시|했을|하였을|되었을|할\s*경우|하는\s*경우)"
            ),
            # C1: 위 패턴도 어미(시/했을/하였을/…)를 요구해, 어미 없이 표 열 제목으로만 나오는
            # "원가모형 평가 장부가액"(조사·어미 없이 명사구로 바로 끝남)은 못 잡는다 — 실측
            # 20260429000737: 정확한 REVALUATION_MODEL 진술이 같은 블록의 이 표 제목 때문에
            # MIXED 로 강등됐다. 표 열 제목 형태를 별도로 strip 한다.
            _p(r"원가\s*(?:모형|모델)(?:으로|로)?\s*(?:평가|측정)\s*장부(?:금액|가액)"),
            # V6 R1: "원가모형에서 재평가모형으로 변경" 문장의 "원가모형에서"가 COST 트리거로
            # 오탐하지 않도록 구절만 지운다(문장 전체를 지우면 뒤따르는 REVAL 트리거까지
            # 삼켜질 위험이 있다 — V5-R1 narrowing 과 같은 이유).
            _p(r"원가\s*(?:모형|모델)에서"),
            # V6 R2: "재평가모형을 적용하는 자산으로서 …"는 손상 회계정책 문구(자산 손상 규정이
            # 적용되는 대상을 서술)지 회사가 측정모형으로 재평가모형을 선택했다는 진술이 아니다.
            # 구절만 지운다. 재리뷰 Minor #3(I4 하드닝): 가드 없이는 "재평가모형을 적용하는
            # 자산은 토지이며, 매 보고기간말 재평가를 실시합니다"(진짜 REVAL 진술, "자산은"으로
            # 이어짐)도 지워 REVALUATION_MODEL 을 UNDETERMINED 로 뒤집었다 — 손상정책 문구의
            # 표면형("…자산으로서")일 때만 지우도록 좁힌다.
            _p(r"재평가\s*(?:모형|모델)(?:을|를)?\s*적용하는\s*자산(?=으로서)"),
        ),
        positive=T2_POSITIVE, fallback=(),
        combo_value="MIXED",
        strip_fn=strip_conditional_clauses,
        stripped_held_reason=HELD_CONDITIONAL_ONLY,
    ),
    "REVENUE_METHOD": TopicSpec(
        code="REVENUE_METHOD",
        values=("OVER_TIME", "POINT_IN_TIME", "BOTH", UNDETERMINED),
        positive_values=frozenset({"OVER_TIME", "POINT_IN_TIME", "BOTH"}),
        applies=_by_topic("수익인식"),
        subject_of=lambda b: None,
        negative=(), strip=_T4_STRIP, positive=_T4_POS, fallback=(),
        combo_value="BOTH", strip_fn=_strip_point_after_over_time,
    ),
    "IP_MEASUREMENT": TopicSpec(
        code="IP_MEASUREMENT",
        values=("COST_MODEL", "FAIR_VALUE_MODEL", UNDETERMINED),
        positive_values=frozenset({"FAIR_VALUE_MODEL", "COST_MODEL"}),
        applies=_by_topic("투자부동산"),
        subject_of=lambda b: None,
        negative=(), strip=(
            _p(r"[^.]*(?:원가|공정가치)\s*(?:모형|모델)\s*(?:또는|이나|중)[^.]*\."),
            # R2(재리뷰 Important #1 수정): 앞의 [^.]* 는 "완결된 한 문장이라 안전하다"는 옛 주석과
            # 달리 실제로는 위험하다 — 정책 진술이 표 셀처럼 마침표 없이 각주 앞에 바로 붙으면
            # (예: "…원가모형을 적용하고 있습니다 (주1) …범주에서…") 선두 [^.]* 가 그 진술까지
            # 거꾸로 삼켜 COST_MODEL 이 UNDETERMINED 로 뒤집힌다(PPE·"과거에" 와 같은 유형의 결함,
            # 실측 재현). phrase-bounded 로 좁혀 "범주에서" 앞은 건드리지 않고, 뒤는 "없습니다/없음"
            # 까지만(최대 40자) 허용한다.
            _p(r"범주에서[^.]{0,80}?범주로\s*대체[^.]{0,40}?(?:없습니다|없음)\.?"),
            # V5-R1 narrowing(재리뷰): "과거에…모형…" 이 문장 확장형(앞뒤 [^.]*)이면, "과거에"와
            # 변경 후 모형 트리거("…모형으로 변경")가 같은 문장 안에 있을 때 트리거까지 통째로
            # 지워버린다(실측: "과거에 원가모형을 적용하였으나 당기부터 공정가치모형으로
            # 변경하였습니다." 전체가 사라져 FAIR_VALUE_MODEL 이 UNDETERMINED 로 뒤집힌다).
            # "과거에 …모형(을|를) 적용" 구절만 지우는 phrase-bounded strip 으로 좁힌다.
            _p(r"과거에[^.]{0,120}?(?:원가|공정가치)\s*(?:모형|모델)(?:을|를)?\s*적용"),
        ),
        positive=T3_POSITIVE, fallback=(),
        strip_fn=strip_conditional_clauses,
        stripped_held_reason=HELD_CONDITIONAL_ONLY,
    ),
}
