"""2층 ② 사채·우선주 기록 근거 검증 — 순수 함수(스펙 §5.3, 브리프 T8 검증 규칙).

1. evidence 각 필드의 근거 id 가 입력 칸·문장에 하나도 없으면 그 필드는 unconfirmed(금액은 null), `missing_ref:<field>`.
   값이 있는데 evidence 키가 없는 필드도 같다.
2. 금액 필드(balance.*.*, separated_amount.*)는 근거 칸 하나의 value(원래 단위)와 같아야 한다.
   unit_kind 가 같고 허용 오차 1. 아니면 null, `amount_mismatch:<field>`.
3. option_class = derivative_liability 는 부채 쪽 파생상품 칸(_deriv_side)이나 옵션 자체를 파생상품부채(파생상품)로
   분류·인식한 문장(부정·조건 아님)이 근거에 있어야 한다. 「파생상품자산」 칸, 금융부채·유동부채·부채요소 분류 문장은
   근거가 아니다. 근거 칸이 부채요소(HOST) 행뿐이면 `host_only`, 그 밖은 `no_derivative_ref` 로 unconfirmed.
   balance.*.derivative_liability·derivative_asset 금액은 같은 쪽(부채·자산) 파생상품 칸과 맞아야 한다(_deriv_side:
   행 이름이나 열 머리글이 파생·옵션을 말하고, 쪽은 행 이름·열 머리글·제목의 명시적 파생상품(부채|자산)으로만).
   맞은 칸이 HOST 행이면 `host_only:<field>`, 그 밖(합계·자본요소 등)은 `not_derivative:<field>` 로 null.
   host_carrying 만 같은 표·같은 열 부채요소 구성 칸의 합을 받는다(_host_sum, 구성 목록·회차 하나 — 회차는 행 이름이나
   label_path 부모에서 읽는다).
   option_class = derivative_liability 는 같은 입력에 그 종류의 자본요소 칸(전환권대가 등, 0 아님)이 같은 회차이거나 회차를
   모를 때 `equity_conflict` 로 unconfirmed. 근거 칸에 회차가 붙어 있으면 기록 회차와 같아야 한다.
   R15: option_class 는 전환권·신주인수권·교환권의 분류다. 그 근거 칸·문장은 이 셋 중 하나를 말해야 한다
   (조기상환청구권·매도청구권·풋·콜 근거는 embedded_separation 과 파생상품 금액에만 쓴다).
   기간(_period_ok): balance.current_end.* 는 current_end 칸(또는 잔액 열·기말 행인 current_period 칸),
   prior_end 는 prior_end 칸(또는 잔액 열·기말 행인 prior_period 칸)과만 맞는다. 기간을 모르는 칸은 값 열이 하나이고
   제목·주석 제목이 당기말·현재를 말하며 발행시·전환시·행사시·최초·평가가 없는 표에서 current_end 에만 맞는다.
   아니면 `period_mismatch:<field>`(host 합이 기간만 어긋나도 같다).
   회차(fix round 5): 금액 근거 칸(합의 칸 포함)의 회차(행 이름·label_path)가 기록 series_key 회차와 다르면(기록 회차를
   모르는데 칸에 회차가 있어도) `series_mismatch:<field>`. 잔액 표가 아닌 표(만기분석·유동성위험·신용위험·이연법인세·
   상계·담보·거래상대방; 현금유입·유출·거래상대방 종류 머리글)의 칸은 `not_balance:<field>`.
   두 쪽을 말하는 제목·머리글·행 이름(「파생상품자산ㆍ부채」·「파생금융자산(부채)」·「파생상품자산부채」 …)은 쪽을
   모르고, 「비파생…」은 파생 낱말이 아니다. 옵션 부채 문장은 같은 문장에 「자산」이 있으면 근거가 아니다(보유자 쪽).
   (R14: 정밀도가 통과율보다 우선 — 거부된 필드는 판단불가가 되지만 거짓 수락은 틀린 판정이 된다.)
4. kind 가 CB|BW|EB|RCPS 가 아니면 기록을 버린다(None, [`bad_kind:<kind>`]).
5. series_key 가 비면 instrument 의 「제 n 회」로 `<kind>-<n>`, 없으면 `<kind>-?<seq>`.
6. 확인된 금액 필드는 `matched[field] = 칸 id`, `matched_multiplier[field] = 그 칸 배수`(원 환산용)를 남기고,
   그 필드의 evidence 는 그 칸 하나만 둔다. null 이 된 필드의 evidence 는 빈 목록이다.
7. option_class·embedded_separation 이 null·없음이면 사유 없이 unconfirmed.
8. T9b: 만기 구간·계약상현금흐름 열의 칸, 그런 열이 있는 표의 장부금액 아닌 열(합계 열)은 모든 금액 필드에서 not_balance.
   기간 모르는 칸의 current_end 허용은 취득일 등 사건 날짜·발행조건·발행내역·자본금·(보통)주식 표, 「전기말 현재」만 말하는
   표에서 쓰지 않는다. 범주 행렬의 다른 범주 열(상각후원가·기타포괄 …)은 파생상품 칸이 아니다. 회차 일치는 칸이 말하는
   상품 종류(전환사채·신주인수권부사채·교환사채·우선주)도 본다. embedded_separation 은 뜻이 맞는 근거(_separation_supported)가
   있어야 하고, 아니면 `no_separation_ref` 로 unconfirmed.
9. fix round 5(R18): 분리 문장(분리함·분리 안 함·FVPL 지정·상각후원가)은 상품 종류를 말해야 그 종류의 확인 근거다
   (_strict_fits; 일반 옵션 낱말만이면 어느 종류도 아님, 바로 앞 문장이 한 종류만 말하고 이어 쓰면 그 종류). 막는 쪽(섞인
   부정 문장·모순 검사)은 이전처럼 넓게 본다. 분리 동사는 제 마디로 읽어 자본 분리 마디를 거르고, 「분리하여 공시하지 않」은
   분리 진술이 아니다. 「X가 발행한」(X 자기 회사 아님)·종속기업·관계기업·「B사 전환사채」 문장은 확인 근거가 아니다.
   option_class 문장·칸에도 보유자 낱말·보유자 공시 가드를 건다(_issuer_option_sentence·_issuer_option_cell).
10. T9c(R14·R15·R17): option_class 문장·칸은 R15 옵션 낱말 지도로만 종류에 묶는다(전환권 → CB·RCPS, 신주인수권 → BW,
   교환권 → EB; 상환권·조기상환·콜·풋은 어느 종류에도 묶지 않는다). 종류 낱말이 없는 option 문장은 바로 앞 문장을 이어 쓰거나
   (_continues), 입력 전체에 그 옵션 낱말이 가리키는 종류가 하나뿐일 때만 그 종류다. 분리 문장·option 문장의 종류 낱말은
   동사가 든 마디(_kind_clause_span)와, 그 마디가 종류도 제 주제어도 없을 때 동사 바로 앞 주어 구에서만 읽는다(_verb_kinds).
   증권 보유자 낱말(매도가능증권·단기매매증권·만기보유증권·유가증권·당기손익인식금융자산·매도가능금융자산·
   기타포괄손익-공정가치측정금융자산)은 확인 쪽만 막고 막는 쪽(섞인 부정·모순 검사)은 이전 낱말로 읽는다. 보유자 공시에서
   「발행회사」는 자기 회사가 아니다. 「상환전환우선주당사가 발행한」처럼 제목이 붙은 주어는 자기 회사다. 제3자 지정 콜을
   자산으로 분리했다는 문장은 separated 확인 근거가 아니다(_third_party_call_only).
   fix round 1: 「지정한 자」·「제3자를 지정」도 제3자 지정 콜이다. 「내재파생상품이 아닌」 문장은 확인 쪽에서 분리 진술이 아니다.
   not_separated·whole_fvpl 확인 문장(분리하지 않음·상각후원가·전체 FVPL 지정)도 동사 마디 종류 규칙을 쓴다.
"""
from __future__ import annotations

import copy
import re

KINDS = ("CB", "BW", "EB", "RCPS")
OPTION_CLASSES = ("equity", "derivative_liability", "unconfirmed")
EMBEDDED_SEPARATIONS = ("separated", "not_separated", "whole_fvpl", "unconfirmed")
MOVEMENTS = ("issued", "converted", "redeemed", "matured", "extinguished", "modified", "none")
UNCONFIRMED = "unconfirmed"
AMOUNT_TOLERANCE = 1

_HOST_RE = re.compile(r"^전환사채$|사채할인발행차금|전환권조정|신주인수권조정|교환권조정|상환할증금")
_OPTION_RE = re.compile(r"전환권|상환권|조기상환|매도청구권|콜옵션|풋옵션|신주인수권|교환권")
_OPTION_EXCLUDE_RE = re.compile(r"대가|자본|조정|가치")
_SIDE_DERIV_RE = re.compile(r"(?<!비)파생(?:상품|금융상품|금융)?(부채|자산)")
_JOINED_GAP = 6  # 「파생상품자산 및 파생상품부채」의 「및」 같은 짧은 이음
_TOTAL_RE = re.compile(r"^(합계|총계|계|소계)$")
_INSTRUMENT_RE = re.compile(r"신주인수권부사채|전환사채|교환사채|우선주")
_JOINED_INSTRUMENT_RE = re.compile(r"(전환사채|신주인수권부사채|교환사채|우선주|우선주부채)(및|과|와|,|·)")
# 옵션 자체를 파생상품부채로 잡았다는 문장(R14): 「전환권…파생상품부채로 분류」, 「전환권…(을|는)…파생상품으로 인식」.
# 금융부채·유동부채·부채요소로의 분류는 옵션의 파생상품부채 근거가 아니다.
# R15: option_class 는 전환권·신주인수권·교환권(CONV)의 분류다. 문장은 CONV 가 든 옵션 목록이 주어(목적어)이고
# 그 목록이 곧바로 조사를 받거나, 그 목록을 담은 「내재파생상품」이 주어일 때만 근거가 된다.
# 조기상환청구권·매도청구권·풋·콜·내재파생상품만 주어인 문장은 option_class 근거가 아니다.
_CONV = r"(?:전환권|신주인수권(?!부)|교환권)(?!\s*(?:조정|대가|가치))"
_PAREN = r"(?:\s*\([^()]{0,20}\))?"
_OPT_ANY = (r"(?:전환권|신주인수권(?!부)|교환권|조기상환청구권|조기상환권|상환청구권|상환권|매도청구권|콜옵션|풋옵션"
            r"|[Cc]all\s*[Oo]ption|[Pp]ut\s*[Oo]ption)" + _PAREN)
_JOIN = r"\s*(?:,|및|과|와|또는|·|/)\s*"
_CONV_LIST = rf"(?:{_OPT_ANY}{_JOIN})*{_CONV}{_PAREN}(?:{_JOIN}{_OPT_ANY})*(?:\s*등)?"
_MARK = r"\s*(?:은|는|을|를|이|가)\s*"
_DERIV_END = (r"(?<!비)(?:파생상품부채|파생금융부채|파생상품금융부채|파생금융상품부채|파생상품|파생금융상품)"
              r"\s*(?:으로|로)\s*(?:분류|인식|계상|회계처리)")
_GAP = r"(?P<gap>[^.。]{0,60}?)"
_OPTION_LIAB_SENT_RES = (
    re.compile(_CONV_LIST + _MARK + _GAP + _DERIV_END),
    re.compile(r"내재파생상품\s*\(\s*" + _CONV_LIST + r"\s*\)" + _MARK + _GAP + _DERIV_END),
    re.compile(_CONV_LIST + r"\s*(?:이|가|을|를)?\s*(?:포함된|포함한|포함하는|으로\s*구성된|로\s*구성된)\s*내재파생상품"
               + _MARK + _GAP + _DERIV_END),
)
# 틈 안에 다른 주어(풋·콜·상품·주계약)나 자본 분류가 있으면 그 파생상품은 전환권이 아니다.
_GAP_OTHER_SUBJECT_RE = re.compile(
    _OPT_ANY + r"(?:\s*등)?\s*(?:은|는|을|를|이|가)(?=\s|$)"
    r"|(?:사채|우선주|부채요소|주계약|금융상품|복합상품)\s*(?:은|는|이|가)(?=\s|$)"
    r"|자본(?:요소)?\s*(?:으로|로)|자본에\s*(?:계상|반영)"
    r"|^(?:없|않|아닌|아니)")  # 「전환권 및 상환권이 없는 우선주」 — 조사 뒤 곧바로 부정이면 전환권은 주어가 아니다
# 같은 문장이 전환권을 자본으로 옮겼다고 하면(「전환권을 …기타자본잉여금으로 분류」) 파생상품부채 근거가 아니다.
_CONV_TO_EQUITY_RE = re.compile(_CONV + r"[^.。]{0,40}?자본(?:잉여금|조정)?\s*(?:으로|로)")
_CONDITIONAL_RE = re.compile(r"경우|때에는|때는|라면")
# 파생상품 잔액이 아닌 열: 공정가치 수준(서열체계)·회계오류 수정·재작성 금액.
_LEVEL_OR_RESTATE_COL_RE = re.compile(r"수준|[Ll]evel|회계오류|오류수정|수정금액|수정의영향|조정금액|재작성|소급")
# 잔액을 명시한 열(기간이 current_period·prior_period 인 표에서 balance.*_end 로 읽을 수 있는 열).
_BALANCE_COL_RE = re.compile(r"장부금액|잔액|기말|보고기간종료일|보고기간말")
_END_ROW_RE = re.compile(r"^(?:당?기말|전기말|기말(?:잔액|금액)?|당기말잔액|전기말잔액|보고기간종료일(?:현재)?(?:장부금액|잔액)?)$"
                         r"|장부가액|장부금액")
