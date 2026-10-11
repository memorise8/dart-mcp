"""2층 ② 사채·우선주 기록 근거 검증 — 순수 함수(스펙 §5.3, 브리프 T8 검증 규칙).

1. evidence 각 필드의 근거 id 가 입력 칸·문장에 하나도 없으면 그 필드는 unconfirmed(금액은 null), `missing_ref:<field>`.
   값이 있는데 evidence 키가 없는 필드도 같다.
2. 금액 필드(balance.*.*, separated_amount.*)는 근거 칸 하나의 value(원래 단위)와 같아야 한다.
   unit_kind 가 같고 허용 오차 1. 아니면 null, `amount_mismatch:<field>`.
3. option_class = derivative_liability 는 파생상품부채 칸(행 이름에 「파생」·「전환권(부채)」, 부채 쪽)이나
   부정하지 않는 「파생…부채」 문장이 근거에 있어야 한다. 「파생상품자산」 칸, 「…파생상품부채로 분류하지 않…」
   문장은 근거가 아니다. 근거 칸이 부채요소(HOST) 행뿐이면 `host_only`, 그 밖은 `no_derivative_ref` 로 unconfirmed.
   balance.*.derivative_liability·derivative_asset 금액은 같은 쪽(부채·자산) 파생상품 칸과 맞아야 한다.
   행 이름이 옵션 낱말(전환권·상환권·조기상환·매도청구권·콜옵션·풋옵션·신주인수권·교환권)뿐이고 HOST·대가 행이
   아니면, 표 제목·주석 제목의 「파생상품부채」·「파생상품자산」(둘 중 하나만 있을 때)으로 쪽을 정한다.
   맞은 칸이 HOST 행이면 `host_only:<field>`, 그 밖(합계·자본요소 등)은 `not_derivative:<field>` 로 null.
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
_OPTION_EXCLUDE_RE = re.compile(r"대가|자본|조정")
_SIDE_DERIV_RE = re.compile(r"파생(?:상품|금융상품|금융)?(부채|자산)")
_JOINED_GAP = 6  # 「파생상품자산 및 파생상품부채」의 「및」 같은 짧은 이음
_TOTAL_RE = re.compile(r"^(합계|총계|계|소계)$")
_CLOSING_RE = re.compile(r"^(당?기말|전기말|기말(잔액|금액)?|당기말잔액|전기말잔액|보고기간종료일(현재)?(장부금액|잔액)?)$")
_INSTRUMENT_RE = re.compile(r"전환사채|신주인수권부사채|교환사채|우선주")
_JOINED_INSTRUMENT_RE = re.compile(r"(전환사채|신주인수권부사채|교환사채|우선주|우선주부채)(및|과|와|,|·)")
_DEFAULT_ASSET_RE = re.compile(r"매도청구권")      # 발행자 콜옵션 — 발행자에게 언제나 자산
_DEFAULT_LIAB_RE = re.compile(r"조기상환청구권|풋옵션|PutOption", re.I)  # 투자자 풋옵션 — 발행자에게 언제나 부채
_OPTION_LIAB_SENT_RE = re.compile(
    r"(?:전환권|신주인수권|교환권|상환권)(?!조정|대가)[^.。]{0,40}?"
    r"(?:파생상품부채|파생금융부채|파생상품|금융부채|부채)\s*(?:으로|로)\s*(?:분류|인식|계상|회계처리)")
_CONDITIONAL_RE = re.compile(r"경우|때에는|때는")
_EQUITY_RE = re.compile(r"자본|대가")
_SENT_ID_RE = re.compile(r"(?:^|:)s:?(\d+)$")
_DERIV_RE = re.compile(r"파생|전환권\s*\(?\s*부채")
_DERIV_LIAB_SENT_RE = re.compile(r"파생[^.,。]{0,30}?부채|전환권\s*\(?\s*부채")
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


def _deriv_title_side(text: str | None) -> str | None:
    """「…파생상품부채의 내역」처럼 파생(상품|금융상품|금융)?(부채|자산)의 마지막 언급으로 쪽을 정한다(제목 앞에 문단이
    붙어 와도 표 바로 앞 문장이 이긴다). 「파생상품자산 및 파생상품부채」처럼 둘이 바로 이어 나오면 모른다."""
    text = re.sub(r"\s+", "", text or "")
    found = list(_SIDE_DERIV_RE.finditer(text))
    if not found:
        return None
    last = found[-1]
    for prev in found[-2::-1]:
        if prev.group(1) != last.group(1) and last.start() - prev.end() <= _JOINED_GAP:
            return None
    return "liability" if last.group(1) == "부채" else "asset"


def _option_term(text: str) -> bool:
    return bool(_OPTION_RE.search(text or "")) and not _OPTION_EXCLUDE_RE.search(text or "")


def _deriv_side(cell: dict) -> str | None:
    """파생상품 칸이면 "liability"·"asset", 쪽을 모르거나 파생상품 칸이 아니면 None.

    부채요소(HOST) 행·합계 행은 언제나 None(Review Focus 3). 그 밖에는 행 종류마다 쪽을 찾는 곳이 다르다.
    - 행 이름에 「파생」: 행 이름 → 열 머리글 → 표·주석 제목의 파생…(부채|자산) → 제목의 부채/자산 낱말.
    - 옵션 낱말 행(행 이름 어느 층이든 전환권·조기상환권·매도청구권 …, 조정·대가·자본 제외): 열 머리글 → 제목 파생 쪽
      → 제목 낱말 → 이름 기본값(매도청구권 = 발행자 콜 = 자산, 조기상환청구권·풋옵션 = 투자자 풋 = 부채; 둘 다면 모름).
    - 기말 행(변동표의 기말·「보고기간종료일 장부금액」): 열 머리글이 파생·옵션이면 그 쪽, 아니면 표·주석 제목의
      파생…(부채|자산).
    - 회차 이름 행(「4회차 전환상환우선주」): 표 제목이 파생…(부채|자산)을 말할 때만.
    """
    label = _norm_label(cell.get("label"))
    if _is_host_cell(cell) or _TOTAL_RE.match(label):
        return None
    parts = [p or "" for p in (cell.get("label_path") or [cell.get("label") or ""])]
    if any(_JOINED_INSTRUMENT_RE.search(_norm_label(p)) for p in parts):
        return None  # 「제13회 전환사채 및 조기상환권」 — 상품 전체와 옵션을 함께 적은 행(옵션만의 금액이 아니다)
    col = re.sub(r"\s+", "", cell.get("col_header") or "")
    tt, nt = cell.get("table_title") or "", cell.get("note_title") or ""
    title_deriv = _deriv_title_side(tt) or _deriv_title_side(nt)
    title_words = _words_side(re.sub(r"\s+", "", tt)) or _words_side(re.sub(r"\s+", "", nt))
    deriv_parts = [p for p in parts if _DERIV_RE.search(p)]
    col_side = _deriv_title_side(col) or (_words_side(col) if (_DERIV_RE.search(col) or _option_term(col)) else None)
    names = " ".join([label, *map(_norm_label, parts), col])
    asset_d, liab_d = bool(_DEFAULT_ASSET_RE.search(names)), bool(_DEFAULT_LIAB_RE.search(names))
    default = None if asset_d == liab_d else ("asset" if asset_d else "liability")
    if deriv_parts:
        return (_words_side(" ".join(deriv_parts)) or _words_side(" ".join(parts)) or col_side or title_deriv
                or title_words)
    if _option_term(label) or any(_option_term(_norm_label(p)) for p in parts if not _INSTRUMENT_RE.search(p)):
        return col_side or title_deriv or title_words or default
    if _CLOSING_RE.match(label):
        if _DERIV_RE.search(col) or _option_term(col):
            return col_side or title_deriv or default
        return title_deriv
    if _INSTRUMENT_RE.search(label):
        return _deriv_title_side(tt)
    return None


def _option_liability_sentence(text: str) -> bool:
    """전환권(신주인수권·교환권·상환권) 자체를 (파생상품)부채로 분류·인식했다는 문장. 조건문(「…경우」)·부정은 아니다."""
    text = text or ""
    for m in _OPTION_LIAB_SENT_RE.finditer(text):
        if _NEGATION_RE.search(text[m.end():]) or _CONDITIONAL_RE.search(text[:m.end()]):
            continue
        return True
    return False


def _positive_deriv_liability_sentence(text: str) -> bool:
    for m in _DERIV_LIAB_SENT_RE.finditer(text or ""):
        if "자산" in m.group(0) or _NEGATION_RE.search(text[m.end():]):
            continue
        return True
    return _option_liability_sentence(text)


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


def _host_sum(ids: list[str], cells: dict, amount: float, want_unit: str) -> list[str] | None:
    """host_carrying 이 인쇄된 칸 하나가 아니라 부채요소 구성 칸(액면·상환할증금·전환권조정 …)의 합일 때.
    댄 근거 id 가 모두 입력에 있고(하나라도 없으면 합으로 보지 않는다), 칸이 2개 이상, 모두 같은 표·같은 열·같은
    단위·같은 배수이고, 파생상품·자본요소 칸이 없으며,
    인쇄된 값(차감 항목은 괄호 음수 그대로)의 합이 금액과 같아야 한다(허용 오차 1)."""
    cs = [(i, cells[i]) for i in ids if i in cells]
    if len(cs) < 2 or len(cs) != len(ids):
        return None
    groups = {(i.rsplit(":r", 1)[0], i.rsplit(":", 1)[1]) for i, _c in cs}
    if len(groups) != 1 or any(c.get("unit_kind") != want_unit for _i, c in cs):
        return None
    if len({c.get("multiplier") for _i, c in cs}) != 1:
        return None
    if any(_deriv_side(c) or _EQUITY_RE.search(_norm_label(c.get("label"))) for _i, c in cs):
        return None
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
        hits = []
        for i in ids:
            c = cells.get(i)
            if c is None or c.get("unit_kind") != want_unit:
                continue
            cv = _num(c.get("value"))
            if amount is not None and cv is not None and abs(cv - amount) <= AMOUNT_TOLERANCE:
                hits.append(i)
        side = _DERIV_AMOUNT_KEYS.get(key)
        good = [i for i in hits if side is None or _deriv_side(cells[i]) == side]
        if not good and key == "host_carrying" and amount is not None and len(ids) == cited.get(field):
            summed = _host_sum(ids, cells, amount, want_unit)
            if summed:
                matched[field] = "+".join(summed)
                matched_mult[field] = cells[summed[0]].get("multiplier")
                evidence[field] = summed
                continue
        if not good:
            holder[key] = None
            evidence[field] = []
            if not hits:
                reasons.append(f"amount_mismatch:{field}")
            elif all(_is_host_cell(cells[i]) for i in hits):
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
        has_deriv = any(_deriv_side(c) == "liability" for c in ev_cells) or any(
            _positive_deriv_liability_sentence(sentences[i]) for i in ids if i in sentences)
        if not has_deriv:
            rec["option_class"] = UNCONFIRMED
            only_host = bool(ev_cells) and all(_is_host_cell(c) for c in ev_cells)
            reasons.append("host_only" if only_host else "no_derivative_ref")

    # 확인된 근거만 남긴다(비어 버린 필드는 빈 목록)
    rec["evidence"] = evidence
    rec["matched"] = matched
    rec["matched_multiplier"] = matched_mult

    # 규칙 5
    if not rec["series_key"].strip():
        rec["series_key"] = _series_key(rec, seq)
    return rec, reasons
