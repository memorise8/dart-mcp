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


def judge(spec: TopicSpec, block: PolicyBlock) -> Judgement:
    subject = spec.subject_of(block)
    text = block.text
    hit = _first_in_order(spec.negative, text)
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
            stripped = spec.strip_fn(stripped)
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
    # 근거는 항상 block.text(원문)에서 뽑는다 — stripped 텍스트는 매치 위치(pos)를 찾는 데만 쓰고,
    # 길이 보존 치환 덕에 그 pos 는 block.text 에서도 같은 글자를 가리킨다. 그래서 evidence 는
    # 항상 원문 축자(verbatim)다(Q-I2) — 그렇지 않으면 감사(policy_stance_audit)의
    # evidence_in_block 이 실데이터에서 상시 위반으로 잡힌다.
    return Judgement(spec.code, subject, value, evidence_sentence(block.text, pos), "rule")


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
    (_p(
        r"(?:채택|적용|도입)하지\s*(?:않|아니)|아직\s*채택|(?:검토|평가|파\s*악)\s*중|영향(?:은|이)?\s*(?:중요하지|없)"
        r"|적용할\s*예정|시행될\s*예정|부터\s*(?:적용|시행)(?:됩니다|되며|될|합니다)"
        r"|적용하되|영향을\s*미치지\s*않을\s*것으로\s*예상|영향이\s*없을\s*것으로\s*예상"
        # V7 R1: 1118호 표본 판독(NOT_ADOPTED 80/80 정확, PERMITTED_ONLY 30건 중 0건 정확)에서
        # 나온 시기·영향 서술 변형 — 기존 목록 뒤에 덧붙인다(목록 순서 = 우선순위, PERMITTED_ONLY
        # 항목은 그대로 이 뒤에 남는다).
        r"|최초\s*적용\s*시|적용하여\s*재무제표를\s*작성하는\s*경우"
        r"|(?:회계연도|보고기간)(?:에|부터)\s*적용(?:해야|하여야|됩니다|합니다|되며|될)"
        r"|재작성(?:됩니다|될|되며)|차이를\s*발생시킬\s*것으로\s*예상|영향(?:은|이)?\s*광범위할\s*것으로\s*예상"
        # Round 3 addendum(item 7, T1): 중간 실행에서 1118호 PERMITTED_ONLY 로 남은 4건이 전부
        # "…회계연도부터 의무 적용되며, …조기 적용이 가능합니다."/"…시작일부터 소급적용되며
        # 조기적용이 허용됩니다." 처럼 시기 서술(의무/소급 적용)을 놓쳐서였다 — 기존 목록
        # 뒤에 덧붙인다(순서 불변).
        # 재리뷰 X2(Important): 첫 대안의 "합니다" 가 "기업회계기준서 제1008호에 따라 소급
        # 적용합니다"(회계정책을 소급 **적용한다**는 진술이지 미적용 선언이 아니다)까지
        # 잡아 2,000 보고서 표본에서 21건 중 14건을 오발화시켰다 — "합니다" 를 뺀다(지정
        # 목표 두 문장은 둘 다 "되며"로 끝나 "합니다" 없이도 잡힌다). 둘째 대안
        # `부터…적용` 은 어미를 전혀 요구하지 않아 과거·현재형("…부터 적용한 회계정책")까지
        # 잡았다(실측 20260331002049/1017, 1건) — 형제 대안들처럼 미래·피동 어미를 요구한다.
        r"|(?:의무|소급)\s*적용(?:되며|됩니다|될|됨)|부터\s*(?:의무|소급)?\s*적용(?:되며|됩니다|될|됨|하며|할)"
    ), "NOT_ADOPTED"),
    (_p(r"조기\s*(?:적용|도입)이?\s*(?:허용|가능)"), "PERMITTED_ONLY"),
)