# 「보고기간종료일 현재 …의 내역」처럼 한 시점의 잔액 표(변동·손익 표 아님) — 당기·전기 열이 곧 당기말·전기말이다.
_STATUS_TITLE_RE = re.compile(r"현재")
_UNKNOWN_OK_RE = re.compile(r"당기말|현재")
# 사건 시점·평가·발행조건·자본금·주식 표(T9b): 「취득일 현재」 이전대가, 「…의 (주요) 발행조건」·「발행내역」 글 표의 권면총액·
# 발행가액, 자본금·(보통)주식 표는 잔액 표가 아니다. 「우선주식(부채)」는 상품 이름이라 막지 않는다.
_EVENT_TITLE_RE = re.compile(r"발행시|전환시|행사시|최초|평가|가치평가|(?:취득|부여|발행|전환|행사|측정)일"
                             r"|발행조건|주요조건|발행내역|발행내용|자본금|(?<!우선)주식")
_FLOW_TITLE_RE = re.compile(r"변동|증감|손익|발생|거래|현금흐름|[당전]기\s*중|기중")


def _title_tail(title: str | None) -> str:
    """1층 제목에 붙어 온 앞 문단을 떼고 표 바로 앞 문장만(「…다.」로 나눈 마지막 의미 있는 조각)."""
    parts = [p for p in re.split(r"다\.", title or "") if len(p.strip()) > 5]
    return parts[-1] if parts else (title or "")
_HOST_COMPONENT_RE = re.compile(
    r"^(?:제?\d+(?:-\d+)?(?:회차|회|차|종))?"
    r"(?:액면(?:금액|가액|가)?|원금|발행가액|전환사채|신주인수권부사채|교환사채|사채|(?:전환)?(?:상환)?(?:전환)?우선주부채"
    r"|사채할인발행차금|전환권조정|신주인수권조정|교환권조정|(?:전환사채|사채)?상환할증금|현재가치할인차금)$")
# 잔액이 아닌 칸: 손익(평가이익·손실), 변동(발행·행사·상환·증감), 민감도(1% 증가) — 행 이름이나 열 머리글.
_NOT_BALANCE_RE = re.compile(r"평가|손익|이익|손실|비용|수익|민감|%|증가|감소|증감|변동|행사|취득|처분|대체|발생|신규|이동|최초"
                             r"|매입|매도(?!청구)|결제|정산")
_OTHER_COL_RE = re.compile(r"^기타(?:\(.*\))?$")  # 변동표의 「기타(*)」 열
# 범주 행렬(T9b): 파생상품 행은 자기 범주 열(당기손익-공정가치·파생·위험회피)에서만 파생상품 칸이다.
_OTHER_CATEGORY_RE = re.compile(r"상각후원가|기타포괄|매도가능|만기보유|대여금|수취채권|금융보증")
_OWN_CATEGORY_RE = re.compile(r"당기손익|파생|위험회피")
# 열 머리글만 파생을 말하는 칸은 행이 잔액 행(기말)·상품·회차 행일 때만(범주 행렬의 「유가증권 × 파생상품자산」 같은 칸 제외).
_COL_ROW_RE = re.compile(r"^(?:당?기말|전기말|기말(?:잔액|금액)?|당기말잔액|전기말잔액|보고기간종료일(?:현재)?(?:장부금액|잔액)?)$"
                         r"|신주인수권부사채|전환사채|교환사채|우선주|Series|시리즈|^제?\d+(?:-\d+)?(?:회차|회|차|종)", re.I)
# 잔액 표가 아닌 표(「현재」가 제목에 있어도): 만기분석(잔존계약만기·만기별·할인하지 않은 현금흐름)·유동성위험·
# 신용위험(신용건전성·최대노출)·이연법인세·상계·담보·거래상대방 집중도 — 표를 설명하는 문장(제목 끝 문장)이나 주석 제목.
# 「발행일 만기일 당기말」 같은 머리글 글자, 「만기 및 이자율」 차입금 내역 표, 앞 문단의 다른 이야기는 아니다.
_NON_BALANCE_TITLE_RE = re.compile(r"만기분석|잔존계약만기|계약상만기|만기별|만기구조|할인하지|유동성위험|신용위험|신용건전성"
                                   r"|최대노출|이연법인세|상계|담보|거래상대방|집중도")
# 현금흐름(만기분석의 현금유입·유출)·상계·담보·신용보강 열, 현금유입·유출 행, 거래상대방 종류 열.
_NON_BALANCE_COL_RE = re.compile(r"현금유입|현금유출|상계|담보|신용보강|최대노출|거래상대방")
_NON_BALANCE_ROW_RE = re.compile(r"^(?:현금유입|현금유출)")
_COUNTERPARTY_COL_RE = re.compile(r"^(?:개인|기업|법인|금융기관|은행|정부|공공기관|국가|지방자치단체|소매|도매|기관|중소기업|대기업"
                                  r"|특수관계자|기타특수관계자|관계기업|종속기업|공동기업|국내|해외|기타거래처|거래처)$")


def _title_core(title: str | None) -> str:
    """제목에서 표를 설명하는 끝 문장(「…다.」로 나눈 조각 중 9자 이상인 마지막 것 — 「① 당기」·「〈당기말〉」 꼬리 제외)."""
    parts = [p for p in re.split(r"다\.", title or "") if len(re.sub(r"\s+", "", p)) > 8]
    return parts[-1] if parts else (title or "")


# 만기 구간·계약상 현금흐름 열(T9b): 「3개월 미만」·「1년 이내」·「1년~5년」·「5년 초과」·「계약상현금흐름」·「잔존계약만기」.
# 「2025년 12월 31일」 같은 날짜 머리글은 아니다(구간 낱말이 붙어야 한다).
_MATURITY_BUCKET_RE = re.compile(r"\d+(?:개월|년|일)(?:이내|미만|이하|초과|이상)|\d+(?:개월|년|일)?[~∼\-–]\d+(?:개월|년|일)"
                                 r"|계약상(?:의)?현금흐름|잔존(?:계약)?만기|요구불")
_CARRYING_COL_RE = re.compile(r"장부금액|장부가액")


def _col_segments(cell: dict) -> list[str]:
    return [seg for seg in re.sub(r"\s+", "", cell.get("col_header") or "").split("/") if seg]


def _maturity_bucket_cell(cell: dict) -> bool:
    """열이 만기 구간·계약상 현금흐름인 칸. 행 묶음의 만기 서술(차입금·사채 내역 표의 「2025년~2026년」·「1년이내」 칸)은
    보지 않는다 — 그것은 내역 표의 설명 열이지 만기분석 구간이 아니다."""
    return any(_MATURITY_BUCKET_RE.search(seg) for seg in _col_segments(cell))


# 상품 이름 뒤에 변동 낱말이 오는 행(fix round 3): 「전환사채 상환」·「전환사채 전환」·「상환전환우선주 발행」은 변동 행이다.
# 옵션·구성 낱말(전환권·조기상환청구권·상환할증금·행사가격·발행가액·미상환잔액 …)은 아니다.
# fix round 4: 상품 낱말 뒤 꼬리에서 옵션 복합어(상환전환권·투자자 상환전환권·조기상환청구권·매도청구권 …)와
# 상품 복합어(전환상환우선주·상환전환우선주), 「(발행)」 표기를 먼저 지우고 본다. 전환가치·발행자·발행회사는 변동이 아니다.
_KIND_WORD_ROW_RE = re.compile(r"전환사채|신주인수권부사채|교환사채|우선주")
_MOVE_TAIL_STRIP_RE = re.compile(r"(?:상환전환|전환상환|조기상환|상환|전환|신주인수|교환|매도|매수)(?:청구)?권"
                                 r"|(?:상환전환|전환상환|상환|전환)우선주|\(발행\)")
_MOVE_WORD_RE = re.compile(r"(?<!미)(?<!조기)(?:상환|전환|행사|발행|증가|감소|취득|처분|대체)"
                           r"(?!권|청구|할증|가격|가액|금액|차금|우선주|사채|잔액|조건|일|예정|기간|비율|가치|자|회사)")


def _kind_movement(text: str) -> bool:
    """행 이름 한 층(공백 없앤 것)이 「상품 낱말 + 변동 낱말」 변동 행인가(「전환사채 상환」·「상환전환우선주 발행」)."""
    for m in _KIND_WORD_ROW_RE.finditer(text or ""):
        tail = _MOVE_TAIL_STRIP_RE.sub("", (text[m.end():]).split("/")[0])
        if _MOVE_WORD_RE.search(tail):
            return True
    return False


def _non_balance_cell(cell: dict) -> bool:
    """잔액 표가 아닌 표의 칸인가(만기분석·유동성위험·신용위험·이연법인세·상계·담보 제목, 현금유입·유출·거래상대방 머리글,
    만기 구간·계약상 현금흐름 열). 표 전체를 보는 규칙(만기 구간 열이 있는 표의 합계 열)은 _non_balance_ids."""
    ctx = re.sub(r"\s+", "", _title_core(cell.get("table_title")) + "|" + (cell.get("note_title") or ""))
    if _NON_BALANCE_TITLE_RE.search(ctx):
        return True
    if _maturity_bucket_cell(cell):
        return True
    if any(_kind_movement(_norm_label(p)) for p in [*(cell.get("label_path") or []), cell.get("label") or ""]):
        return True  # 「전환사채 상환」 변동 행
    col = re.sub(r"\s+", "", cell.get("col_header") or "")
    labels = [_norm_label(p) for p in (cell.get("label_path") or [])] + [_norm_label(cell.get("label"))]
    if _NON_BALANCE_COL_RE.search(col) or any(_NON_BALANCE_ROW_RE.search(t) for t in labels):
        return True
    return any(_COUNTERPARTY_COL_RE.match(seg) for seg in col.split("/"))


def _table_of(cid: str) -> str:
    return cid.rsplit(":r", 1)[0]


def _non_balance_ids(cells: dict) -> set[str]:
    """잔액이 아닌 칸 id. 칸 하나로 보는 규칙(_non_balance_cell)에 더해, 만기 구간 열이 하나라도 있는 표는 장부금액
    열이 아닌 모든 칸(「합계」 열 = 할인하지 않은 현금흐름 합)을 뺀다."""
    maturity_tables = {_table_of(i) for i, c in cells.items() if _maturity_bucket_cell(c)}
    return {i for i, c in cells.items()
            if _non_balance_cell(c) or (_table_of(i) in maturity_tables
                                        and not any(_CARRYING_COL_RE.search(seg) for seg in _col_segments(c)))}


_MATURITY_COL_RE = re.compile(r"개월|\d+년|만기|현금흐름|이내|초과")  # 만기분석 열은 장부금액이 아니다
_MOVE_START_RE = re.compile(r"^(?:기초|발행|상환|전환|인식|제거)(?!권|청구권|우선주|사채)")
_SERIES_TAG_RE = re.compile(r"^제?(\d+(?:-\d+)?)(?:회차|회|차|종)")
_SERIES_ANY_RE = re.compile(r"(?:^|[^\d])제?(\d+(?:-\d+)?)(?:회차|회|차|종)|[Ss]eries([A-Z0-9]+)")
_SENT_ID_RE = re.compile(r"(?:^|:)s:?(\d+)$")
_DERIV_RE = re.compile(r"(?<!비)파생|전환권\s*\(?\s*부채")  # 「비파생금융부채」는 파생이 아니다
_NEGATION_RE = re.compile(r"^[^.,。]{0,25}?(않|아니|없)")
_DERIV_AMOUNT_KEYS = {"derivative_liability": "liability", "derivative_asset": "asset"}
_NON_DERIV_KEYS = ("face", "host_carrying", "equity_component")


def _derivative_row(cell: dict) -> bool:
    """행 이름(어느 층이든)이 파생상품 행인가(「파생상품부채」 — face·host_carrying·equity_component 근거가 아니다)."""
    return any(_DERIV_RE.search(_norm_label(p)) for p in [*(cell.get("label_path") or []), cell.get("label") or ""])
_SERIES_RE = re.compile(r"제\s*(\d+)\s*회")
_ENUMS = {"option_class": OPTION_CLASSES, "embedded_separation": EMBEDDED_SEPARATIONS}


def _norm_label(label: str | None) -> str:
    return re.sub(r"\s+", "", re.sub(r"\(\*\d*\)|\*\d*\)?$", "", label or ""))


def _is_host_cell(cell: dict) -> bool:
    return bool(_HOST_RE.search(_norm_label(cell.get("label"))))


def _words_side(text: str) -> str | None:
    liab, asset = "부채" in text, "자산" in text
    if liab == asset:
        return None
    return "liability" if liab else "asset"


# 「파생…(자산|부채)」 바로 뒤에 다른 쪽이 오면 두 쪽이다 — 이음은 낱말(및·과·와·또는)이나 글자가 아닌 기호 아무것
# (「,」「·」「ㆍ」(U+318D)「∙」「/」「(」), 또는 아무것도 없음(「파생상품자산부채」·「파생금융자산(부채)」·「파생상품부채(자산)」).
_SIDE_SEP = r"(?:[^가-힣A-Za-z0-9]{0,3}(?:및|과|와|또는)?[^가-힣A-Za-z0-9]{0,3})"
_BOTH_SIDES_RE = re.compile(r"(?<!비)파생(?:금융)?(?:상품)?" + _SIDE_SEP + r"(자산|부채)" + _SIDE_SEP
                            + r"(?:파생(?:금융)?(?:상품)?)?" + _SIDE_SEP + r"(자산|부채)")


