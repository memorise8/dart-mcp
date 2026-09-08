"""측정모형(T2 유형자산·T3 투자부동산) 규칙 — 긍정 패턴·조건절 배제·표제 판별·자산군 행.

왜 별도 모듈인가 — T5 를 `policy_rules.py` 안에 다 넣으면 928줄이 된다. 경계는 분명하다:
`policy_sentences` 가 문장 분류를, 여기가 **측정모형 어휘**(어떤 문장이 어떤 모형을 뜻하는가)를,
`policy_rules` 가 **판정 순서**(부정 → strip → 긍정 → 보류)를 맡는다. T4(수익인식)는 판정
순서와 얽힌 전처리가 많아 `policy_rules` 에 그대로 둔다.

자산군 행이 이 모듈에 있는 이유도 같다 — 자산군 행의 값은 `T2_POSITIVE` 와 **같은 어휘**로
확인해야 한다(그러지 않으면 행의 근거가 감사(policy_stance_audit)의 pattern_in_evidence 를
통과하지 못한다. 실측: 어휘를 따로 두었을 때 1,486 보고서 표본에서 53행 위반).

값의 권위는 회계사 확인(2026-09-08 P3~P5)이다:
- 디와이디 20260430001895 연결: 2.2.8 유형자산은 원가모형(「유형자산을 감가상각누계액과
  손상차손누계액을 차감한 잔액으로 표시」). 2.2.11 비금융자산의 손상의 「다만, 재평가모형을
  적용하고 있는 자산의 경우에는 …」은 조건절이라 근거가 될 수 없다.
- 온타이드 20260319001171: 토지·건물 → 재평가모형, 「토지와 건물을 제외한 유형자산」 → 원가모형.
- 라닉스 20260318000717 연결: 「토지를 제외한 유형자산」 → 원가모형, 토지 → 재평가모형.
"""
from __future__ import annotations

import re
from bisect import bisect_right

from dart_search_mcp.policy_blocks import PolicyBlock
from dart_search_mcp.policy_sentences import COMPANY_SUBJECT_RE, KIND_CONDITIONAL, split_sentences

_COST_MODEL = "COST_MODEL"
_REVALUATION_MODEL = "REVALUATION_MODEL"

