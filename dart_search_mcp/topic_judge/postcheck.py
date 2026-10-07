"""WS8 판정 사후 검사(스펙 §4.3).

모델 답(dict)을 받아 규칙을 순서대로 검사하고, 첫 실패 사유를 `held_reason`에
둔 UNDETERMINED를 낸다. 투자부동산 공정가치는 창의 숫자로 결정한다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from dart_search_mcp.audit_xml_parser import _normalize_text

from .topics import TopicDef
from .window import JOINER, Window, window_pieces

UNDETERMINED = "UNDETERMINED"


@dataclass(frozen=True)
class Verdict:
    value: str
    evidence: str  # 통과한 근거(공백 정규화). UNDETERMINED면 빈 문자열(모델 근거는 raw에 남는다)
    subject: str   # 모델이 낸 subject 그대로
    held_reason: str | None
    raw: dict | None


def _norm(s: str) -> str:
    """doc_text와 같은 정규화(공백 하나로·갈라진 어미 닫기) + NFC."""
    return unicodedata.normalize("NFC", _normalize_text(s or ""))


# --- 4. 근거가 창 안에 있는가 -------------------------------------------------

_MIN_PART = 10  # 근거를 두 조각으로 나눌 때 조각의 최소 길이


def _in_window(evidence: str, window: Window) -> bool:
    """정규화한 근거가 창의 원문 조각 하나, 또는 이웃한 두 조각 안에 있는가.

    - 조각은 `Window.spans` 길이로 되돌린다(원문에 ` … `가 있어도 안전).
    - 이웃 두 조각은 ` … `·공백 하나·빈 문자열로 이어 본다.
    - 근거가 두 문장(`.` 뒤에서 나눔)을 이어 붙인 꼴이면 같은 조각(또는 이웃 쌍) 안에
      두 문장이 순서대로 있을 때 통과한다(가운데 문장 생략, 스파이크 #22)."""
    ev = _norm(evidence)
    pieces = [_norm(p) for p in window_pieces(window)]
    hays = list(pieces)
    for a, b in zip(pieces, pieces[1:]):
        hays += [a + JOINER + b, a + " " + b, a + b]
    if any(ev in h for h in hays):
        return True
    for m in re.finditer(r"\.(?!\d)", ev):
        left, right = ev[: m.end()].strip(), ev[m.end():].strip()
        if len(left) < _MIN_PART or len(right) < _MIN_PART:
            continue
        for h in hays:
            i = h.find(left)
            if i >= 0 and h.find(right, i + len(left)) >= 0:
                return True
    return False


# --- 5. 조건문 -----------------------------------------------------------------

_CONDITIONAL_RE = re.compile(r"경우|라면|충족하는|때에는")
_AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})+")
_PROPER_RE = re.compile(r"제\s*\d+\s*회")
_CHOICE_RE = re.compile(r"선택하여|적용하지 않습니다|지정하였습니다")


def _is_conditional(ev: str) -> bool:
    if not _CONDITIONAL_RE.search(ev):
        return False
    return not (_AMOUNT_RE.search(ev) or _PROPER_RE.search(ev) or _CHOICE_RE.search(ev))


# --- 6. 값마다 필요한 말 --------------------------------------------------------

_VALUE_WORDS: dict[tuple[str, str], re.Pattern] = {
    ("CONVERSION_OPTION_CLASS", "EQUITY"): re.compile(r"전환권대가|자본요소"),
    ("CONVERSION_OPTION_CLASS", "LIABILITY"): re.compile(r"파생상품부채|금융부채\s*(?:로\s*)?(?:분류|인식)"),
    ("DEV_COST_CAPITALIZATION", "CAPITALIZED"): re.compile(
        r"개발비\s*(?:\(\*?\d*\)\s*)?\(?\d[\d,]*\)?(?![\d,]*\s*(?:년|개월|%))|개발비로\s*(?:인식|자산화)"
    ),
    ("FVOCI_DESIGNATION", "NOT_DESIGNATED"): re.compile(r"지정하지 않"),
    ("LEASE_EXEMPTION", "NONE"): re.compile(r"적용하지 않"),
}

# --- 7. 내재파생 분류 목록 문장 ---------------------------------------------------

_CLASSIFICATION_LIST_RE = re.compile(r"분리된 내재파생상품도")

# --- 투자부동산 공정가치(숫자) ---------------------------------------------------