def _names_both_sides(text: str | None) -> bool:
    text = re.sub(r"\s+", "", text or "")
    return any(m.group(1) != m.group(2) for m in _BOTH_SIDES_RE.finditer(text))


def _deriv_title_side(text: str | None) -> str | None:
    """「…파생상품부채의 내역」처럼 파생(상품|금융상품|금융)?(부채|자산)의 언급으로 쪽을 정한다(제목 앞에 문단이 붙어 오면
    마지막 언급). 제목이 두 쪽을 다 말하면(「파생상품자산 및 부채」·「파생상품 자산 및 부채」·「파생상품자산과
    파생상품부채」, 앞 문단의 「…파생상품자산으로 인식」과 뒤 「파생상품부채의 내역」) 쪽을 모른다(R14)."""
    text = re.sub(r"\s+", "", text or "")
    if _names_both_sides(text):
        return None
    found = list(_SIDE_DERIV_RE.finditer(text))
    if not found or len({m.group(1) for m in found}) > 1:
        return None
    last = found[-1]
    for prev in found[-2::-1]:
        if prev.group(1) != last.group(1) and last.start() - prev.end() <= _JOINED_GAP:
            return None
    return "liability" if last.group(1) == "부채" else "asset"


def _option_term(text: str) -> bool:
    """옵션 낱말이 있는가. 상품 이름 안의 글자(「신주인수권부사채」의 신주인수권)는 옵션이 아니다."""
    text = _INSTRUMENT_RE.sub("", text or "")
    return bool(_OPTION_RE.search(text)) and not _OPTION_EXCLUDE_RE.search(text)


def _deriv_side(cell: dict) -> str | None:
    """파생상품 칸이면 "liability"·"asset", 쪽을 모르거나 파생상품 칸이 아니면 None(R14: 거짓 수락보다 거부).

    부채요소(HOST) 행·합계 행·「상품 및 옵션」 결합 행은 언제나 None(Review Focus 3). 행 이름이나 열 머리글이 파생·옵션을
    말해야 파생 칸이고, 쪽은 근거가 있어야 한다.
    - 행 이름에 「파생」(「…내재파생상품」·「파생상품부채」): 파생 부분 낱말 → 상품 이름이 아닌 행 이름 층의 낱말
      → 열 머리글의 파생…(부채|자산) → 표·주석 제목의 명시적 파생상품(부채|자산).
    - 옵션 낱말 행(전환권·조기상환권·매도청구권 …, 조정·대가·가치·자본 제외): 열 머리글 → 제목의 명시적 파생상품(부채|자산).
    - 그 밖의 행(기말 행·상품 이름 행 등): 열 머리글이 「파생상품부채」·「파생상품자산」을 명시할 때만.
    손익·변동·민감도 행·열(평가이익, 발행, 행사, 1% 증가 …)과 상품·파생을 합친 열은 잔액이 아니므로 언제나 None.
    제목의 일반 「부채」 낱말(부채요소·금융부채)이나 옵션 이름 기본값으로는 쪽을 정하지 않는다.
    """
    label = _norm_label(cell.get("label"))
    if _is_host_cell(cell) or _TOTAL_RE.match(label) or cell.get("region_kind") == "statement_cf":
        return None  # 현금흐름표 줄(「파생상품자산의 정산」)은 잔액이 아니다
    if _non_balance_cell(cell):
        return None  # 만기분석·신용위험·이연법인세·상계·담보·거래상대방 표
    parts = [_norm_label(p) for p in (cell.get("label_path") or [cell.get("label") or ""])]
    if any(_JOINED_INSTRUMENT_RE.search(p) for p in parts):
        return None  # 「제13회 전환사채 및 조기상환권」 — 상품 전체와 옵션을 함께 적은 행(옵션만의 금액이 아니다)
    col = re.sub(r"\s+", "", cell.get("col_header") or "")
    if (any(_NOT_BALANCE_RE.search(p) or _MOVE_START_RE.match(p) for p in [label, *parts])
            or _NOT_BALANCE_RE.search(col) or _MATURITY_COL_RE.search(col)
            or any(_MOVE_START_RE.match(seg) for seg in col.split("/"))):
        return None  # 손익·변동·민감도 칸은 파생상품 잔액이 아니다
    if (_JOINED_INSTRUMENT_RE.search(col) or _LEVEL_OR_RESTATE_COL_RE.search(col)
            or any(_OTHER_COL_RE.match(seg) for seg in col.split("/"))):
        return None  # 상품과 파생을 합친 열, 공정가치 수준 열, 회계오류 수정·재작성 열
    if any(_OTHER_CATEGORY_RE.search(seg) and not _OWN_CATEGORY_RE.search(seg) for seg in col.split("/")):
        return None  # 범주 행렬의 다른 범주 열(「파생상품부채」 × 「상각후원가측정금융부채」)
    tt, nt = cell.get("table_title") or "", cell.get("note_title") or ""
    title_deriv = _deriv_title_side(tt) or _deriv_title_side(nt)
    col_side = _deriv_title_side(col) or (_words_side(col) if _DERIV_RE.search(col) else None)
    deriv_parts = [p for p in parts if _DERIV_RE.search(p)]
    if deriv_parts:
        if any(_names_both_sides(p) for p in deriv_parts):
            return None  # 「파생상품자산(부채)」 행 — 쪽을 모른다
        plain = [p for p in parts if not _INSTRUMENT_RE.search(p)]
        return _words_side(" ".join(deriv_parts)) or _words_side(" ".join(plain)) or col_side or title_deriv
    if _option_term(label) or any(_option_term(p) for p in parts if not _INSTRUMENT_RE.search(p)):
        return col_side or title_deriv
    if (_DERIV_RE.search(col) or _option_term(col)) and _COL_ROW_RE.search(label):
        return _deriv_title_side(col)  # 기말·상품·회차 행: 열 머리글이 쪽까지 말해야 한다
    return None


def _sentence_around(text: str, start: int, end: int) -> str:
    """일치 범위를 담은 한 문장(앞 「다.」·「.」 뒤부터 뒤 「.」까지)."""
    a = max(text.rfind(".", 0, start), text.rfind("。", 0, start)) + 1
    b = min([i for i in (text.find(".", end), text.find("。", end)) if i >= 0] or [len(text)])
    return text[a:b]


# T9c(R15): option_class 옵션 낱말 지도. 전환권 → CB·RCPS(전환우선주 포함), 신주인수권 → BW, 교환권 → EB.
# 상환권·조기상환·콜·풋 같은 일반 옵션 낱말은 option_class 에서 어느 종류에도 가지 않는다.
_R15_OPTION_MAP = ((re.compile(r"전환권(?!조정|대가|가치)"), frozenset({"CB", "RCPS"})),
                   (re.compile(r"신주인수권(?!부|조정|대가|가치)"), frozenset({"BW"})),
                   (re.compile(r"교환권(?!조정|대가|가치)"), frozenset({"EB"})))


def _r15_kinds(text: str | None) -> set[str]:
    """글의 전환권·신주인수권·교환권이 가리키는 종류(상품 이름 안의 글자·조정·대가·가치는 아니다)."""
    t = _INSTRUMENT_RE.sub("", re.sub(r"\s+", "", text or ""))
    out: set[str] = set()
    for rx, ks in _R15_OPTION_MAP:
        if rx.search(t):
            out |= ks
    return out


def _option_liability_spans(text: str) -> list[tuple[int, int, set[str]]]:
    """전환권·신주인수권·교환권(R15)을 파생상품부채(파생상품)로 분류·인식했다는 일치들: (시작, 끝, 그 옵션 목록의 R15 종류).
    그 옵션이 든 목록이 주어이거나 그 목록을 담은 「내재파생상품」이 주어여야 한다. 부정·조건(「…경우」, 앞이든 「…분류된
    경우로서」처럼 뒤든)·자산, 틈 안의 다른 주어(풋·콜·상품)·자본 분류는 아니다."""
    text = text or ""
    if _CONV_TO_EQUITY_RE.search(text):
        return []
    out = []
    for m in (m for rx in _OPTION_LIAB_SENT_RES for m in rx.finditer(text)):
        if "자산" in _sentence_around(text, m.start(), m.end()) or _NEGATION_RE.search(text[m.end():]) or _GAP_OTHER_SUBJECT_RE.search(m.group("gap")):
            continue
        if _CONDITIONAL_RE.search(text[:m.end()]) or _CONDITIONAL_RE.search(text[m.end():m.end() + 12]):
            continue
        out.append((m.start(), m.end(), _r15_kinds(text[m.start():m.start("gap")])))
    return out


def _option_liability_sentence(text: str) -> bool:
    """_option_liability_spans 가 하나라도 있는 문장."""
    return bool(_option_liability_spans(text))


def _positive_deriv_liability_sentence(text: str) -> bool:
    return _option_liability_sentence(text)


def _period_ok(cell: dict, target: str, n_value_cols: int | None = None) -> bool:
    """balance.<target>.* 금액이 이 칸의 기간과 맞는가. target 은 current_end·prior_end.
    - 칸 기간이 target 이면 맞다.
    - current_period(prior_period) 칸은 열 머리글이 잔액(장부금액·잔액·기말)을 말하거나, 행이 기말·장부가액 행이거나,
      표 바로 앞 문장이 「…현재 …의 내역」 같은 시점 잔액 표(변동·손익·「당기 중」 아님)일 때만 current_end(prior_end) 로
      읽는다.
    - 기간을 모르는 칸(None·unknown)은 표의 값 열이 하나일 때만 맞다."""
    p = cell.get("period")
    if p == target:
        return True
    if p in (None, "", "unknown"):
        # 값 열 하나인 잔액 표이고 제목·주석 제목이 당기말·현재를 말하며 사건 시점·평가 표가 아닐 때, current_end 에만.
        if target != "current_end" or (cell.get("n_value_cols") if n_value_cols is None else n_value_cols) != 1:
            return False
        ctx = re.sub(r"\s+", "", (cell.get("table_title") or "") + "|" + (cell.get("note_title") or ""))
        if "전기말" in ctx and "당기말" not in ctx:
            return False  # 「전기말 현재 …」 표는 current_end 가 아니다
        return bool(_UNKNOWN_OK_RE.search(ctx)) and not _EVENT_TITLE_RE.search(ctx)
    if p == target.replace("_end", "_period"):
        col = re.sub(r"\s+", "", cell.get("col_header") or "")
        if _BALANCE_COL_RE.search(col) or _END_ROW_RE.search(_norm_label(cell.get("label"))):
            return True
        tail = _title_tail(cell.get("table_title"))
        return bool(_STATUS_TITLE_RE.search(tail) and not _FLOW_TITLE_RE.search(tail))
    return False


# 종류별 전환권(신주인수권·교환권) 자본요소 행 이름. 파생·부채 낱말이 든 행(「전환권대가(파생상품금융부채)」)은 아니다.
_EQUITY_OPTION_RE = {
    "CB": re.compile(r"전환권대가|전환권의?자본요소|전환사채의?자본요소"),
    "RCPS": re.compile(r"전환권대가|전환권의?자본요소|우선주의?자본요소"),
    "BW": re.compile(r"신주인수권대가|신주인수권의?자본요소|신주인수권부사채의?자본요소"),
    "EB": re.compile(r"교환권대가|교환권의?자본요소|교환사채의?자본요소"),
}


def _record_series(rec: dict) -> str | None:
    m = re.match(r"^[A-Z]+-(\d+(?:-\d+)?|[A-Z0-9]+)$", rec.get("series_key") or "")
    return m.group(1) if m else None


# 상품 종류 낱말(T9b). 「신주인수권부사채」·「교환사채」·「전환사채」·「우선주」(상환전환·전환상환·전환우선주 모두 RCPS).
_KIND_WORD_RES = (("CB", re.compile(r"전환사채|(?<![A-Za-z])CB(?![A-Za-z])")),
                  ("BW", re.compile(r"신주인수권부사채|(?<![A-Za-z])BW(?![A-Za-z])")),
                  ("EB", re.compile(r"교환사채|(?<![A-Za-z])EB(?![A-Za-z])")),
                  ("RCPS", re.compile(r"우선주|(?<![A-Za-z])(?:RCPS|CPS|RPS)(?![A-Za-z])")))


def _text_kinds(text: str | None) -> set[str]:
    text = re.sub(r"\s+", "", text or "")
    return {k for k, rx in _KIND_WORD_RES if rx.search(text)}


def _cell_kinds(cell: dict) -> set[str]:
    """칸의 행 이름(어느 층이든)·열 머리글이 말하는 상품 종류."""
    out: set[str] = set()
    for t in [*(cell.get("label_path") or []), cell.get("label") or "", cell.get("col_header") or ""]:
        out |= _text_kinds(t)
    return out


def _series_fits(cell: dict, series: str | None, kind: str | None = None) -> bool:
    """칸의 회차(행 이름·label_path)가 없거나 기록 회차와 같으면 True. 기록 회차를 모르면 회차가 붙은 칸은 맞지 않는다.
    칸의 행 이름·열 머리글이 상품 종류를 말하면 기록 종류도 그 안에 있어야 한다(T9b; 말하지 않으면 번호만 본다)."""
    if kind is not None:
        kinds = _cell_kinds(cell)
        if kinds and kind not in kinds:
            return False
    tags = _cell_series(cell)
    return not tags or (series is not None and series in tags)