# ---- R1 조건절 배제 -------------------------------------------------------------
# 「…하는 경우(에는)」「다만 … 의 경우(에는)」 절은 회사가 실제로 고른 측정모형이 아니라
# **그 모형을 쓰는 자산이 있다면 어떻게 처리하는가**를 규정하는 회계처리 조항이다(손상차손
# 환입·재평가잉여금 상계 등). 실측(디와이디 20260430001895)에서 이 조항 하나가 원가모형
# 회사를 REVALUATION_MODEL 로 뒤집었다.
#
# 절이 어디서 시작하는지가 이 규칙의 전부다. 정규식 하나로는 표현할 수 없어(정규식은 늘 가장
# **왼쪽** 매치를 고르는데 우리에게 필요한 것은 조건절 바로 앞의 **마지막** 경계다) 경계를
# 명시적으로 구해 쓴다.
#
# 경계(절이 시작할 수 있는 자리):
#   ① 문자열 처음, ② 문장 구두점(`.,;·ㆍ`) 뒤,
#   ③ 연결어미 「…며」 뒤 — 단, 그 「며」가 **회사의 진술을 닫을 때만**.
#
# ③의 단서가 이 규칙의 핵심이다. Fix round 1 은 「며」를 무조건 경계로 삼았는데, 조건절 자신의
# 서술 안에 「며」가 있으면 조건절의 머리가 살아남았다(리뷰 재현:
# 「연결회사는 재평가모형을 적용하고 있는 자산이 손상되었으며 회수가능액에 미달하는 경우 …」
# → 「재평가모형을 적용하고 있는 자산이 손상되었으며」가 남아 REVALUATION_MODEL 오탐).
# 반대로 「며」를 아예 경계에서 빼면 앞 절의 확정 진술까지 삼킨다(fix round 1 이 고친 그 결함:
# 「연결회사는 유형자산에 대하여 원가모형을 적용하고 있으며 자산의 장부금액이 …초과하는 경우 …」
# → COST_MODEL 소실). 두 재현을 가르는 것은 「며」가 닫는 절의 **주어**다 — 앞은 「자산이」가
# 주어(조건절 서술의 일부)이고, 뒤는 「연결회사는」이 주어(회사의 확정 진술)다.
#
# 그래서 「며」는 다음 둘 중 하나일 때만 경계다(주어 어휘는 `policy_sentences` 정본을 그대로 쓴다):
#   (i)  그 「며」가 닫는 절의 주어가 회사다 — 직전 경계 이후에 회사 주어가 있고, 그 뒤에 다른
#        주어(「…이/가」)가 끼지 않았다.
#   (ii) 「며」 바로 뒤가(쉼표·공백을 건너뛰어) 회사 주어로 새 절을 연다.
# (컨트롤러 지시는 (ii)만 적었으나, (ii)만으로는 위 두 번째 재현(뒤에 오는 주어가 「자산의
#  장부금액이」라 회사 주어가 아니다)이 COST_MODEL 을 다시 잃는다 — 지시가 함께 요구한 세 재현을
#  모두 만족시키려면 (i)이 필요하다. 보고서에 근거를 적었다.)
#
# 「고」·「나」 같은 다른 연결어미는 경계에 넣지 않는다 — 낱말 안("보고기간", "하나")에서도 걸려
# 조건절의 핵심만 남기고 트리거를 살려 보낸다(디와이디 「…적용하고 있는 자산의 경우에는」).
# 「다만 …의 경우」는 구두점을 넘어가는 별도 정형구라 정규식 하나로 따로 잡는다.
_DAMAN_CLAUSE_RE = re.compile(r"다만[^.]{0,120}?(?:의|는)\s*경우(?:에는)?")
_COND_CORE_RE = re.compile(r"는\s*경우(?:에는)?")
_PUNCT_OR_MI_RE = re.compile(r"[.,;·ㆍ]|며")
# 다른 주어("자산이", "장부금액이"). 두 글자 이상을 요구해 "추가 "·"평가 "·"높이 " 같은 낱말이
# 주어로 오인되지 않게 한다.
_OTHER_SUBJECT_RE = re.compile(r"(?<![가-힣])[가-힣]{2,10}(?:이|가)(?=\s)")
_COMPANY_SUBJECT_AFTER_RE = re.compile(rf"[,\s]*(?:{COMPANY_SUBJECT_RE.pattern})")
# 경계를 못 찾아 절이 이 길이를 넘으면 아예 지우지 않는다 — 표처럼 구두점이 없는 텍스트에서
# 절 하나가 수백 자로 늘어나 확정 진술을 통째로 삼키는 것을 막는다(못 지우는 쪽이 안전하다).
_CLAUSE_MAX = 200


def _mi_closes_company_clause(text: str, clause_from: int, mi_start: int, after: int) -> bool:
    """「며」가 절 경계인가 — 위 주석의 (i)·(ii)."""
    if _COMPANY_SUBJECT_AFTER_RE.match(text, after) is not None:
        return True  # (ii) 뒤가 회사 주어로 새 절을 연다
    subject = COMPANY_SUBJECT_RE.search(text, clause_from, mi_start)
    if subject is None:
        return False
    return _OTHER_SUBJECT_RE.search(text, subject.end(), mi_start) is None  # (i)


def _clause_starts(text: str) -> list[int]:
    """절이 시작할 수 있는 자리(오름차순). 왼쪽에서 오른쪽으로 훑는다 — (i) 판정이 "직전
    경계 이후"를 봐야 하기 때문이다."""
    starts = [0]
    for m in _PUNCT_OR_MI_RE.finditer(text):
        if m.group(0) != "며":
            starts.append(m.end())
        elif _mi_closes_company_clause(text, starts[-1], m.start(), m.end()):
            starts.append(m.end())
    return starts