_IP_FV_MODEL_RE = re.compile(r"공정가치모형")
_IP_FV_APPROX_RE = re.compile(r"차이가 중요하지 않|근사치|근사한")
_IP_FV_VALUES = ("FV_DIFFERS", "FV_EQUALS")
# 「장부금액 공정가치」 또는 「공정가치 장부금액」 열 머리(당기·전기 두 쌍이 이어질 수 있다).
_IP_HEADER_RE = re.compile(r"(장부\s*금액|공정\s*가치)\s+(장부\s*금액|공정\s*가치)")
_NUM = r"\(?\d{1,3}(?:,\d{3})*\)?"
_IP_ROW_RE = re.compile(r"(?:합\s*계|투자부동산)\s+(" + _NUM + r")\s+(" + _NUM + r")")
_CURRENT_RE = re.compile(r"당\s*기|\(당\)")
_PRIOR_RE = re.compile(r"전\s*기|\(전\)")
_IP_ROW_REACH = 400  # 열 머리에서 합계 행까지 최대 거리
_TABLE_END_RE = re.compile(r"다\.|\(\d{1,2}\)\s")


def _amount(tok: str) -> int:
    neg = tok.startswith("(")
    v = int(tok.strip("()").replace(",", ""))
    return -v if neg else v


def ip_fair_value(window_text: str) -> tuple[str, str] | None:
    """창의 투자부동산 표에서 당기 장부금액과 공정가치를 읽어 비교한다.

    보수적으로 읽는다: 「장부금액 공정가치」(순서 무관) 열 머리가 있고, 머리 바로
    앞에 당기 표시가 있으며(전기 표시가 있으면 당기가 먼저), 머리 뒤 400자 안의
    「합계」(또는 단일 「투자부동산」) 행에서 첫 두 금액(=당기 열)을 읽을 수 있을 때만
    (값, 근거)를 낸다. 근거는 열 머리부터 그 행까지의 원문이다. 못 읽으면 None."""
    for seg in window_text.split(JOINER):
        seg = _norm(seg)
        for h in _IP_HEADER_RE.finditer(seg):
            first, second = (re.sub(r"\s", "", g) for g in h.groups())
            if first == second:
                continue
            before = seg[max(0, h.start() - 60): h.start()]
            cur = list(_CURRENT_RE.finditer(before))
            if not cur:
                continue
            prior = _PRIOR_RE.search(before)
            if prior and prior.start() < cur[0].start():
                continue
            reach = min(len(seg), h.end() + _IP_ROW_REACH)
            stop = _TABLE_END_RE.search(seg, h.end(), reach)  # 표가 끝나면(문장 끝·다음 소제목) 거기서 멈춘다
            row = _IP_ROW_RE.search(seg, h.end(), stop.start() if stop else reach)
            if row is None:
                continue
            a, b = _amount(row.group(1)), _amount(row.group(2))
            book, fv = (a, b) if first == "장부금액" else (b, a)
            value = "FV_EQUALS" if book == fv else "FV_DIFFERS"
            start = cur[0].start() + max(0, h.start() - 60)
            return value, seg[start: row.end()]
    return None


def _held(raw: dict, reason: str) -> Verdict:
    return Verdict(UNDETERMINED, "", str(raw.get("subject") or ""), reason, raw)


def postcheck(defn: TopicDef, raw: dict, window: Window) -> Verdict:
    value = raw.get("value")
    subject = str(raw.get("subject") or "")
    evidence = _norm(raw.get("evidence") if isinstance(raw.get("evidence"), str) else "")

    if not isinstance(value, str) or value not in defn.values:
        return _held(raw, "invalid_value")
    if value == UNDETERMINED:
        return _held(raw, "model_undetermined")
    if subject != "self":
        return _held(raw, "not_self")
    if not evidence:
        return _held(raw, "evidence_missing")
    if not _in_window(evidence, window):
        return _held(raw, "evidence_not_in_window")
    if _is_conditional(evidence):
        return _held(raw, "conditional")
    words = _VALUE_WORDS.get((defn.code, value))
    if words is not None and not words.search(evidence):
        return _held(raw, "value_words_missing")
    if defn.code == "EMBEDDED_DERIVATIVE_SEPARATION" and _CLASSIFICATION_LIST_RE.search(evidence):
        return _held(raw, "classification_list")

    if defn.code == "IP_FAIR_VALUE":
        if _IP_FV_MODEL_RE.search(evidence):
            return Verdict("FV_EQUALS", evidence, subject, None, raw)
        nums = ip_fair_value(window.text)
        if nums is not None:
            return Verdict(nums[0], nums[1], subject, None, raw)
        if value == "FV_EQUALS" and _IP_FV_APPROX_RE.search(evidence):
            return Verdict("FV_EQUALS", evidence, subject, None, raw)
        if value in _IP_FV_VALUES:
            return _held(raw, "ip_numbers_missing")

    return Verdict(value, evidence, subject, None, raw)
