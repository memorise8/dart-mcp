"""WS8 8개 정책 주제 정의.

제목·값 설명·함정 예시는 스파이크 `common.py`, 키워드는 `build_inputs.py`에서
옮겼다(`IP_FAIR_VALUE_DISCLOSED` → `IP_FAIR_VALUE`). 우선 위치 정규식은 스펙
§4.1 표를 따르며, 튜플 순서가 곧 우선순위다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicDef:
    code: str
    title: str
    values: tuple[str, ...]
    value_help: dict[str, str]
    traps: tuple[str, ...]
    keyword_re: re.Pattern
    priority_res: tuple[re.Pattern, ...]


def _def(code: str, title: str, value_help: dict[str, str], traps: list[str],
         keyword: str, priorities: list[str]) -> TopicDef:
    return TopicDef(
        code=code,
        title=title,
        values=tuple(value_help),
        value_help=dict(value_help),
        traps=tuple(traps),
        keyword_re=re.compile(keyword),
        priority_res=tuple(re.compile(p) for p in priorities),
    )


# 번호 붙은 주석 머리("31. 사업결합"). "2.5 사업결합" 같은 회계정책 소절은 제외.
_BC_NOTE_HEAD = r"(?:^|\s)\d{1,2}\.\s*사업결합"
# 회계정책·주석의 금융자산 절 머리("(3) 금융자산", "2.7 금융자산", "가. 비파생금융자산").
_FA_SECTION_HEAD = r"(?:^|\s)(?:\d{1,2}(?:\.\d{1,2})*\.?|\(\d{1,2}\)|[가-하]\.)\s*(?:비파생\s*)?금융자산"

_DEFS = [
    _def(
        "CONVERSION_OPTION_CLASS", "전환사채 전환권 분류",
        {"EQUITY": "전환권을 자본(전환권대가)으로 분류",
         "LIABILITY": "전환권을 부채(파생상품부채 등)로 분류",
         "UNDETERMINED": "창에서 회사 자신의 처리를 확정할 수 없음"},
        ['"전환권이 확정수량의 지분상품과 교환되지 않는 경우 금융부채로 분류합니다." → 조건문(conditional), UNDETERMINED.',
         '"종속기업이 발행한 전환사채의 전환권은 파생상품부채로 인식하였습니다." → 다른 실체(other), UNDETERMINED.'],
        r"전환권",
        [r"전환권대가|파생상품부채", r"복합금융상품", r"사채"],
    ),
    _def(
        "EMBEDDED_DERIVATIVE_SEPARATION", "복합금융상품 내재파생상품 분리 여부",
        {"SEPARATED": "회사가 내재파생상품을 주계약에서 분리해 별도 인식함",
         "NOT_SEPARATED": "회사가 분리하지 않음(복합계약 전체로 측정 등)",
         "NOT_APPLICABLE": "회사에 해당 복합계약이 없다고 명시",
         "UNDETERMINED": "확정 불가"},
        ['"내재파생상품을 포함하는 복합계약은 계약상 현금흐름이 원금과 이자로만 구성되어 있는지를 결정할 때 해당 복합계약 전체를 고려합니다." → 기준서 일반규정(standard_text), UNDETERMINED.',
         '"주계약이 금융자산이 아닌 경우 내재파생상품은 분리하여 회계처리합니다." → 조건부 일반규정, 회사 실행 진술 없음, UNDETERMINED.'],
        r"내재파생|내재된 파생",
        [r"(?:전환|신주인수|교환)?사채|우선주", r"내재파생"],
    ),
    _def(
        "BUSINESS_COMBINATION_COST_CLASS", "사업결합 관련 비용(취득관련원가) 표시 위치",
        {"OPERATING": "영업비용·판매비와관리비로 인식",
         "NON_OPERATING": "영업외비용(기타비용·금융비용 등)으로 인식",
         "UNDETERMINED": '창에서 표시 위치를 확정할 수 없음(예: "발생시 비용으로 처리"만 있음)'},
        ['"취득관련원가는 발생시 당기 비용으로 인식합니다." → 비용 처리만 있고 영업/영업외 구분 없음, UNDETERMINED.',
         '"피합병법인은 사업결합과 관련하여 발생한 원가를 판매비와관리비로 처리하였습니다." → 다른 실체(other), UNDETERMINED.'],
        r"취득관련원가|사업결합 관련 원가|사업결합과 관련하여 발생한 원가",
        [_BC_NOTE_HEAD, r"취득관련원가"],
    ),
    _def(
        "PPA_MEASUREMENT_PERIOD", "사업결합 잠정금액(측정기간) 사용 여부",
        {"PROVISIONAL": "당기 사업결합에 잠정금액을 사용(PPA 미완료)",
         "FINAL": "사업결합 금액이 확정됨(측정기간 종료·PPA 완료 명시)",
         "NO_BUSINESS_COMBINATION": "회사에 해당 사업결합이 없다고 명시",
         "UNDETERMINED": "확정 불가"},
        ['"사업결합이 발생한 보고기간말까지 사업결합에 대한 첫 회계처리를 완료하지 못한다면, 잠정금액으로 재무제표에 보고합니다." → 조건부 정책 문장(conditional), UNDETERMINED.',
         '"측정기간은 취득일부터 1년을 초과할 수 없습니다." → 기준서 일반규정(standard_text), UNDETERMINED.'],
        r"측정기간|잠정적인 금액|잠정금액",
        [r"사업결합"],
    ),
    _def(
        "IP_FAIR_VALUE", "투자부동산 공정가치 공시",
        {"FV_DIFFERS": "회사가 투자부동산 공정가치를 공시했고 장부금액과 다름",
         "FV_EQUALS": "공시한 공정가치가 장부금액과 같음(또는 장부금액이 공정가치의 근사치라고 명시)",
         "FV_NOT_DISCLOSED": "공정가치를 공시하지 않음(신뢰성 있게 측정 불가 등 명시)",
         "UNDETERMINED": "창에서 판단 불가(금액이 창 밖에 있는 경우 포함)"},
        ['"투자부동산의 공정가치는 독립된 평가인이 수행한 평가에 기초하여 결정합니다." → 방법만 있고 금액·장부금액 비교 없음, UNDETERMINED.',
         '"관계기업이 보유한 투자부동산의 공정가치는 …원입니다." → 다른 실체(other), UNDETERMINED.'],
        r"투자부동산의 공정가치",
        [r"투자부동산.{0,40}공정가치"],
    ),
    _def(
        "LEASE_EXEMPTION", "리스 인식 면제(단기·소액) 적용",
        {"SHORT_TERM": "단기리스 면제만 적용",
         "LOW_VALUE": "소액 기초자산 리스 면제만 적용",
         "BOTH": "두 면제 모두 적용",
         "NONE": "면제를 적용하지 않음을 명시",
         "UNDETERMINED": "확정 불가"},
        ['"리스이용자는 단기리스와 소액 기초자산 리스에 인식 면제를 선택할 수 있습니다." → 기준서 일반규정(standard_text), UNDETERMINED.',
         '"단기리스 및 소액 기초자산 리스 관련 비용: 12,345" 같은 표 항목 → 금액 공시는 회사가 면제를 적용했다는 근거로 쓸 수 있음(self). 단, 표 제목만 있고 금액이 없으면 UNDETERMINED.'],
        r"단기리스|소액 ?기초자산|소액자산 리스",
        [r"리스이용자", r"단기리스|소액"],
    ),
    _def(
        "FVOCI_DESIGNATION", "지분상품 기타포괄손익-공정가치(FVOCI) 지정",
        {"DESIGNATED": "회사가 지분상품을 FVOCI로 지정함",
         "NOT_DESIGNATED": "회사가 지정하지 않음을 명시(지분상품을 모두 FVPL 등)",
         "UNDETERMINED": "확정 불가"},
        ['"단기매매항목이 아닌 지분상품에 대한 투자는 최초 인식시점에 … 지정하는 취소불가능한 선택을 할 수 있습니다." → 기준서 일반규정(standard_text), UNDETERMINED.',
         '"채무상품을 기타포괄손익-공정가치 측정 금융자산으로 분류합니다." → 채무상품 분류는 지정이 아님, UNDETERMINED.'],
        r"기타포괄손익-공정가치 ?측정 ?항목으로 지정|기타포괄손익-공정가치로 지정|FVOCI로 지정|취소불가능한 선택",
        [_FA_SECTION_HEAD, r"기타포괄손익-?공정가치"],
    ),
    _def(
        "DEV_COST_CAPITALIZATION", "개발비 자산화 여부",
        {"CAPITALIZED": "회사가 개발비를 무형자산으로 자산화함(장부금액·취득 등)",
         "EXPENSED": "회사가 개발 관련 지출을 전액 비용 처리(경상연구개발비 등)하고 자산화하지 않음",
         "UNDETERMINED": "확정 불가"},
        ['"개발단계 지출은 다음 요건을 모두 충족하는 경우 무형자산으로 인식합니다." → 조건문(conditional), UNDETERMINED.',
         '"연구개발비 중 개발비는 자산 인식요건을 충족하지 못하여 …" 처럼 회사 진술이면 self지만, "종속기업의 개발비"는 other → UNDETERMINED.'],
        r"개발비",
        [r"개발비\s*[\d,]", r"연구.{0,3}개발"],
    ),
]

TOPIC_DEFS: dict[str, TopicDef] = {d.code: d for d in _DEFS}