def strip_conditional_clauses(text: str) -> str:
    """조건절을 길이 보존 치환으로 지운다(Q-I2 — 오프셋이 원문과 정확히 같아야 근거가 축자다)."""
    spans = [(m.start(), m.end()) for m in _DAMAN_CLAUSE_RE.finditer(text)]
    starts = _clause_starts(text)
    for m in _COND_CORE_RE.finditer(text):
        begin = starts[bisect_right(starts, m.start()) - 1]
        if m.start() - begin <= _CLAUSE_MAX:
            spans.append((begin, m.end()))
    if not spans:
        return text
    chars = list(text)
    for begin, end in spans:
        chars[begin:end] = " " * (end - begin)
    return "".join(chars)


# ---- 유형자산 표제 판별 ----------------------------------------------------------
# 「(1) 유형자산, 사용권자산 손상차손 및 환입」처럼 표제 자체가 손상 회계정책인 블록은 측정모형
# 블록이 아니다(실측 흥아해운 20260318000745). 표제는 "첫 주격·목적격 조사 앞까지" 로 본다 —
# 주석 표제는 서술어가 없는 명사구고, 본문은 조사가 붙은 낱말로 시작하기 때문이다. 조사 "의"는
# 표제 안에서도 흔해서(「유형자산 및 무형자산의 손상」) 경계에 넣지 않는다.
_HEAD_PARTICLE_RE = re.compile(r"(?:은|는|이|가|을|를|에서)\s")
# 상한 30자 — 표 블록("(2) 유형자산 장부금액 (당기) (단위 : 천원) 구분 취득가액 … 손상차손누계액 …")은
# 조사가 없어 상한까지 통째로 표제가 되는데, 상한이 넓으면 표의 열 이름 「손상차손누계액」이
# 손상 표제로 오인된다. 실제 손상 표제(「유형자산, 사용권자산 손상차손 및 환입」 25자,
# 「유형자산 및 무형자산의 손상」 15자)는 전부 30자 안에 든다.
_HEAD_MAX = 30


def block_heading(block: PolicyBlock) -> str:
    """블록 표제(첫 주격·목적격 조사 앞까지, 최대 30자). 조사가 없으면 앞 30자."""
    m = _HEAD_PARTICLE_RE.search(block.text, 0, _HEAD_MAX)
    return block.text[: m.start() if m is not None else _HEAD_MAX]