def _equity_conflict(rec: dict, cells: dict) -> bool:
    """입력에 그 종류의 자본요소 칸(0 아님)이 있고 그 회차가 기록 회차와 같거나 모르면 True — 그 전환권은 (어느 시점이든)
    자본이었으므로 파생상품부채라고 확인하지 않는다(R14)."""
    rx = _EQUITY_OPTION_RE.get(rec.get("kind"))
    series = _record_series(rec)
    for c in cells.values():
        label = " ".join(_norm_label(p) for p in (c.get("label_path") or [c.get("label") or ""]))
        if not rx or not rx.search(label) or re.search(r"파생|부채", label):
            continue
        v = _num(c.get("value"))
        if not v:
            continue
        tags = _cell_series(c)
        if not tags or series is None or series in tags:
            return True
    return False


def _cell_series(cell: dict) -> frozenset:
    """행 이름과 label_path 의 회차 번호들(「제7회 …」, 「Series A」)."""
    out = set()
    for t in [*(cell.get("label_path") or []), cell.get("label") or ""]:
        for m in _SERIES_ANY_RE.finditer(_norm_label(t)):
            out.add(m.group(1) or m.group(2))
    return frozenset(out)


def _amount_fields(rec: dict) -> list[tuple[str, dict, str]]:
    """[(필드 이름, 담은 dict, 키)]."""
    out = []
    for period, vals in (rec.get("balance") or {}).items():
        if isinstance(vals, dict):
            out.extend((f"balance.{period}.{k}", vals, k) for k in vals)
    sep = rec.get("separated_amount")
    if isinstance(sep, dict):
        out.extend((f"separated_amount.{k}", sep, k) for k in sep)
    return out


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _series_key(rec: dict, seq: int) -> str:
    name = rec.get("instrument")
    m = _SERIES_RE.search(name if isinstance(name, str) else "")
    return f"{rec['kind']}-{m.group(1)}" if m else f"{rec['kind']}-?{seq}"


def _coerce_shapes(rec: dict) -> None:
    """LLM 이 형을 틀리게 준 칸을 안전한 빈 형으로 바꾼다(값이 틀린 것은 아래 규칙이 다룬다)."""
    if not isinstance(rec.get("evidence"), dict):
        rec["evidence"] = {}
    if not isinstance(rec.get("movement"), dict):
        rec["movement"] = {}
    bal = rec.get("balance")
    rec["balance"] = {k: v if isinstance(v, dict) else {} for k, v in bal.items()} if isinstance(bal, dict) else {}
    if "separated_amount" in rec and not isinstance(rec["separated_amount"], dict):
        del rec["separated_amount"]
    if not isinstance(rec.get("separated_targets"), list):
        rec["separated_targets"] = []
    if not isinstance(rec.get("series_key"), str):
        rec["series_key"] = ""


def _canonical_id(i, cells: dict, sentences: dict) -> str | None:
    """입력에 있는 id 면 그대로. 문장 id 를 「s:494」·「<접수번호>:00761:s997」처럼 적은 것은 「s494」·「s997」이
    입력에 있을 때만 그 id 로 읽는다(없는 id 를 만들지 않는다)."""
    if not isinstance(i, str):
        return None
    if i in cells or i in sentences:
        return i
    m = _SENT_ID_RE.search(i)
    if m and f"s{m.group(1)}" in sentences:
        return f"s{m.group(1)}"
    return None


def _component_label(cell: dict) -> str:
    return re.sub(r"\(.*?\)", "", _norm_label(cell.get("label")))


def _host_sum(ids: list[str], cells: dict, amount: float, want_unit: str) -> list[str] | None:
    """host_carrying 이 인쇄된 칸 하나가 아니라 부채요소 구성 칸의 합일 때(R14로 좁힘).
    - 댄 id 가 모두 입력에 있고 2개 이상, v 칸(글 표 금액)은 쓰지 않는다.
    - 모두 같은 표·같은 열·같은 단위·같은 배수.
    - 모든 칸의 행 이름이 부채요소 구성 목록(액면·원금·사채·우선주부채 원금, 사채할인발행차금, 전환권조정,
      신주인수권조정, 교환권조정, 상환할증금, 현재가치할인차금)에 들고, 회차 번호가 둘 이상 섞이지 않는다.
    - 인쇄된 값(차감은 음수) 합이 금액과 같다(허용 오차 1)."""
    cs = [(i, cells[i]) for i in ids if i in cells]
    if len(cs) < 2 or len(cs) != len(ids) or any(":v" in i.rsplit(":r", 1)[-1] for i, _c in cs):
        return None
    groups = {(i.rsplit(":r", 1)[0], i.rsplit(":", 1)[1]) for i, _c in cs}
    if len(groups) != 1 or any(c.get("unit_kind") != want_unit for _i, c in cs):
        return None
    if len({c.get("multiplier") for _i, c in cs}) != 1:
        return None
    labels = [_component_label(c) for _i, c in cs]
    if not all(_HOST_COMPONENT_RE.match(lb) for lb in labels):
        return None
    series = {_cell_series(c) for _i, c in cs}
    if len(series) != 1 or len(next(iter(series))) > 1:
        return None  # 모든 칸이 같은 회차 하나(회차는 행 이름이나 label_path 부모에서)
    vals = [_num(c.get("value")) for _i, c in cs]
    if any(v is None for v in vals) or abs(sum(vals) - amount) > AMOUNT_TOLERANCE:
        return None
    return [i for i, _c in cs]



# ---- 규칙 8: embedded_separation 근거(T9b, R14) ----
# 분리하지 않았다는 말: 「분리하지 않」·「분리되지 않」·「분리요건을 (모두) 충족하지 못」·「분리하여 계상하고 있지 않」·「비분리」
# (「비분리형 신주인수권부사채」는 신주인수권 분리 여부를 말하는 상품 이름이라 아니다).
_NEG_SEP_RE = re.compile(
    r"분리\s*(?:하지|되지|할\s*수\s*없|대상이\s*아니|되는\s*것이\s*아니)"
    r"|분리\s*(?:요건|조건)[^.。]{0,10}?(?:충족|만족)\s*(?:하지|되지|시키지)\s*(?:못|않)"
    r"|분리\s*(?:하여|해서)?\s*(?:별도로?\s*)?(?:계상|인식|회계처리|평가)\s*(?:하고\s*있)?지\s*(?:않|아니)"
    r"|비분리(?!형)")
# 분리했다는 동사 꼴만(「분리하여」·「분리하였」·「분리되는」 …). 「분리요건을 만족하는 …」·「분리금액」은 분리 진술이 아니다.
_SEP_WORD_RE = re.compile(r"분리\s*(?:하여|해서|하고|하였|했|하며|한|된|되어|되었|되며|되는|처리)")
_SEP_NEAR_RE = re.compile(r"파생|공정가치")
_EQUITY_SPLIT_RE = re.compile(r"자본부분|자본요소|전환권대가|신주인수권대가|교환권대가|자본으로|자본잉여금")
# fix round 5: 분리 동사는 제 마디(「,」·「…고 」·「…며 」로 나눈 조각)에 묶는다. 자본 분리 마디는 다른 마디가 「파생」을 말해도
# 분리가 아니다. 「분리하여 공시(표시)하지 않」은 분리 진술이 아니다(다른 마디가 정한다).
_CLAUSE_BREAK_RE = re.compile(r"[,，]|(?<=[가-힣])(?:고|며)(?=\s)")
_SEP_NOT_SHOWN_RE = re.compile(r"\s*(?:별도로?\s*)?(?:공시|표시|기재)\s*(?:하지|되지|하고\s*있지)\s*(?:않|아니)")


def _clause_span(text: str, start: int, end: int) -> tuple[int, int]:
    a = max([m.end() for m in _CLAUSE_BREAK_RE.finditer(text, 0, start)] or [0])
    nxt = _CLAUSE_BREAK_RE.search(text, end)
    return a, (nxt.start() if nxt else len(text))


def _clause(text: str, start: int, end: int) -> str:
    a, b = _clause_span(text, start, end)
    return text[a:b]


# T9c: 종류 낱말은 동사(분리 동사·옵션 부채 분류)가 든 마디에서만 읽는다(_verb_kinds).
# - 그 마디가 종류 낱말을 말하면 그것만 센다(「전환우선주부채등은 모두 보통주로 전환되었으며, 당기말 현재 전환사채 관련
#   조기상환권은 … 분리」 → CB 만).
# - 마디가 종류를 말하지 않고 동사 앞에 제 주제어(옵션·상품·회사 + 은/는)가 있으면 아무 종류도 아니다(「상환전환우선주부채는 …
#   구분되며, …, 조기상환권 및 전환권은 파생상품으로 인식」 — 앞 마디의 종류는 묶지 않는다).
# - 마디가 종류도 제 주제어도 없으면 동사 바로 앞의 주어(문장 앞쪽의 마지막 「… 은/는/이/가」 구, 그 마디 처음부터)가 동사의
#   주어이므로 그 구의 종류를 센다(「상기 전환상환우선주가 …하였으며, 전환권 및 조기상환권을 … 분리하고」, 「전환상환우선주에
#   내재된 전환권 및 조기상환권은 …하였으며, 전환권 및 조기상환권이 … 분리하여」, 「…전환사채에 내재된 전환권은, 주계약과
#   분리하여」). 앞 마디의 목적어(「당사는 상환전환우선주를 발행하였으며, 전환권을 분리하여」)는 묶지 않는다.
_SUBJECT_HEAD = (r"(?:" + _OPT_ANY + r"|전환사채|신주인수권부사채|교환사채|우선주(?:부채)?|복합금융상품|금융상품"
                 r"|내재파생상품|파생상품|당사|회사|연결회사|지배기업|지배회사|연결실체|연결기업|동사)(?:\s*등)?\s*")
_TOPIC_RE = re.compile(_SUBJECT_HEAD + r"(?:은|는)(?=[\s,，]|$)")
_SUBJECT_RE = re.compile(_SUBJECT_HEAD + r"(?:은|는|이|가)(?=[\s,，]|$)")


# 종류 묶기의 마디 나눔은 _CLAUSE_BREAK_RE 와 같되, 옵션·상품 낱말 나열의 쉼표(「전환권, 조기상환청구권 및 매도청구권」·
# 「전환사채, 신주인수권부사채」)는 마디를 나누지 않는다. 마침표(「3.16」처럼 숫자 사이가 아닌 것)도 나눈다 — 1층이 각주에
# 붙여 온 뒷 문장(「…분리되었습니다.(5) 당기 및 전기의 전환상환우선주 변동내역은 …」)의 종류는 동사에 묶이지 않는다.
_LIST_COMMA_BEFORE_RE = re.compile(r"(?:" + _OPT_ANY + r"|전환사채|신주인수권부사채|교환사채|우선주(?:부채)?"
                                   r"|(?<![A-Za-z])(?:CB|BW|EB|RCPS))\s*$")


_KIND_BREAK_RE = re.compile(_CLAUSE_BREAK_RE.pattern + r"|(?<!\d)[.。]|[.。](?!\d)")


def _kind_breaks(text: str, endpos: int | None = None) -> list[re.Match]:
    end = len(text) if endpos is None else endpos
    return [m for m in _KIND_BREAK_RE.finditer(text, 0, end)
            if not (m.group(0) in ",，" and _LIST_COMMA_BEFORE_RE.search(text, 0, m.start()))]


def _kind_clause_span(text: str, start: int, end: int) -> tuple[int, int]:
    a = max([m.end() for m in _kind_breaks(text, start)] or [0])
    nxt = [m.start() for m in _kind_breaks(text) if m.start() >= end]
    return a, (min(nxt) if nxt else len(text))


def _verb_kinds(text: str, spans) -> set[str]:
    """동사 일치(spans: (시작, 끝, …))가 든 마디의 종류 낱말. 마디가 종류도 제 주제어도 없으면 동사 바로 앞 주어 구의 종류."""
    out: set[str] = set()
    for sp in spans:
        s, e = sp[0], sp[1]
        a, b = _kind_clause_span(text, s, e)
        own = _text_kinds(text[a:b])
        out |= own
        if own or _TOPIC_RE.search(text, a, e):
            continue  # 마디가 종류를 말하거나 제 주제어(동사 앞)가 있다
        topics = list(_SUBJECT_RE.finditer(text, 0, a))
        if topics:
            t = topics[-1]
            ta = max([m.end() for m in _kind_breaks(text, t.start())] or [0])
            out |= _text_kinds(text[ta:t.end()])
    return out
# 보유자 쪽(취득한 상품의 옵션, 금융자산)은 회사 자신의 상품 분리가 아니다. 제3자 지정 콜을 파생상품자산으로 잡은 문장은
# 분리 동사가 없고(「구분 계상」) 옵션 경로가 자산 끝을 받지 않으므로 따로 막지 않는다(「…풋과 (제3자) 콜을 분리하여」는 분리).
# T9c: 증권 보유자 낱말(매도가능증권·단기매매증권·만기보유증권·유가증권(「유가증권시장」 아님)·당기손익인식금융자산·
# 매도가능금융자산·기타포괄손익-공정가치측정금융자산). 확인하는 쪽(_HOLDER_RE·_HOLDER_WORD_RE)에만 더하고, 막는 쪽(섞인
# 부정 문장·not_separated·whole_fvpl 모순 검사)은 이전 낱말(_HOLDER_BASE_RE)로 읽어 느슨해지지 않게 한다.
_HOLDER_SEC = (r"매도가능\s*증권|단기매매\s*증권|만기보유\s*증권|유가증권(?!\s*시장)|당기손익\s*인식\s*금융자산"
               r"|매도가능\s*금융자산|기타포괄손익\s*[-‐–]?\s*공정가치\s*측정\s*금융자산")
