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
_CONV_TERM_RE = re.compile(_CONV)
# 파생상품 잔액이 아닌 열: 공정가치 수준(서열체계)·회계오류 수정·재작성 금액.
_LEVEL_OR_RESTATE_COL_RE = re.compile(r"수준|[Ll]evel|회계오류|오류수정|수정금액|수정의영향|조정금액|재작성|소급")
# 잔액을 명시한 열(기간이 current_period·prior_period 인 표에서 balance.*_end 로 읽을 수 있는 열).
_BALANCE_COL_RE = re.compile(r"장부금액|잔액|기말|보고기간종료일|보고기간말")
_END_ROW_RE = re.compile(r"^(?:당?기말|전기말|기말(?:잔액|금액)?|당기말잔액|전기말잔액|보고기간종료일(?:현재)?(?:장부금액|잔액)?)$"
                         r"|장부가액|장부금액")
# 「보고기간종료일 현재 …의 내역」처럼 한 시점의 잔액 표(변동·손익 표 아님) — 당기·전기 열이 곧 당기말·전기말이다.
_STATUS_TITLE_RE = re.compile(r"현재")
_UNKNOWN_OK_RE = re.compile(r"당기말|현재")
_EVENT_TITLE_RE = re.compile(r"발행시|전환시|행사시|최초|평가|가치평가")
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


def _non_balance_cell(cell: dict) -> bool:
    """잔액 표가 아닌 표의 칸인가(만기분석·유동성위험·신용위험·이연법인세·상계·담보 제목, 현금유입·유출·거래상대방 머리글)."""
    ctx = re.sub(r"\s+", "", _title_core(cell.get("table_title")) + "|" + (cell.get("note_title") or ""))
    if _NON_BALANCE_TITLE_RE.search(ctx):
        return True
    col = re.sub(r"\s+", "", cell.get("col_header") or "")
    labels = [_norm_label(p) for p in (cell.get("label_path") or [])] + [_norm_label(cell.get("label"))]
    if _NON_BALANCE_COL_RE.search(col) or any(_NON_BALANCE_ROW_RE.search(t) for t in labels):
        return True
    return any(_COUNTERPARTY_COL_RE.match(seg) for seg in col.split("/"))


_MATURITY_COL_RE = re.compile(r"개월|\d+년|만기|현금흐름|이내|초과")  # 만기분석 열은 장부금액이 아니다
_MOVE_START_RE = re.compile(r"^(?:기초|발행|상환|전환|인식|제거)(?!권|청구권|우선주|사채)")
_SERIES_TAG_RE = re.compile(r"^제?(\d+(?:-\d+)?)(?:회차|회|차|종)")
_SERIES_ANY_RE = re.compile(r"(?:^|[^\d])제?(\d+(?:-\d+)?)(?:회차|회|차|종)|[Ss]eries([A-Z0-9]+)")
_SENT_ID_RE = re.compile(r"(?:^|:)s:?(\d+)$")
_DERIV_RE = re.compile(r"(?<!비)파생|전환권\s*\(?\s*부채")  # 「비파생금융부채」는 파생이 아니다
_NEGATION_RE = re.compile(r"^[^.,。]{0,25}?(않|아니|없)")
_DERIV_AMOUNT_KEYS = {"derivative_liability": "liability", "derivative_asset": "asset"}
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


def _option_liability_sentence(text: str) -> bool:
    """전환권·신주인수권·교환권(R15)을 파생상품부채(파생상품)로 분류·인식했다는 문장. 그 옵션이 든 목록이 주어이거나
    그 목록을 담은 「내재파생상품」이 주어여야 한다. 부정·조건(「…경우」, 앞이든 「…분류된 경우로서」처럼 뒤든)·자산,
    틈 안의 다른 주어(풋·콜·상품)·자본 분류는 아니다."""
    text = text or ""
    if _CONV_TO_EQUITY_RE.search(text):
        return False
    for m in (m for rx in _OPTION_LIAB_SENT_RES for m in rx.finditer(text)):
        if "자산" in _sentence_around(text, m.start(), m.end()) or _NEGATION_RE.search(text[m.end():]) or _GAP_OTHER_SUBJECT_RE.search(m.group("gap")):
            continue
        if _CONDITIONAL_RE.search(text[:m.end()]) or _CONDITIONAL_RE.search(text[m.end():m.end() + 12]):
            continue
        return True
    return False


def _positive_deriv_liability_sentence(text: str) -> bool:
    return _option_liability_sentence(text)


def _names_conversion_option(cell: dict) -> bool:
    """R15: 칸의 행 이름(어느 층이든)이나 열 머리글이 전환권·신주인수권·교환권을 말하는가(상품 이름 안의 글자,
    전환권조정·대가·가치는 아니다)."""
    texts = [_norm_label(p) for p in (cell.get("label_path") or [])] + [
        _norm_label(cell.get("label")), re.sub(r"\s+", "", cell.get("col_header") or "")]
    return any(_CONV_TERM_RE.search(_INSTRUMENT_RE.sub("", t)) for t in texts)


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


def _series_fits(cell: dict, series: str | None) -> bool:
    """칸의 회차(행 이름·label_path)가 없거나 기록 회차와 같으면 True. 기록 회차를 모르면 회차가 붙은 칸은 맞지 않는다."""
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
        fit = [i for i in hits if _series_fits(cells[i], series)]
        bal = [i for i in fit if not _non_balance_cell(cells[i])]
        side = _DERIV_AMOUNT_KEYS.get(key)
        good = [i for i in bal if side is None or _deriv_side(cells[i]) == side]
        sum_reason = None
        if not good and key == "host_carrying" and amount is not None and len(ids) == cited.get(field):
            summed = _host_sum(ids, cells, amount, want_unit)
            if summed and target is not None and not all(_period_ok(cells[i], target) for i in summed):
                summed, sum_reason = None, "period_mismatch"
            elif summed and not all(_series_fits(cells[i], series) for i in summed):
                summed, sum_reason = None, "series_mismatch"
            elif summed and any(_non_balance_cell(cells[i]) for i in summed):
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
        ev_cells = [cells[i] for i in ids if i in cells]
        series = _record_series(rec)
        has_deriv = any(_deriv_side(c) == "liability" and _names_conversion_option(c) and _series_fits(c, series)
                        for c in ev_cells) or any(
            _positive_deriv_liability_sentence(sentences[i]) for i in ids if i in sentences)
        if not has_deriv:
            rec["option_class"] = UNCONFIRMED
            only_host = bool(ev_cells) and all(_is_host_cell(c) for c in ev_cells)
            reasons.append("host_only" if only_host else "no_derivative_ref")
        elif _equity_conflict(rec, cells):
            rec["option_class"] = UNCONFIRMED
            reasons.append("equity_conflict")

    # 확인된 근거만 남긴다(비어 버린 필드는 빈 목록)
    rec["evidence"] = evidence
    rec["matched"] = matched
    rec["matched_multiplier"] = matched_mult

    # 규칙 5
    if not rec["series_key"].strip():
        rec["series_key"] = _series_key(rec, seq)
    return rec, reasons