def ppe_applies(block: PolicyBlock) -> bool:
    """PPE 측정모형 판정 대상 블록 — 「유형자산」 주제 표제 블록이되 손상 표제는 뺀다."""
    return block.topic_heading == "유형자산" and "손상" not in block_heading(block)


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# ---- T2 유형자산 측정모형 ------------------------------------------------------
T2_POSITIVE = (
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
    # 재리뷰 N3 로 붙였던 "유형자산" 앵커는 재리뷰 X5 로 되돌린다 — 이 패턴은 애초에
    # `applies=_by_topic("유형자산")` 로 PPE 블록에서만 돌아가고(REVAL 방향 쌍둥이도 앵커가
    # 없다), 앵커를 넣어도 N3 가 겨냥한 재현(투자부동산 문장이 블록 표제의 "유형자산" 토큰
    # 60자 이내에 있는 경우)은 그대로 발화해 안 닫혔는데, Minor #2 원 목표 문장("재평가모형에서
    # 원가모형으로 회계정책을 변경하였습니다." 단독)은 오히려 다시 UNDETERMINED 로 되돌렸다
    # (X5, 순손실).
    (_p(r"원가\s*(?:모형|모델)(?:으로|로)\s*(?:회계정책을\s*)?(?:변경|전환)(?:하였|했|하기로)"), "COST_MODEL"),
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
    # 재리뷰 X4(Minor): 위에서 새로 넓힌 "계상"·"평가되어" 두 표면형이 I1 이 닫으려던 손상
    # 조건절 클래스를 다시 열었다 — "재평가모형에 따라 재평가금액을 장부금액으로 계상하는
    # 경우 손상차손은 …"/"재평가모형에 따라 평가되어 있는 자산의 손상차손은 …" 둘 다 손상
    # 회계처리 규정이지 회사의 측정모형 선택 진술이 아니다(합성 재현, 실데이터 0건). "재평가
    # 모형에 따라" 뒤 60자 이내에 "손상"이 있으면 배제한다.
    (_p(
        r"재평가\s*(?:모형|모델)에\s*따라(?![^.]{0,60}손상)[^.]{0,60}?(?:재평가를?\s*(?:실시|수행)|재평가하여"
        r"|평가(?:하고|하였|되었|되고|되어)|측정(?:하고|하였|되고|되어)|계상)"
    ), "REVALUATION_MODEL"),
    # V8: "재평가모형을 선택/도입"도 채택 진술이다(실측 batch-01.md: "재평가모형을 선택하고
    # 있으며"). 마지막 동사 어미는 컨트롤러가 준 `(?:하|했|하였|하여)`에서 홑 "하"를 뺐다 —
    # "하"는 "선택하지 않았습니다"(부정)·"선택하는 경우"(조건)처럼 "하"로 시작하는 모든
    # 활용형과 매치돼(했다"만 다른 음절), "했|하였|하여"가 이미 커버하는 완료형 외에
    # 부정·조건형까지 함께 열어버린다(스크립트로 확인) — 완료형 세 가지만 남긴다. 구현 중
    # 발견: I1/X4 손상 조건절 대조 문장("재평가모형을 도입하여 재평가모형에 따라 …하는
    # 경우 …손상차손은 …")이 이 "도입" 표면형으로 다시 발화하는 회귀를 스크립트로 잡았다 —
    # X4 와 같은 손상 문맥 배제 룩어헤드를 이 패턴에도 붙였다.
    # 재리뷰 Important(V11): 그 블랭킷 `(?![^.]{0,60}손상)` 룩어헤드가 실데이터에서 42건의
    # 진짜 재평가 선언("재평가모형을 회계정책으로 선택하여 토지 및 건물을 공정가치로
    # 측정하고 있으며, 건물의 경우 공정가치에서 재평가시점 이후의 감가상각누계액과
    # 손상차손누계액을 차감하여 표시하고 있습니다"류, 통상적인 장부금액 표시 서술일 뿐 손상
    # 조건절이 아님)를 UNDETERMINED 로 억눌렀다 — "경우"류 조건 표지가 "손상" 바로 앞(20자
    # 이내)에 있을 때만 배제하도록 좁힌다.
    (_p(
        r"재평가\s*(?:모형|모델)(?:을|를)?\s*(?:선택|도입)"
        r"(?![^.]{0,60}(?:하는\s*경우|경우에는|경우)[^.]{0,20}손상)(?:했|하였|하여)"
    ), "REVALUATION_MODEL"),
    # V8: "토지는 …공정가치로 평가/측정"도 재평가모형 채택 진술이다(실측 batch-03.md: "토지는
    # 일정한 주기로 공정가치로 평가하고 있으며").
    # 재리뷰 Important(V10): 같은 커밋의 형제 패턴(선택/도입, 바로 위)은 손상 문맥 배제
    # 룩어헤드를 받았는데 이 패턴은 못 받았다 — "토지를 공정가치로 평가하는 경우 …
    # 손상차손은 …"(I1/X4 가 이미 닫은 손상 조건절과 같은 모양, 트리거만 "재평가모형에
    # 따라"에서 "토지를 공정가치로 평가"로 바뀐 것) 이 그대로 발화해 MIXED 로 오판될 수
    # 있다. 같은 룩어헤드를 붙였다.
    # 재리뷰 Important(V11): 위 선택/도입 패턴과 같은 이유로 좁힌다 — 실데이터 42건의 진짜
    # 재평가 선언(통상적인 손상차손누계액 차감 표시 서술)이 블랭킷 룩어헤드에 억눌렸다.
    (_p(
        r"토지[^.]{0,40}공정가치로\s*(?:측정|평가)"
        r"(?![^.]{0,60}(?:하는\s*경우|경우에는|경우)[^.]{0,20}손상)"
    ), "REVALUATION_MODEL"),
    # T5(회계사 확인 P3·P4): 「원가모형」이라는 낱말 없이 후속측정 방식만 서술하는 원가모형
    # 정형구가 코퍼스의 다수인데 트리거가 하나도 없었다(1,486 보고서 표본에서 PPE COST_MODEL
    # 9행 대 UNDETERMINED 1,215행). 두 표면형을 더한다.
    # (a) 「(취득|역사적) 원가에서 감가상각누계액과 손상차손누계액을 차감」 — 후속측정 기준이
    #     원가라는 단정이다. 「…로 측정/평가」(최초 인식 서술, 재평가모형 회사도 똑같이 쓴다)는
    #     넣지 않는다 — 브리프의 원안(`(에서|로)\s*(감가상각누계액|평가|측정|표시)`)은 실측에서
    #     재평가 단정 회사를 무더기로 MIXED 로 뒤집었다(구현 중 A/B, 보고서에 수치 기록).
    # (b) 「유형자산을 (취득원가에서) 감가상각누계액과 손상차손누계액을 차감한 잔액으로 표시」 —
    #     원가 낱말조차 없는 변형(실측 디와이디 20260430001895 2.2.8). 앞에 "유형자산(을|은)"을
    #     요구해, 재평가 회사의 「재평가금액에서 이후의 감가상각누계액을 차감한 …」 과 갈린다.
    (_p(r"(?:취득|역사적\s*)원가(?:에서|로부터)\s*감가상각누계액"), "COST_MODEL"),
    (_p(r"유형자산(?:을|은)\s*(?:최초\s*인식\s*후\s*)?감가상각누계액과\s*손상차손누계액을\s*차감"), "COST_MODEL"),
)