_HOLDER_BASE_RE = re.compile(r"금융자산|취득한|취득하여|투자한")
_HOLDER_RE = re.compile(_HOLDER_BASE_RE.pattern + "|" + _HOLDER_SEC)
_SEP_CONDITIONAL_RE = re.compile(r"경우|때에는|때는|라면")
# fix round 4(R17 「자기 회사」): separated 를 확인하는 분리 문장은 발행자 쪽만이다. 보유자 낱말 — 발행회사에 대하여,
# 행사할 수 있는 권리, 보유(하고 있는·한·중 …), 취득(시점·한·하여 …), 대출채권, 매매목적, 투자(투자자 제외),
# 금융자산(으로 분류·인식, 당기손익-공정가치측정금융자산) — 이 든 문장은 아니다. 「사채권자가 보유한 …」처럼 보유의 주어가
# 사채권자·투자자이면 발행자 문장이라 막지 않는다.
_HOLDER_WORD_RE = re.compile(r"발행회사에\s*대하여|행사할\s*수\s*있는\s*권리|보유\s*(?:하고\s*있|한|중|하는|하여|목적)"
                             r"|취득|대출채권|매매\s*목적|투자(?!자)|금융자산|" + _HOLDER_SEC)
_HOLDER_SUBJECT_BEFORE_RE = re.compile(r"(?:권자|투자자|보유자)(?:가|는|이|들이|들은)?\s*$")
# 보유자 진술 쪽(넓게): 보유의 주어가 남(제3자·주주·기업·특수관계자)이거나 목적어가 다른 것(「의결권을 보유한」).
_STATEMENT_SUBJECT_BEFORE_RE = re.compile(r"(?:권자|투자자|보유자|제\s*3\s*자|제삼자|주주|(?<!지배)(?<!연결)기업|특수관계자)"
                                          r"(?:가|는|이|들이|들은)?\s*$|(?:을|를)\s*$")
# 「…상환전환우선주를 보유한 투자자가」 — 보유의 주어가 뒤에 오는 관계절(투자자·주주·사채권자)
_HOLDER_SUBJECT_AFTER_RE = re.compile(r"^\s*(?:한|하는|하고\s*있는|중인)\s*(?:투자자|주주|사채권자|보유자|자(?:가|는|의))")
# 공시 단위 보유자 진술 — 상품이 보유·취득·투자의 목적어이거나 자산으로 분류된 문장:
# 「…전환사채 및 신주인수권부사채를 매매목적으로 보유」, 「…해외전환사채 및 신주인수권을 보유」, 「보유하고 있는 전환사채」·
# 「취득한 전환사채」, 「기타대출채권으로 분류된 전환사채」, 「… 상환전환우선주는 … 장기투자자산으로 분류」.
# 희석주당이익 문장(「회사가 보유하고 있는 희석성 잠재적보통주로는 전환상환우선주」), 자기 회사 상품(「…기업이 보유하고 있는
# 연결회사의 전환사채」), 「…권리를 보유」처럼 상품이 목적어가 아닌 문장은 아니다.
# 우선주는 전환·상환이 붙은 것만(「보통주와 의결권이 있는 우선주를 합산한 보유주식수」는 주주 지분이다).
_PREF = r"(?:상환전환|전환상환|상환|전환)우선주"
_KIND_OBJ = r"(?:전환사채|신주인수권부사채|교환사채|" + _PREF + r"|신주인수권(?!부))"
_KIND_NAME = r"(?:전환사채|신주인수권부사채|교환사채|" + _PREF + r")"
_HOLDER_STATEMENT_RES = (
    re.compile(_KIND_OBJ + r"(?:\s*등)?\s*(?:을|를)\s*[^.。]{0,15}?(?:보유(?!자)|취득|투자(?!자))"),
    re.compile(r"(?:보유|취득|투자)\s*(?:하고\s*있는|하고\s*있던|한|중인)\s*(?:[^.。\s]{1,12}\s*){0,2}?" + _KIND_NAME),
    re.compile(r"(?:금융자산|투자자산|대출채권|증권)\s*(?:으로|로)\s*(?:분류|계상)\s*(?:된|되는|한|하고\s*있는)\s*[^.。]{0,30}?"
               + _KIND_NAME),
    re.compile(_KIND_NAME + r"(?:\s*등)?\s*(?:은|는|을|를)\s*(?P<gap>[^.。]{0,120}?)(?:금융자산|투자자산|대출채권|증권)\s*(?:으로|로)\s*"
               r"(?:분류|인식|계상|지정)"),
    re.compile(r"매매\s*목적"),
)
_HOLDER_STATEMENT_SKIP_RE = re.compile(r"희석|잠재적\s*보통주")
_OWN_INSTRUMENT_RE = re.compile(r"(?<![가-힣])(?:당사|회사|연결회사|지배기업|지배회사|연결실체)\s*(?:의|가\s*발행한|이\s*발행한)\s*"
                                + _KIND_NAME)
_GAP_OPTION_RE = re.compile(r"콜옵션|풋옵션|매도청구권|매수청구권|조기상환|전환권|신주인수권(?!부)|교환권")
# 자기 회사를 발행자로 적은 말(「당사가 발행한」·「연결회사가 발행한」·「회사는 … 발행하였」). 「발행회사」·「B사」는 아니다.
_ISSUER_NAMED_RE = re.compile(r"(?:(?<![가-힣])|(?<=사채)|(?<=우선주)|(?<=복합금융상품))(?:당사|회사|연결회사|지배기업|지배회사|연결실체|연결기업)"
                              r"(?:가|는|이|은|의|에서)?\s*[^.。]{0,25}?발행\s*(?:한|하였|했)")


def _holder_hits(rx: re.Pattern, text: str) -> bool:
    """rx 일치 중 보유의 주어가 사채권자·투자자·보유자가 아닌 것이 있는가."""
    for m in rx.finditer(text):
        if m.group(0).startswith("보유") and _HOLDER_SUBJECT_BEFORE_RE.search(text[max(0, m.start() - 12):m.start()]):
            continue
        return True
    return False


def _holder_wording(text: str) -> bool:
    return _holder_hits(_HOLDER_WORD_RE, text or "")


def _holder_statement(text: str) -> bool:
    """회사가 남의 전환사채·신주인수권부사채·우선주를 보유(취득·투자)한다는 진술(위 _HOLDER_STATEMENT_RES)."""
    text = text or ""
    if not _text_kinds(text) or _HOLDER_STATEMENT_SKIP_RE.search(text):
        return False
    for rx in _HOLDER_STATEMENT_RES:
        for m in rx.finditer(text):
            if m.group(0).startswith(("보유", "취득", "투자")):
                if _STATEMENT_SUBJECT_BEFORE_RE.search(text[max(0, m.start() - 12):m.start()]):
                    continue
            elif _HOLDER_SUBJECT_AFTER_RE.search(text[m.end():m.end() + 12]):
                continue
            if _OWN_INSTRUMENT_RE.search(m.group(0)):
                continue  # 「…보유하고 있는 연결회사의 전환사채」 — 자기 회사 상품
            if "gap" in rx.groupindex and _GAP_OPTION_RE.search(m.group("gap") or ""):
                continue  # 「전환사채에 … 콜옵션을 금융자산으로」 — 옵션이 자산이지 상품이 아니다
            return True
    return False


# 옵션(풋·콜 포함) 목록이 주어로 바로 조사를 받고 「파생상품(부채)로 분류·인식·계상·회계처리」 — 분리했다는 뜻이다.
# 파생상품자산으로 잡았다는 문장(제3자 지정 콜 등)은 분리 근거로 쓰지 않는다(그런 분리는 「분리하여」 문장이나 칸으로).
_OPT_LIST_ANY = rf"(?:{_OPT_ANY}{_JOIN})*{_OPT_ANY}(?:\s*등)?"
_DERIV_LIAB_END = (r"(?<!비)(?:파생상품부채|파생금융부채|파생상품금융부채|파생금융상품부채|파생상품|파생금융상품)(?!\s*자산)"
                   r"\s*(?:으로|로)\s*(?:분류|인식|계상|회계처리)")
_OPTION_DERIV_SENT_RES = (
    re.compile(_OPT_LIST_ANY + _MARK + _GAP + _DERIV_LIAB_END),
    re.compile(r"내재파생상품\s*\(\s*" + _OPT_LIST_ANY + r"\s*\)" + _MARK + _GAP + _DERIV_LIAB_END),
)
# 상품 전체를 당기손익-공정가치 측정 금융부채로 지정(d8). 「지정」이 아니면(계상·분류 …) 「전체」·「하나의」·「분리하지 않」이
# 같은 문장에 있어야 한다(옵션만 FVPL 부채로 분류한 문장은 분리다).
_FVPL_LIAB_RE = re.compile(r"당기손익[-‐–·]?(?:인식|공정가치)(?:로)?(?:측정|지정)?(?:하는|된)?(?:금융)?부채")
_FVPL_DESIGNATION_RE = re.compile(_FVPL_LIAB_RE.pattern + r"(?:로|으로)[^.。]{0,20}?(지정|분류|계상|인식|측정)")
_WHOLE_RE = re.compile(r"전체|하나의|분리하지않|분리되지않")
_AMORTISED_RE = re.compile(r"상각후원가")
_SENT_SERIES_RE = re.compile(r"제\s*(\d+(?:-\d+)?)\s*(?:회차|회|차|종)(?![계사])|(?<!\d)(\d+(?:-\d+)?)회차")
_OPTION_KIND_RES = ((re.compile(r"전환권"), {"CB", "RCPS"}), (re.compile(r"신주인수권(?!부)"), {"BW"}),
                    (re.compile(r"교환권"), {"EB"}))
_GENERIC_OPTION_RE = re.compile(r"조기상환|상환청구권|상환권|매도청구권|콜옵션|풋옵션|[Cc]all\s*[Oo]ption|[Pp]ut\s*[Oo]ption")


def _ties(texts: list[str], kind: str) -> bool | None:
    """글들이 이 종류의 상품을 말하는가(막는 쪽 _sentence_fits 에만 쓴다 — 넓게 묶어야 섞인 부정·모순 검사가 느슨해지지
    않는다; 확인 쪽은 _strict_fits·_issuer_option_sentence). 상품 종류 낱말이 있으면 그 안에 있어야 하고, 없으면 옵션 낱말
    (전환권 → CB·RCPS, 신주인수권 → BW, 교환권 → EB, 풋·콜·조기상환은 어느 종류든)로 본다. 둘 다 없으면 None."""
    kinds: set[str] = set()
    for t in texts:
        kinds |= _text_kinds(t)
    if kinds:
        return kind in kinds
    opt: set[str] = set()
    for t in texts:
        t = _INSTRUMENT_RE.sub("", re.sub(r"\s+", "", t or ""))
        for rx, ks in _OPTION_KIND_RES:
            if rx.search(t):
                opt |= ks
        if _GENERIC_OPTION_RE.search(t):
            opt |= set(KINDS)
    return (kind in opt) if opt else None


def _sentence_fits(text: str, kind: str, series: str | None) -> bool:
    """문장이 이 기록의 상품(종류·회차)을 말하는가. 회차가 적혀 있으면 기록 회차가 그 안에 있어야 한다."""
    if _ties([text], kind) is not True:
        return False
    tags = {m.group(1) or m.group(2) for m in _SENT_SERIES_RE.finditer(text)}
    return not tags or (series is not None and series in tags)


# fix round 5(R18): 분리 문장은 종류를 말해야 그 종류에 묶인다. 일반 옵션 낱말(매도청구권·콜·풋·조기상환)이나 전환권·
# 신주인수권·교환권만 있는 문장은 어느 종류에도 묶이지 않는다. 단, 바로 앞 문장(s<n-1>)이 한 종류만 말하고 이 문장이 그것을
# 이어 쓸 때(새 항목 표시로 시작하지 않고, 앞 문장이 「…다.」로 끝난 글이며 보유자·남의 상품·조건 문장이 아님)는 그 종류다.
_SENT_NUM_RE = re.compile(r"^s(\d+)$")
_ITEM_START_RE = re.compile(r"^\s*(?:\(\s*\*|\(\s*주|\*|\(\s*\d+\s*\)|\d+\s*[.)]|[가-하]\s*[.)]|[①-⑳]|주\s*\d|\[|[-•·ㆍ※])")


def _prev_sentence(sid: str | None, sentences: dict) -> str | None:
    """입력의 바로 앞 문단(s<n-1>). 각주(f:…)·없는 번호는 None."""
    m = _SENT_NUM_RE.match(sid or "")
    return sentences.get(f"s{int(m.group(1)) - 1}") if m else None


def _series_tags(text: str) -> set:
    return {m.group(1) or m.group(2) for m in _SENT_SERIES_RE.finditer(text or "")}


def _continues(text: str, prev: str | None, kind: str, holder: bool = False) -> bool:
    """종류 낱말이 없는 문장이 바로 앞 문장(prev)을 이어 써서 그 종류를 받는가: 앞 문장이 이 종류 하나만 말하는 「…다.」
    문장이고 보유자 낱말·남의 상품·조건(「…경우」) 문장이 아니며, 이 문장이 새 항목 표시로 시작하지 않는다."""
    return not (not prev or _ITEM_START_RE.match(text or "") or not re.search(r"다\s*\.?\s*$", prev)
                or _text_kinds(prev) != {kind} or _holder_wording(prev) or _non_self_issuer(prev, holder=holder)
                or _SEP_CONDITIONAL_RE.search(prev))  # 조건문(「…경우」)은 이어 쓸 사실이 아니다


