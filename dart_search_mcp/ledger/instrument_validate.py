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
_OPTION_EXCLUDE_RE = re.compile(r"대가|자본")
_TITLE_SIDE_RE = re.compile(r"파생\s*상품\s*(부채|자산)")
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


def _title_side(cell: dict) -> str | None:
    sides = {m.group(1) for k in ("table_title", "note_title")
             for m in _TITLE_SIDE_RE.finditer(re.sub(r"\s+", "", cell.get(k) or ""))}
    if len(sides) != 1:
        return None
    return "liability" if sides == {"부채"} else "asset"


def _deriv_side(cell: dict) -> str | None:
    """파생상품 칸이면 "liability"·"asset", 쪽을 모르거나 파생상품 칸이 아니면 None."""
    parts = [p or "" for p in (cell.get("label_path") or [cell.get("label") or ""])]
    deriv = [p for p in parts if _DERIV_RE.search(p)]
    if not deriv:
        label = _norm_label(cell.get("label"))
        if _OPTION_RE.search(label) and not _is_host_cell(cell) and not _OPTION_EXCLUDE_RE.search(label):
            return _title_side(cell)
        return None
    for group in (deriv, parts):
        text = " ".join(group)
        liab, asset = "부채" in text, "자산" in text
        if liab != asset:
            return "liability" if liab else "asset"
    return None


def _positive_deriv_liability_sentence(text: str) -> bool:
    for m in _DERIV_LIAB_SENT_RE.finditer(text or ""):
        if "자산" in m.group(0) or _NEGATION_RE.search(text[m.end():]):
            continue
        return True
    return False


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
    for field, ids in evidence_in.items():
        ids = ids if isinstance(ids, list) else [ids]
        good = [i for i in ids if isinstance(i, str) and (i in cells or i in sentences)]
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