# ---- T3 투자부동산 측정모형 -----------------------------------------------------
T3_POSITIVE = (
    # 정책 변경 문장은 변경 후 모형이 최종값이다(R3) — 목록 순서 = 우선순위이므로 맨 앞에 둔다.
    # 재리뷰 Important #2: 부정형("…변경한 사항은 없습니다")·조건형("…변경하는 경우")도 그대로
    # 매치돼 같은 블록의 정확한 현재 진술을 무조건 덮어썼다(목록 맨 앞 = 최우선이라 가드가
    # 없으면 항상 이긴다). 단정 어미(하였|했|하기로)를 요구해 실제 완료·확정 진술만 잡는다.
    (_p(r"공정가치\s*(?:모형|모델)\s*(?:으로|로)\s*(?:변경|전환)\s*(?:하였|했|하기로)"), "FAIR_VALUE_MODEL"),
    (_p(r"원가\s*(?:모형|모델)\s*(?:으로|로)\s*(?:변경|전환)\s*(?:하였|했|하기로)"), "COST_MODEL"),
    (_p(r"투자부동산.{0,80}공정가치\s*(?:모형|모델)(?:을|를|으로)?\s*(?:적용|채택|측정)"), "FAIR_VALUE_MODEL"),
    (_p(r"투자부동산.{0,80}원가\s*(?:모형|모델)(?:을|를|으로)?\s*(?:적용|채택|측정)"), "COST_MODEL"),
)


# ---- 자산군별 행 ----------------------------------------------------------------
_ASSET_CLASS = r"토지|건물|구축물|기계장치|차량운반구|비품"
_JOIN = r"\s*(?:와|과|및|,|·|ㆍ)\s*"
# 「토지와 건물은 …」「토지에 대하여 …」 — 자산군 열거 + 주격/처격 조사.
_ASSET_SUBJECT_RE = re.compile(
    rf"((?:{_ASSET_CLASS})(?:{_JOIN}(?:{_ASSET_CLASS}))*)\s*(?:은|는|에\s*대하여|에\s*대해)"
)
# 「토지를 제외한 유형자산」「토지와 건물을 제외한 유형자산」 — subject 는 이 문구 그대로다.
_EXCLUDED_PPE_RE = re.compile(
    rf"((?:{_ASSET_CLASS})(?:{_JOIN}(?:{_ASSET_CLASS}))*(?:를|을)\s*제외한\s*유형자산)"
)
# 자산군 뒤에 이어져야 하는 서술 — 이것이 없으면 자산군 언급일 뿐이다(「토지는 감가상각을
# 하지 않으며」는 측정모형 진술이 아니다).
_REVAL_PREDICATE_RE = re.compile(
    r"공정가치로\s*(?:측정|평가)"
    r"|재평가\s*(?:모형|모델)(?:을|를|에)?\s*(?:적용|채택|선택|도입|따라)"
    r"|재평가하여|재평가를?\s*(?:실시|수행)"
)
_COST_PREDICATE_RE = re.compile(r"원가\s*(?:모형|모델)|(?:역사적|취득)\s*원가(?:에서|로|으로)")
_ASSET_CLASS_RE = re.compile(_ASSET_CLASS)
_WS_RE = re.compile(r"\s+")
EVIDENCE_MAX = 400