def _strict_fits(text: str, kind: str, series: str | None, prev: str | None, spans=None, holder: bool = False) -> bool:
    """분리 문장(분리함·분리 안 함·상각후원가·FVPL 지정)이 이 기록의 상품을 말하는가(fix round 5, 확인 쪽에만 쓴다).
    문장이 종류 낱말을 말하면 기록 종류가 있어야 한다 — spans(분리 동사 일치)를 주면 동사가 든 마디와 동사 앞 주제어 구에
    (_verb_kinds, T9c), 아니면 문장 어디든. 말하지 않으면 바로 앞 문장(prev)을 이어 써야 한다(_continues). 회차는
    문장(이어 쓰면 앞 문장 포함)에 적힌 것과 같아야 한다."""
    kinds = _text_kinds(text)
    tags = _series_tags(text)
    if kinds:
        if kind not in (_verb_kinds(text, spans) if spans is not None else kinds):
            return False
    else:
        if not _continues(text, prev, kind, holder):
            return False
        tags |= _series_tags(prev)
    return not tags or (series is not None and series in tags)


# 「X가/이 발행한」에서 X 가 자기 회사가 아닌 문장(fix round 5) — 남(종속기업·관계기업·투자 대상)이 발행한 상품이다.
_SELF_ISSUER_EXTRA = {"지배기업", "연결실체", "연결기업", "연결회사", "지배회사"}  # + _SELF_COMPANY(아래)
_ISSUE_VERB_RE = re.compile(r"발행\s*(?:한|하였|했|하는|하고)")
_SUBJECT_TOKEN_RE = re.compile(r"(?<!\S)(\S+?)(?:가|이)(?=\s)")
_AFFILIATE_RE = re.compile(r"(?:종속|관계|공동)(?:기업|회사)|피?투자(?:회사|기업)|자회사|관계사")
_AFFILIATE_INSTRUMENT_RE = re.compile(_AFFILIATE_RE.pattern + r"[^.。,]{0,20}?(?:발행|" + _KIND_NAME + r"|신주인수권부사채)")
# T9c: 주석 제목이 주어에 붙어 온 꼴(「(17) 상환전환우선주당사가 발행한」·「3.16 전환사채회사가 발행한」·「2.9 복합금융상품
# 당사가 발행한」) — 종류 낱말(또는 제목 「복합금융상품」) 뒤의 당사·회사·연결회사 등은 자기 회사다.
_GLUED_KIND_RE = re.compile(r"^.*(?:전환사채|신주인수권부사채|교환사채|우선주|복합금융상품)"
                            r"(?=(?:당사|회사|동사|연결회사|지배회사|지배기업|연결실체|연결기업)$)")


def _self_names(holder: bool = False) -> set[str]:
    """자기 회사 낱말. 보유자 공시(holder=True)에서는 「발행회사」가 투자 대상(남)일 수 있으므로 자기 회사가 아니다(T9c)."""
    return _SELF_COMPANY - {"발행회사"} if holder else _SELF_COMPANY


def _non_self_issuer(text: str | None, holder: bool = False) -> bool:
    """문장이 남이 발행한 상품을 말하는가: 「X가/이 (…) 발행한」의 X(발행 바로 앞 30자 안의 마지막 주어)가 자기 회사가
    아님, 종속기업·관계기업·공동기업·투자회사·자회사 + 상품·발행, 「B사(의) 전환사채」(_investee_named 문장판).
    T9c: 「상환전환우선주당사」처럼 종류 낱말 뒤에 붙은 자기 회사 낱말은 자기 회사. holder=True(보유자 공시)면 「발행회사」는
    자기 회사가 아니다."""
    text = text or ""
    selfs = _self_names(holder)
    for m in _ISSUE_VERB_RE.finditer(text):
        a = m.start() - 30
        head = text[max(0, a):m.start()]
        head = re.split(r"[,.。]", head)[-1]
        subs = list(_SUBJECT_TOKEN_RE.finditer(head + " "))
        if subs:
            tok = re.sub(r"[^가-힣A-Za-z0-9()]", "", subs[-1].group(1))
            tok = re.sub(r"^.*\(이하", "", tok).strip("()")
            tok = _GLUED_KIND_RE.sub("", tok)
            if tok not in selfs and tok not in _SELF_ISSUER_EXTRA:
                return True
    if _AFFILIATE_INSTRUMENT_RE.search(text):
        return True
    return any(m.group(1) not in selfs for m in _INVESTEE_RE.finditer(text))


def _separation_kind(text: str, holder_rx: re.Pattern | None = None) -> str | None:
    """문장 하나가 말하는 분리 여부: "sep"(분리함), "not"(분리 안 함), None(어느 쪽도 아니거나 둘 다).
    holder_rx 기본은 _HOLDER_RE(확인 쪽). 막는 쪽은 _HOLDER_BASE_RE 를 넘긴다(T9c: 새 증권 낱말로 막는 쪽이 느슨해지지 않게)."""
    return _separation_scan(text, holder_rx)[0]


def _separation_spans(text: str) -> list[tuple[int, int]]:
    """분리 진술("sep") 문장의 분리 동사(또는 옵션 목록 → 파생상품부채) 일치 범위들. "sep" 이 아니면 빈 목록."""
    kind, spans = _separation_scan(text, None)
    return spans if kind == "sep" else []


# T9c fix round 1: 「…내재파생상품이 아닌 … 독립된 파생상품자산으로 분리」 — 그 옵션은 내재파생상품이 아니라고 말하므로
# 내재파생상품 분리 진술이 아니다. 확인 쪽(holder_rx 기본)에서만 None 으로 본다. 「분리하지 않았다」(not)로 바꾸지는 않는다
# (not_separated 확인 근거가 새로 생기지 않게). 막는 쪽(_HOLDER_BASE_RE)은 이전 그대로 분리 문장으로 센다.
_NOT_EMBEDDED_RE = re.compile(r"내재\s*파생\s*(?:금융)?상품\s*(?:이|은|는)?\s*(?:아닌|아니)")


def _separation_scan(text: str, holder_rx: re.Pattern | None) -> tuple[str | None, list[tuple[int, int]]]:
    text = text or ""
    if (holder_rx or _HOLDER_RE).search(text) or _SEP_CONDITIONAL_RE.search(text):
        return None, []
    if holder_rx is None and _NOT_EMBEDDED_RE.search(text):
        return None, []
    neg_spans = [m.span() for m in _NEG_SEP_RE.finditer(text)]
    pos = False
    spans: list[tuple[int, int]] = []
    for m in _SEP_WORD_RE.finditer(text):
        if any(a <= m.start() < b for a, b in neg_spans) or text[max(0, m.start() - 1):m.start()] == "비":
            continue
        if _SEP_NOT_SHOWN_RE.match(text, m.end()):
            continue  # 「별도로 분리하여 공시하지 않았습니다」 — 공시 방식이지 분리 진술이 아니다
        window = text[max(0, m.start() - 40):m.end() + 60]
        if not _SEP_NEAR_RE.search(window):
            continue
        if _EQUITY_SPLIT_RE.search(window) and "파생" not in window:
            continue  # 부채부분·자본부분(전환권대가) 분리는 내재파생상품 분리가 아니다
        clause = _clause(text, m.start(), m.end())
        if _EQUITY_SPLIT_RE.search(clause) and "파생" not in clause:
            continue  # 「…파생상품자산으로 구분하여 인식하고 복합금융상품은 부채요소와 자본요소로 분리」 — 제 마디는 자본 분리
        pos = True
        spans.append(m.span())
    if not pos:
        # 겹친 후보도 본다(「전환권은 자본으로 분류되지 않았으며, 전환권과 …을 파생상품부채로 분류」의 둘째 주어)
        starts = sorted({m.start() for m in re.finditer(_OPT_ANY + r"|내재파생상품", text)})
        for m in (m for rx in _OPTION_DERIV_SENT_RES for p in starts for m in [rx.match(text, p)] if m):
            if (_NEGATION_RE.search(text[m.end():]) or _GAP_OTHER_SUBJECT_RE.search(m.group("gap"))
                    or any(a < m.end() and m.start() < b for a, b in neg_spans)):
                continue  # 「…분리요건을 충족하지 못하여 주계약에 포함된 내재파생금융부채로 인식」은 분리가 아니다
            pos = True
            spans.append(m.span())
            break
    if pos and neg_spans:
        return None, []
    return ("sep", spans) if pos else (("not", []) if neg_spans else (None, []))


def _fvpl_designation(text: str) -> bool:
    """상품 전체를 당기손익-공정가치 측정 금융부채로 지정했다는 문장(부채; 보유자·조건·부정 아님)."""
    if _HOLDER_RE.search(text or "") or _SEP_CONDITIONAL_RE.search(text or ""):
        return False
    flat = re.sub(r"\s+", "", text or "")
    for m in _FVPL_DESIGNATION_RE.finditer(flat):
        if _NEGATION_RE.search(flat[m.end():]):
            continue
        if m.group(1) == "지정" or _WHOLE_RE.search(flat):
            return True
    return False


def _amortised_sentence(text: str) -> bool:
    """상품(부채요소)을 상각후원가로 측정했다는 문장 — 파생·공정가치·분리·옵션 낱말 없이."""
    t = text or ""
    if _HOLDER_RE.search(t) or _SEP_CONDITIONAL_RE.search(t) or not _AMORTISED_RE.search(t):
        return False
    return not re.search(r"파생|공정가치|분리|" + _OPT_ANY, t)


def _row_col_texts(cell: dict) -> list[str]:
    return [*(cell.get("label_path") or []), cell.get("label") or "", cell.get("col_header") or ""]


def _cell_tied(cell: dict, kind: str) -> bool:
    """칸이 이 종류의 상품 것인가 — 상품 종류 낱말로만(fix round 1: 매도청구권·콜옵션·풋·조기상환 같은 일반 옵션 낱말은
    아무 종류에나 붙으므로 칸을 묶지 못한다). 행 이름·열 머리글에 종류 낱말이 없으면 표 끝 문장·주석 제목에서 본다."""
    kinds: set[str] = set()
    for t in _row_col_texts(cell):
        kinds |= _text_kinds(t)
    if not kinds:
        for t in (_title_core(cell.get("table_title")), cell.get("note_title") or ""):
            kinds |= _text_kinds(t)
    return kind in kinds


# 자산 쪽 분리 칸에서 막는 것: 제3자(지정 가능) 콜, 「…권자의」 보유자 옵션, 회사·투자 이름 층(남의 상품·지분 옵션).
_ASSET_SEP_BLOCK_RE = re.compile(r"제\s*3\s*자|제삼자|권자")
# 보유자 쪽 표(fix round 2): 표 제목(앞 문장이 붙어 온 것 포함)·주석 제목이 보유·취득·투자를 말하면 자산 칸은 남의 상품이다.
_HOLDER_TITLE_RE = re.compile(r"보유\s*(?:하고\s*있는|한|중인|중|하는)|취득\s*(?:한|하여)|투자(?!자)")
# 투자 대상 이름 + 상품 종류(「B사 전환사채」·「에이비씨사의 전환사채」). 당사·회사·연결회사·지배회사·발행회사 등은 자기 자신.
_INVESTEE_RE = re.compile(r"(?<![가-힣A-Za-z0-9])([가-힣A-Za-z0-9]{0,10}사)\s*의?\s*"
                          r"(?:전환사채|신주인수권부사채|교환사채|(?:상환)?(?:전환)?(?:상환)?우선주)")
_SELF_COMPANY = {"당사", "회사", "동사", "연결회사", "지배회사", "모회사", "발행회사", "본사"}
# 행·열의 회사 이름(부채·자산 칸 모두, fix round 4). 「투자자」·「조합」 같은 낱말은 넣지 않는다(부채 칸 「투자자의 조기상환청구권」).
_COMPANY_NAME_RE = re.compile(r"\(주\)|㈜|주식회사|유한회사|(?<![A-Za-z])(?:Inc|Ltd|Corp|LLC)(?![A-Za-z])")
_COMPANY_LAYER_RE = re.compile(r"\(주\)|㈜|주식회사|유한회사|합자회사|조합|투자|펀드|(?<![A-Za-z])(?:Inc|Ltd|Co|Corp|LLC)(?![A-Za-z])")


def _investee_named(cell: dict, holder: bool = False) -> bool:
    """행 이름·열 머리글이 상품 앞에 투자 대상 회사 이름을 적었는가(「B사 전환사채의 전환권 등」). 당사·회사·연결회사 등
    자기 회사는 아니다. 「종속회사의 전환사채」·「자회사 …」도 막는다(fix round 4: 기록의 회사가 연결 발행자인지 이 칸만으로
    알 수 없다 — 거부는 안전하다). T9c: 보유자 공시(holder=True)에서는 「발행회사」도 투자 대상이다."""
    selfs = _self_names(holder)
    return any(m.group(1) not in selfs for t in _row_col_texts(cell) for m in _INVESTEE_RE.finditer(t))


def _holder_title(cell: dict) -> bool:
    titles = re.sub(r"\s+", " ", (cell.get("table_title") or "") + " | " + (cell.get("note_title") or ""))
    return bool(_HOLDER_TITLE_RE.search(titles))