# ---- T2 유형자산 측정모형 ------------------------------------------------------
_T2_POS = (
    (_p(r"재평가\s*(?:모형|모델)(?:을|를)?\s*(?:적용|채택|사용)"), "REVALUATION_MODEL"),
    (_p(r"유형자산.{0,60}원가\s*(?:모형|모델)(?:을|를)?\s*(?:적용|채택|사용)"), "COST_MODEL"),
    # V6 R1: 열거형·동사 변형 — 기존 두 패턴 뒤에 추가한다(목록 순서 = 우선순위이므로 기존
    # 순서를 바꾸지 않는다). "재평가모형을, 기타 유형자산은 원가모형을 적용" 처럼 REVAL 이
    # 쉼표로 끊기는 열거형은 "적용/채택/사용" 동사가 REVAL 트리거 바로 뒤에 없어 기존 패턴이
    # 못 잡았다. 재리뷰 Minor #1(I4 하드닝): 가드 없는 쉼표는 "재평가모형을, 적용하지 않고
    # 원가모형을 적용하고 있습니다" 처럼 바로 뒤에 오는 부정에도 매치돼 COST_MODEL 단독 회사를
    # MIXED 로 잘못 승격시켰다 — 쉼표 뒤가 곧바로 부정("적용하지 않"류)이면 배제한다.
    (_p(r"재평가\s*(?:모형|모델)(?:을|를)?\s*,(?!\s*(?:적용|채택|사용)하지\s*(?:않|아니))"), "REVALUATION_MODEL"),
    # COST 동사에 "평가/측정"(으로/로 조사)을 허용 — "원가모형으로 평가하고 있습니다".
    (_p(r"유형자산.{0,60}원가\s*(?:모형|모델)(?:을|를|으로|로)?\s*(?:적용|채택|사용|평가|측정)"), "COST_MODEL"),
    # "취득원가로 평가"도 COST 트리거다(유형자산 앵커 유지). 재리뷰 Important #2(I2): `.{0,60}`
    # 간격은 실제 문장 마침표를 넘어 이어붙는다(`_first_in_order` 의 docstring 경고 그대로) —
    # "유형자산…법인세법의 규정을 적용하였습니다. 매도가능증권…은 취득원가로 평가하였으나"
    # 처럼 60자 앞의 블록 표제 토큰 "유형자산"이 전혀 다른 자산(매도가능증권)의 문장에 앵커를
    # 채워버렸다(실측 20260424000208, 발화 10건 중 1건 오탐). `[^.]` 로 좁혀 문장 경계를
    # 넘지 않게 한다.
    (_p(r"유형자산[^.]{0,60}취득원가(?:로|으로)\s*평가"), "COST_MODEL"),
    # 정책 변경 문장은 변경 후 모형(REVAL)이 최종값이다 — "원가모형에서 재평가모형으로 …
    # 변경하였습니다".
    (_p(r"재평가\s*(?:모형|모델)(?:으로|로)\s*(?:회계정책을\s*)?(?:변경|전환)(?:하였|했|하기로)"), "REVALUATION_MODEL"),
    # 재리뷰 Minor #2: 위 REVAL 방향 정책변경 패턴만 있고 반대 방향(COST)이 없어 "재평가모형에서
    # 원가모형으로 회계정책을 변경하였습니다"가 UNDETERMINED 로 남았다 — 대칭 패턴을 추가한다
    # (IP_MEASUREMENT 의 _T3_POS 가 공정가치·원가 양방향을 두는 것과 같은 구조).
    # 재리뷰 N3: 같은 _T2_POS 의 다른 COST 패턴들(위 두 개, 취득원가 패턴)은 전부 "유형자산"
    # 앵커를 요구하는데 이 패턴만 없어, PPE 블록 안에서 다른 자산(예: 투자부동산)을 다루는
    # 정책변경 문장에도 발화할 수 있었다 — 앵커를 넣어 형제 패턴들과 맞춘다.
    (_p(r"유형자산[^.]{0,60}원가\s*(?:모형|모델)(?:으로|로)\s*(?:회계정책을\s*)?(?:변경|전환)(?:하였|했|하기로)"), "COST_MODEL"),
    # 재리뷰 Important #1(I1): 가드 없는 "재평가모형에 따라"는 R2 가 막 strip 한 손상정책 오탐
    # 클래스를 다른 표면형으로 다시 들여왔다 — "재평가모형을 도입하여 재평가모형에 따라
    # 재평가금액을 장부금액으로 하는 경우 …"(손상차손 회계처리 규정, 조건절)가 회사의 측정모형
    # 선택 진술로 오판됐다(실측 20260331004511·20260610000623). 문장 안에 회사가 실제로
    # 재평가를 실시·수행했거나 그 방식으로 측정·평가한다는 서술이 있을 때만 인정한다.
    # 재리뷰 Critical #N1: 위 첫 버전(허용 동사 "재평가를 실시/수행"·"측정"·"평가하고" 세
    # 표면형뿐)이 실데이터에서 가장 흔한 피동·연결형("평가되었으며", "재평가하여 … 계상하고")을
    # 전부 놓쳐 진짜 REVALUATION_MODEL 진술 3건을 UNDETERMINED 로 잃었다(실측
    # 20260407000993·20251110000027·20260407003334, 3,000 보고서 표본에서 PPE
    # REVALUATION_MODEL→UNDETERMINED 7건 중 3건이 이 오소실이었다). 피동·연결형을 허용
    # 동사에 추가한다 — 손상 보일러플레이트("…하는 경우 …손상차손…")는 여전히 어느 대안과도
    # 안 맞아 배제된다.
    (_p(
        r"재평가\s*(?:모형|모델)에\s*따라[^.]{0,60}?(?:재평가를?\s*(?:실시|수행)|재평가하여"
        r"|평가(?:하고|하였|되었|되고|되어)|측정(?:하고|하였|되고|되어)|계상)"
    ), "REVALUATION_MODEL"),
)

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
)
# 재리뷰 N2: 앵커가 맨 문자열 "기간에 걸쳐" 였는데, 코퍼스에 흔한 "리스기간에 걸쳐"·
# "보증기간에 걸쳐"·"상각기간에 걸쳐"·"내용연수기간에 걸쳐" 처럼 OVER_TIME 트리거가 아닌
# 복합어에서도 발화해, 같은 문장(마침표 없는 80자 이내)의 진짜 시점기준 진술을 지웠다.
# `_T4_POS` 의 실제 OVER_TIME 트리거(진행률/진행기준/투입법, "기간에 걸쳐" 뒤에 수익·수행의무·
# 인식·이행이 이어지는 형태)로 앵커를 좁힌다.
_POINT_AFTER_OVER_TIME_RE = re.compile(
    r"((?:기간에\s*걸쳐\s*(?:수익|수행의무|인식|이행)|진행률|진행기준|투입법)[^.]{0,80}?)"
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
        r"진행(?:기준|률)(?:에|을|로)?\s*(?:따라|의해|적용|기준으로)|투입법|기간에\s*걸쳐\s*(?:수익을\s*)?인식"
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
    ), "POINT_IN_TIME"),
)

# ---- T3 투자부동산 측정모형 -----------------------------------------------------
_T3_POS = (
    # 정책 변경 문장은 변경 후 모형이 최종값이다(R3) — 목록 순서 = 우선순위이므로 맨 앞에 둔다.
    # 재리뷰 Important #2: 부정형("…변경한 사항은 없습니다")·조건형("…변경하는 경우")도 그대로
    # 매치돼 같은 블록의 정확한 현재 진술을 무조건 덮어썼다(목록 맨 앞 = 최우선이라 가드가
    # 없으면 항상 이긴다). 단정 어미(하였|했|하기로)를 요구해 실제 완료·확정 진술만 잡는다.
    (_p(r"공정가치\s*(?:모형|모델)\s*(?:으로|로)\s*(?:변경|전환)\s*(?:하였|했|하기로)"), "FAIR_VALUE_MODEL"),
    (_p(r"원가\s*(?:모형|모델)\s*(?:으로|로)\s*(?:변경|전환)\s*(?:하였|했|하기로)"), "COST_MODEL"),
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
            # 20260409002974·20260213000544). strip 동사 집합을 positive(아래 _T2_POS 의 COST 확장
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
        positive=_T3_POS, fallback=(),
    ),
}