def _norm(s: str) -> str:
    return _WS_RE.sub(" ", s).strip()


def _backed_by_t2(sentence: str, value: str) -> bool:
    """그 문장이 `T2_POSITIVE` 의 같은 값 패턴으로도 확인되는가.

    자산군 행의 근거는 회사 수준 행과 **같은 어휘**로 되짚을 수 있어야 한다 — 감사
    (`policy_stance_audit._pattern_in_evidence_rule`)가 행의 value 를 내는 `spec.positive`
    패턴이 evidence 에 있는지 보기 때문이다. 위 두 서술 정규식은 자산군 문장을 **찾기** 위한
    것이라 T2 어휘보다 넓다(예: T2 는 「재평가모형을 선택하여/하였」 완료형만 인정하는데 여기는
    「선택」 전부를 받는다) — 넓은 쪽으로만 잡힌 문장은 행으로 내지 않는다."""
    return any(pat.search(sentence) for pat, v in T2_POSITIVE if v == value)


def clip_span(text: str, start: int, end: int, pos: int) -> str:
    """[start, end) 를 근거 문자열로. 상한을 넘으면 매치 자리(pos)를 중심으로 창을 잡아 트리거
    구절이 창 밖으로 밀려나지 않게 한다.

    Fix round 1(리뷰 minor): 같은 규칙이 `policy_rules._clip` 과 여기에 따로 있었다 — 상한
    (EVIDENCE_MAX)과 창 계산이 갈리면 근거 길이 불변식이 조용히 어긋난다. 정본은 이 함수 하나다."""
    span = text[start:end].strip()
    if len(span) <= EVIDENCE_MAX:
        return span
    lo = max(start, pos - 150)
    hi = min(end, lo + EVIDENCE_MAX)
    if hi - lo < EVIDENCE_MAX:
        lo = max(start, hi - EVIDENCE_MAX)
    return text[lo:hi].strip()


def asset_class_rows(block: PolicyBlock) -> list[tuple[str, str, str]]:
    """자산군별 (subject, value, evidence). 자산군이 특정되지 않으면 빈 목록.

    문장 단위로 본다 — 「토지와 건물은 … 공정가치로 측정됩니다」의 서술어가 다음 문장의
    자산군에 붙으면 안 되기 때문이다. 조건절 문장은 아예 건너뛴다(R1 과 같은 이유).
    한 subject 가 여러 문장에서 나오면 먼저 나온 문장이 이긴다(블록 앞쪽이 정책 진술,
    뒤쪽이 부연·표인 경우가 압도적으로 많다)."""
    rows: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    def add(subject: str, value: str, evidence: str) -> None:
        if subject in seen:
            return
        seen.add(subject)
        rows.append((subject, value, evidence))

    for sent in split_sentences(block.text):
        if sent.kind == KIND_CONDITIONAL:
            continue
        body = sent.text
        for m in _EXCLUDED_PPE_RE.finditer(body):
            hit = _COST_PREDICATE_RE.search(body, m.end())
            if hit is not None and _backed_by_t2(body, _COST_MODEL):
                add(_norm(m.group(1)), _COST_MODEL, clip_span(body, 0, len(body), hit.end() - 1))
        for m in _ASSET_SUBJECT_RE.finditer(body):
            hit = _REVAL_PREDICATE_RE.search(body, m.end())
            if hit is None or not _backed_by_t2(body, _REVALUATION_MODEL):
                continue
            for name in _ASSET_CLASS_RE.findall(m.group(1)):
                add(name, _REVALUATION_MODEL, clip_span(body, 0, len(body), hit.end() - 1))
    return rows