def _holder_filing(cells: dict, sentences: dict) -> bool:
    """공시 단위 보유자 신호: 보유자 진술 문장(상품 종류 낱말 + 매매목적·보유·취득·투자·금융자산으로 분류), 투자 대상 이름
    칸(「B사 전환사채의 전환권 등」), 또는 제목 끝 문장이 보유자 낱말과 상품 종류를 함께 말하는 표(「보유하고 있는 전환사채」)."""
    if any(_holder_statement(t) for t in sentences.values()):
        return True
    for c in cells.values():
        if _cell_kinds(c) and _investee_named(c):
            return True
        core = _title_core(c.get("table_title"))
        if _HOLDER_TITLE_RE.search(core) and _text_kinds(core):
            return True
    return False


# T9c(R14·R17): 제3자 지정 콜(제3자 지정·지정권자·지정하는 (제3)자·제3자에게 매도)을 분리했다는 문장은 separated 의
# 확인 근거가 아니다 — 분리 동사가 든 마디가 부채 쪽(파생…부채·금융부채)을 말하지 않고, 파생상품자산을 말하거나 콜(매도청구권·
# 콜옵션)만 말할 때. 부채 쪽을 말하면 부채 쪽 분리 문장이라 그대로 본다(「제3자지정 콜옵션 및 조기상환권에 대해 각각
# 파생상품자산 및 주계약과 분리한 후 파생상품부채로 회계처리」). 「조기상환청구권과 발행자(또는 발행자가 지정하는 제3자)
# 매도청구권을 … 분리하여 별도의 파생상품으로 분류」처럼 풋도 함께 분리하는 문장도 그대로 본다.
_THIRD_PARTY_CALL_RE = re.compile(r"제\s*3\s*자\s*(?:가\s*)?지정|제삼자\s*지정|지정권자|지정하는\s*(?:제\s*3\s*)?자"
                                  r"|지정한\s*자|제\s*3\s*자를\s*지정|제\s*3\s*자에게\s*(?:매도|매각|양도)")
_LIAB_SIDE_RE = re.compile(r"(?<!비)파생[가-힣]{0,6}부채|금융부채")
_CALL_RE = re.compile(r"매도청구권|콜\s*옵션|[Cc]all\s*[Oo]ption")
_NON_CALL_OPTION_RE = re.compile(r"조기상환청구권|조기상환권|상환청구권|상환권|풋\s*옵션|[Pp]ut\s*[Oo]ption|전환권|신주인수권(?!부)"
                                 r"|교환권")


def _third_party_call_only(text: str, spans) -> bool:
    """제3자 지정 콜 낱말이 있고, 분리 동사가 든 마디들이 부채 쪽을 말하지 않으며, 파생상품자산을 말하거나 콜만 말하는 문장."""
    if not _THIRD_PARTY_CALL_RE.search(text or ""):
        return False
    clauses = [_clause(text, s, e) for s, e in spans]
    if any(_LIAB_SIDE_RE.search(c) for c in clauses):
        return False
    joined = " ".join(clauses)
    return "자산" in joined or not _NON_CALL_OPTION_RE.search(_CALL_RE.sub("", joined))


def _issuer_sep_sentence(text: str, kind: str, series: str | None, holder_filing: bool, prev: str | None = None) -> bool:
    """separated 를 확인하는 발행자 쪽 분리 문장(fix round 4, R17): 분리 진술("sep")이고 이 상품(종류·회차)을 말하며,
    보유자 낱말이 없고, 보유자 공시에서는 자기 회사를 발행자로 적어야 한다(「당사가 발행한」).
    fix round 5: 종류는 문장이 말하거나 바로 앞 문장(prev)을 이어 써야 하고(_strict_fits), 남이 발행한 상품(「종속기업인
    오스틴제약(주)가 발행한」)을 말하면 아니다.
    T9c: 종류 낱말은 분리 동사가 든 마디(와 동사 앞 주제어 구)에서만 읽는다(_verb_kinds). 보유자 공시에서는 「발행회사」가
    자기 회사가 아니다. 제3자 지정 콜을 자산으로 분리했다는 문장(_third_party_call_only)은 아니다."""
    spans = _separation_spans(text)
    if (not spans or not _strict_fits(text, kind, series, prev, spans, holder_filing) or _holder_wording(text)
            or _non_self_issuer(text, holder=holder_filing) or _third_party_call_only(text, spans)):
        return False
    return not holder_filing or bool(_ISSUER_NAMED_RE.search(text or ""))


def _input_kinds(cells: dict, sentences: dict) -> set[str]:
    """입력 전체(문장, 칸의 행 이름·열 머리글·표 제목·주석 제목)가 말하는 상품 종류.
    주의: build_input 이 넘긴 문장·칸만 본다. 문장은 MAX_SENTENCES(200)·대상 주석으로 잘리므로 잘린 곳에만 있는 종류는
    세지 않는다 — 그만큼 option_class 의 「입력 단일 종류」 경로가 넓어질 수 있다(최선 노력 검사이지 보증이 아니다)."""
    out: set[str] = set()
    for t in sentences.values():
        out |= _text_kinds(t)
    for c in cells.values():
        out |= _cell_kinds(c) | _text_kinds(c.get("table_title")) | _text_kinds(c.get("note_title"))
    return out


def _issuer_option_sentence(text: str, kind: str, holder_filing: bool, prev: str | None = None,
                            input_kinds: set[str] | frozenset = frozenset(), series: str | None = None) -> bool:
    """option_class = derivative_liability 를 확인하는 문장(fix round 5, T9c): 전환권·신주인수권·교환권을 파생상품부채로
    분류한 문장(_option_liability_spans)이고, 보유자 낱말·남이 발행한 상품이 없고, 보유자 공시면 자기 회사를 발행자로 적는다.
    종류는 R15 옵션 낱말 지도로만 묶는다(전환권 → CB·RCPS, 신주인수권 → BW, 교환권 → EB; 상환권·조기상환·콜·풋은 어느
    종류에도 묶지 않는다). 그 일치의 옵션 낱말이 이 종류로 가야 하고, 다음 중 하나여야 한다.
    - 문장이 종류 낱말을 말하면: 그 일치가 든 마디(와 마디 앞 주제어 구, _verb_kinds)에 이 종류가 있다. 앞 마디에만 있으면 아니다.
    - 문장에 종류 낱말이 없으면: 바로 앞 문장을 이어 쓴다(_continues — 앞 문장이 이 종류 하나만 말하는 조건 아닌 「…다.」
      문장, 새 항목 표시 아님), 또는 입력 전체(input_kinds)에서 그 옵션 낱말이 가리키는 종류가 이 종류 하나뿐이다. 뒤의 경우
      바로 앞 문장이 조건·보유자·남의 상품 문장이거나 이 종류를 말하지 않는 종류 문장이면 아니다.
    회차는 문장(이어 쓰면 앞 문장 포함)에 적힌 것과 같아야 한다."""
    text = text or ""
    matches = _option_liability_spans(text)
    if (not matches or _holder_wording(text) or _non_self_issuer(text, holder=holder_filing)
            or (holder_filing and not _ISSUER_NAMED_RE.search(text))):
        return False
    mine = [sp for sp in matches if kind in sp[2]]
    if not mine:
        return False
    tags = _series_tags(text)
    if _text_kinds(text):
        if kind not in _verb_kinds(text, mine):
            return False
    elif _continues(text, prev, kind, holder_filing):
        tags |= _series_tags(prev)
    else:
        # 바로 앞 문장이 조건·보유자·남의 상품 문장이거나 이 종류를 말하지 않는 다른 종류 문장이면 기대지 않는다
        if prev and (_SEP_CONDITIONAL_RE.search(prev) or _holder_wording(prev) or _non_self_issuer(prev, holder=holder_filing)
                     or (_text_kinds(prev) and kind not in _text_kinds(prev))):
            return False
        mapped = set().union(*(sp[2] for sp in mine))
        if mapped & set(input_kinds) != {kind}:
            return False
    return not tags or (series is not None and series in tags)


def _cell_option_kinds(cell: dict) -> set[str]:
    """칸의 행 이름(어느 층이든)·열 머리글의 전환권·신주인수권·교환권이 가리키는 종류(R15 지도)."""
    out: set[str] = set()
    for t in [*(cell.get("label_path") or []), cell.get("label") or "", cell.get("col_header") or ""]:
        out |= _r15_kinds(_norm_label(t))
    return out


def _issuer_option_cell(cell: dict, holder_filing: bool, issuer_sentence: bool) -> bool:
    """option_class 근거 칸의 보유자 가드(fix round 5, 분리 칸 부채 쪽 규칙과 같다): 투자 대상 이름·회사 이름 칸은 아니고,
    보유자 제목 표나 보유자 공시의 칸은 입력에 이 종류의 발행자 문장(issuer_sentence)이 있거나 (보유자 제목이 아닌) 그 표
    제목이 자기 회사를 발행자로 적을 때만. T9c: 보유자 공시에서는 「발행회사」도 투자 대상 이름이다."""
    if (_investee_named(cell, holder=holder_filing)
            or any(_COMPANY_NAME_RE.search(re.sub(r"\s+", "", t)) for t in _row_col_texts(cell))):
        return False
    if issuer_sentence or not (_holder_title(cell) or holder_filing):
        return True
    own_title = _title_core(cell.get("table_title")) + " | " + (cell.get("note_title") or "")
    return not _holder_title(cell) and bool(_ISSUER_NAMED_RE.search(own_title))


def _separation_cell(cid: str, cell: dict, kind: str, series: str | None, nb: set, *, sep_sentence: bool = False,
                     holder_filing: bool = False, contradiction: bool = False) -> bool:
    """이 상품의 분리 파생상품 금액 칸(잔액 표, 0 아님, 종류·회차가 맞고 상품 종류 낱말로 그 상품에 묶임).
    기본은 부채 쪽 칸만이다(fix round 1). 자산 쪽 칸은 행 이름·열 머리글 자체가 기록 종류를 말하고, 제3자·권자(보유자)
    낱말과 회사·투자 이름 층(label_path)이 없을 때만 — 제3자 지정 콜, 보유한 신주인수권, 남의 지분 콜은 분리가 아니다.
    fix round 2: 상품 앞의 투자 대상 이름(「B사 전환사채」, 당사·회사 제외)이나 표 제목(앞 문장 포함)·주석 제목의 보유자
    낱말(보유하고 있는·보유한·취득한·투자)이 있어도 자산 칸은 아니다.
    fix round 3(R17): 자산 칸은 입력에 같은 종류(회차)의 발행자 분리 문장(sep_sentence)이 있을 때만이다.
    fix round 4: 부채 칸도 투자 대상 이름(종속회사·자회사 포함)이나 행·열의 회사 이름(「(주)…」·「㈜」·주식회사)이면 아니다.
    보유자 제목 표(「회사가 보유하고 있는 파생상품의 내역」)나 보유자 공시(holder_filing)의 부채 칸은 같은 종류의 발행자 분리
    문장(sep_sentence)이 있거나, (보유자 제목이 아닌) 그 표의 제목·주석 제목이 자기 회사를 발행자로 적을 때(「당사가 발행한
    전환사채 및 관련 내재파생상품의 세부내역」)만이다. 자산 칸은 보유자 제목이면 문장이 있어도 아니다.
    contradiction=True(not_separated·whole_fvpl 모순 검사)는 이전 그대로 부채 쪽 칸이면 모두 센다 — 막는 규칙이 모순
    검사를 느슨하게 하지 않도록."""
    if cid in nb or not _num(cell.get("value")) or not _series_fits(cell, series, kind) or not _cell_tied(cell, kind):
        return False
    side = _deriv_side(cell)
    if contradiction:
        return side == "liability"
    if side not in ("liability", "asset") or _investee_named(cell, holder=holder_filing):
        return False
    if any(_COMPANY_NAME_RE.search(re.sub(r"\s+", "", t)) for t in _row_col_texts(cell)):
        return False  # 「(주)에이투지엔터테인먼트 전환우선주 풋옵션」 — 남의 회사 상품
    if side == "liability":
        if sep_sentence or not (_holder_title(cell) or holder_filing):
            return True
        # 보유자 공시라도 표 제목(끝 문장)·주석 제목이 자기 회사를 발행자로 적으면(「당사가 발행한 전환사채 및 관련
        # 내재파생상품의 세부내역」) 그 표의 부채 칸은 발행자 칸이다(보유자 제목 표는 아니다).
        own_title = _title_core(cell.get("table_title")) + " | " + (cell.get("note_title") or "")
        return not _holder_title(cell) and bool(_ISSUER_NAMED_RE.search(own_title))
    if not sep_sentence:
        return False
    texts = [re.sub(r"\s+", "", t) for t in _row_col_texts(cell)]
    own = set().union(*(_text_kinds(t) for t in texts))
    if kind not in own or any(_ASSET_SEP_BLOCK_RE.search(t) for t in texts):
        return False
    if any(_COMPANY_LAYER_RE.search(p) for p in (cell.get("label_path") or [])):
        return False
    return not _holder_title(cell)


def _measured_cell(cid: str, cell: dict, kind: str, series: str | None, nb: set, rx: re.Pattern) -> bool:
    """상품 행(종류 낱말, 파생·옵션 행 아님)이 rx(상각후원가·당기손익-공정가치 측정 금융부채) 열·묶음 행·표 끝 문장에 있는 칸."""
    if cid in nb or not _num(cell.get("value")) or not _series_fits(cell, series, kind):
        return False
    labels = [_norm_label(p) for p in (cell.get("label_path") or [])] + [_norm_label(cell.get("label"))]
    own = [t for t in labels if _text_kinds(t)]
    if not own or kind not in set().union(*(_text_kinds(t) for t in own)):
        return False
    if any(_DERIV_RE.search(t) or _option_term(t) or "자산" in t for t in labels):
        return False
    texts = _col_segments(cell) + labels + [re.sub(r"\s+", "", _title_core(cell.get("table_title")))]
    return any(rx.search(t) and "자산" not in t for t in texts)


def _separation_supported(claim: str, ids: list[str], rec: dict, cells: dict, sentences: dict, nb: set,
                          has_deriv_amount: bool) -> bool:
    """embedded_separation 값이 근거로 뒷받침되는가(R14: 없거나 어긋나면 False).
    - separated: 그 상품(종류·회차)의 분리 파생상품 금액 칸, 또는 그 상품의 분리 문장(「분리하여 … 파생상품/공정가치」,
      옵션 목록이 「파생상품(부채)로 인식」). 근거 중 분리하지 않았다는 문장이 있으면 아니다. 자본요소 분리는 아니다.
      분리 문장은 발행자 쪽만(_issuer_sep_sentence: 보유자 낱말 없음, 보유자 공시면 「당사가 발행한」 — fix round 4).
    - not_separated: 분리하지 않았다는 문장, 상각후원가로만 측정한다는 문장·칸, 또는 전체 FVPL 지정(d8).
    - whole_fvpl: 전체 FVPL 지정 문장이나 당기손익-공정가치 측정 금융부채 칸의 그 상품 행.
    not_separated·whole_fvpl 은 기록에 확인된 파생상품 금액이 없고, 입력에 그 상품의 분리 파생상품 칸·분리 문장이 없을 때만.
    주의: 이 「입력 전체」 검사는 build_input 이 넘긴 문장·칸만 본다. 문장은 MAX_SENTENCES(200)·대상 주석으로 잘리므로
    잘린 곳에 있는 분리 문장은 모순으로 잡히지 않는다(최선 노력 검사이지 보증이 아니다)."""
    kind, series = rec.get("kind"), _record_series(rec)
    holder_filing = _holder_filing(cells, sentences)
    # 막는 쪽(분리하지 않았다는 문장 섞임·분리 문장 모순)은 넓게(_sentence_fits, _HOLDER_BASE_RE), 확인하는 쪽은
    # 좁게(_strict_fits, fix round 5; 새 증권 보유자 낱말 _HOLDER_RE, T9c)
    sent = [sentences[i] for i in ids if i in sentences and _sentence_fits(sentences[i], kind, series)]
    # T9c fix round 1: not_separated·whole_fvpl 확인 문장도 동사 마디 종류 규칙 — 「분리하지 않」·상각후원가·전체 FVPL 지정
    # 일치가 든 마디(와 동사 바로 앞 주어)의 종류로 묶는다.
    def confirm(test, spans_of) -> bool:
        for i in ids:
            t = sentences.get(i)
            if t is None or not test(t) or _non_self_issuer(t, holder=holder_filing):
                continue
            spans = spans_of(t)
            if spans and _strict_fits(t, kind, series, _prev_sentence(i, sentences), spans, holder_filing):
                return True
        return False
    ev_cells = [(i, cells[i]) for i in ids if i in cells]
    kinds = [_separation_kind(t, _HOLDER_BASE_RE) for t in sent]
    if claim == "separated":
        if "not" in kinds:
            return False
        # 발행자 쪽 분리 문장만(fix round 4). 입력 어딘가에 같은 종류(회차)의 발행자 분리 문장이 있어야 자산 칸과
        # 보유자 표·보유자 공시의 부채 칸이 근거가 된다(R17).
        if any(_issuer_sep_sentence(sentences[i], kind, series, holder_filing, _prev_sentence(i, sentences))
               for i in ids if i in sentences):
            return True
        any_issuer = any(_issuer_sep_sentence(t, kind, series, holder_filing, _prev_sentence(i, sentences))
                         for i, t in sentences.items())
        return any(_separation_cell(i, c, kind, series, nb, sep_sentence=any_issuer, holder_filing=holder_filing)
                   for i, c in ev_cells)
    if has_deriv_amount or "sep" in kinds:
        return False
    # 모순 검사는 넓게: 보유자·발행자를 가리지 않은 분리 문장, 부채 쪽 분리 칸(막는 규칙 없이)
    any_sep = any(_separation_kind(t, _HOLDER_BASE_RE) == "sep" and _sentence_fits(t, kind, series)
                  for t in sentences.values())
    if any_sep or any(_separation_cell(i, c, kind, series, nb, contradiction=True) for i, c in cells.items()):
        return False
    fvpl = confirm(_fvpl_designation, lambda t: _flat_spans(t, _FVPL_DESIGNATION_RE)) or any(
        _measured_cell(i, c, kind, series, nb, _FVPL_LIAB_RE) for i, c in ev_cells)
    if claim == "whole_fvpl":
        return fvpl
    return (fvpl or confirm(lambda t: _separation_kind(t) == "not", lambda t: [m.span() for m in _NEG_SEP_RE.finditer(t)])
            or confirm(_amortised_sentence, lambda t: [m.span() for m in _AMORTISED_RE.finditer(t)])
            or any(_measured_cell(i, c, kind, series, nb, _AMORTISED_RE) for i, c in ev_cells))


def _flat_spans(text: str, rx: re.Pattern) -> list[tuple[int, int]]:
    """공백을 뺀 글에서 찾은 rx 일치를 원문 위치로 돌린다(_FVPL_DESIGNATION_RE 는 공백 없는 글에 쓴다)."""
    pos = [i for i, ch in enumerate(text or "") if not ch.isspace()]
    flat = "".join(text[i] for i in pos)
    return [(pos[m.start()], pos[m.end() - 1] + 1) for m in rx.finditer(flat) if m.end() > m.start()]

def validate(record: dict, inp: dict, *, seq: int = 1, unit_kind: str | None = None) -> tuple[dict | None, list[str]]:
    """근거를 확인한 기록(사본)과 버린 사유 목록. kind 가 틀리면 (None, [사유]).

    금액은 근거 칸의 원래 단위 값으로 비교한다. 비교 단위 종류는 record["unit_kind"] → 인자 unit_kind → "krw".
    """
    kind = record.get("kind")
    if kind not in KINDS:
        return None, [f"bad_kind:{kind}"]
    rec = copy.deepcopy(record)
    reasons: list[str] = []
    cells = inp.get("cells") or {}
    sentences = inp.get("sentences") or {}
    want_unit = rec.get("unit_kind") or unit_kind or "krw"

    _coerce_shapes(rec)
    evidence_in = rec.get("evidence")
    evidence: dict[str, list[str]] = {}
    cited: dict[str, int] = {}
    for field, ids in evidence_in.items():
        ids = ids if isinstance(ids, list) else [ids]
        cited[field] = len(ids)
        good = []
        for i in ids:
            i = _canonical_id(i, cells, sentences)
            if i is not None and i not in good:
                good.append(i)
        evidence[field] = good

    # 값 이름이 틀린 필드
    for field, allowed in _ENUMS.items():
        if rec.get(field) is None:
            rec[field] = UNCONFIRMED
        elif rec[field] not in allowed:
            rec[field] = UNCONFIRMED
            reasons.append(f"bad_value:{field}")
    for period in ("current", "prior"):
        mv = rec["movement"].get(period)
        rec["movement"][period] = [m for m in mv if isinstance(m, str) and m in MOVEMENTS] if isinstance(mv, list) else []

    # 규칙 1·2·3·6 — 금액 필드
    matched: dict[str, str] = {}
    matched_mult: dict[str, object] = {}
    series = _record_series(rec)
    nb = _non_balance_ids(cells)
    for field, holder, key in _amount_fields(rec):
        value = holder[key]
        if value is None:
            continue
        ids = evidence.get(field) or []
        if not ids:
            holder[key] = None
            evidence[field] = []
            reasons.append(f"missing_ref:{field}")
            continue
        amount = _num(value)
        target = field.split(".")[1] if field.startswith("balance.") else None
        if target not in ("current_end", "prior_end"):
            target = None
        value_hits = []
        for i in ids:
            c = cells.get(i)
            if c is None or c.get("unit_kind") != want_unit:
                continue
            cv = _num(c.get("value"))
            if amount is not None and cv is not None and abs(cv - amount) <= AMOUNT_TOLERANCE:
                value_hits.append(i)
        hits = [i for i in value_hits if target is None or _period_ok(cells[i], target)]
        fit = [i for i in hits if _series_fits(cells[i], series, kind)]
        bal = [i for i in fit if i not in nb]
        side = _DERIV_AMOUNT_KEYS.get(key)
        if side is None and key in _NON_DERIV_KEYS:
            nonderiv = [i for i in bal if not _derivative_row(cells[i])]
            deriv_row_only = bool(bal) and not nonderiv  # 사유만 정해 두고 host 합 경로는 그대로 돈다(fix round 2)
            bal = nonderiv
        else:
            deriv_row_only = False
        good = [i for i in bal if side is None or _deriv_side(cells[i]) == side]
        sum_reason = None
        if not good and key == "host_carrying" and amount is not None and len(ids) == cited.get(field):
            summed = _host_sum(ids, cells, amount, want_unit)
            if summed and target is not None and not all(_period_ok(cells[i], target) for i in summed):
                summed, sum_reason = None, "period_mismatch"
            elif summed and not all(_series_fits(cells[i], series, kind) for i in summed):
                summed, sum_reason = None, "series_mismatch"
            elif summed and any(i in nb for i in summed):
                summed, sum_reason = None, "not_balance"
            if summed:
                matched[field] = "+".join(summed)
                matched_mult[field] = cells[summed[0]].get("multiplier")
                evidence[field] = summed
                continue
        if not good:
            holder[key] = None
            evidence[field] = []
            if sum_reason and not value_hits:
                reasons.append(f"{sum_reason}:{field}")
            elif value_hits and not hits:
                reasons.append(f"period_mismatch:{field}")
            elif not hits:
                reasons.append(f"amount_mismatch:{field}")
            elif not fit:
                reasons.append(f"series_mismatch:{field}")
            elif deriv_row_only:
                reasons.append(f"derivative_row:{field}")
            elif not bal:
                reasons.append(f"not_balance:{field}")
            elif all(_is_host_cell(cells[i]) for i in bal):
                reasons.append(f"host_only:{field}")
            else:
                reasons.append(f"not_derivative:{field}")
            continue
        matched[field] = good[0]
        matched_mult[field] = cells[good[0]].get("multiplier")
        evidence[field] = [good[0]]

    # 규칙 1 — 분류 필드
    for field in _ENUMS:
        if rec.get(field) in (None, UNCONFIRMED):
            continue
        if not evidence.get(field):
            rec[field] = UNCONFIRMED
            reasons.append(f"missing_ref:{field}")

    # 규칙 3 — 전환권 파생상품부채
    if rec.get("option_class") == "derivative_liability":
        ids = evidence.get("option_class") or []
        ev_cells = [cells[i] for i in ids if i in cells and i not in nb]
        # fix round 5: 문장·칸 모두 보유자 낱말·보유자 공시·남이 발행한 상품 가드(분리 근거와 같은 R17 규칙)
        hf = _holder_filing(cells, sentences)
        ik = _input_kinds(cells, sentences)
        issuer_any = any(_issuer_option_sentence(t, kind, hf, _prev_sentence(i, sentences), ik, series)
                         for i, t in sentences.items()) or any(
            _issuer_sep_sentence(t, kind, series, hf, _prev_sentence(i, sentences)) for i, t in sentences.items())
        # T9c: 칸은 R15 옵션 낱말 지도로만 종류에 묶는다(「파생상품부채(전환권 및 조기상환권)」 → CB·RCPS, 「신주인수권」 → BW)
        has_deriv = any(_deriv_side(c) == "liability" and kind in _cell_option_kinds(c) and _series_fits(c, series, kind)
                        and _issuer_option_cell(c, hf, issuer_any) for c in ev_cells) or any(
            _issuer_option_sentence(sentences[i], kind, hf, _prev_sentence(i, sentences), ik, series)
            for i in ids if i in sentences)
        if not has_deriv:
            rec["option_class"] = UNCONFIRMED
            only_host = bool(ev_cells) and all(_is_host_cell(c) for c in ev_cells)
            reasons.append("host_only" if only_host else "no_derivative_ref")
        elif _equity_conflict(rec, cells):
            rec["option_class"] = UNCONFIRMED
            reasons.append("equity_conflict")

    # 규칙 8 — 내재파생상품 분리(T9b): 뜻이 맞는 근거가 있어야 한다
    claim = rec.get("embedded_separation")
    if claim not in (None, UNCONFIRMED):
        has_deriv_amount = any(holder[key] is not None for f, holder, key in _amount_fields(rec)
                               if key in _DERIV_AMOUNT_KEYS or f.startswith("separated_amount."))
        if not _separation_supported(claim, evidence.get("embedded_separation") or [], rec, cells, sentences, nb,
                                     has_deriv_amount):
            rec["embedded_separation"] = UNCONFIRMED
            evidence["embedded_separation"] = []
            reasons.append("no_separation_ref")
    if rec.get("embedded_separation") != "separated":
        rec["separated_targets"] = []  # 분리가 확인되지 않으면 분리 대상도 없다(fix round 1)

    # 확인된 근거만 남긴다(비어 버린 필드는 빈 목록)
    rec["evidence"] = evidence
    rec["matched"] = matched
    rec["matched_multiplier"] = matched_mult

    # 규칙 5
    if not rec["series_key"].strip():
        rec["series_key"] = _series_key(rec, seq)
    return rec, reasons
