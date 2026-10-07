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


# 문장 끝: 「다.」(뒤에 「2.3.19」처럼 번호가 붙어도), 또는 숫자가 뒤따르지 않는 마침표.
_SENT_END_RE = re.compile(r"다\.|\.(?!\d)")
# 문장 앞에 붙은 머리 표시(「1)」「(1)」「①」「2.3.」「2.3.19.3」). 문장은 마지막 머리 뒤에서 시작한다.
_HEAD_MARK_RE = re.compile(r"(?:^|\s)(?:\d{1,2}\)|\(\d{1,2}\)|[①-⑳]|\d{1,2}(?:\.\d{1,2})+\.?)\s")
# 머리 표시 바로 뒤의 짧은 제목 「…인/일 경우」 + 회사 주어. 제목 부분만 걷어 낸다.
_HEAD_LABEL_RE = re.compile(
    r"[^.]{0,30}?(?:인|일)\s*경우\s+(?=(?:회사|당사|연결실체|연결회사|연결기업|지배기업|지배회사)(?:는|은)\s)")
_SENT_REACH = 200  # 문장 넓히기의 앞뒤 최대 거리(표처럼 마침표가 없는 곳에서 끝없이 넓히지 않는다)


def _sentence_around(hay: str, start: int, end: int) -> str:
    """hay[start:end]를 품은 문장. 앞은 직전 문장 끝(또는 200자) 뒤의 마지막 머리 표시 뒤,
    뒤는 다음 문장 끝(또는 다음 머리 표시, 또는 200자)까지."""
    lo = max(0, start - _SENT_REACH)
    ends = list(_SENT_END_RE.finditer(hay, lo, start))
    s0 = ends[-1].end() if ends else lo
    heads = list(_HEAD_MARK_RE.finditer(hay[s0:start]))
    if heads:
        s0 += heads[-1].end()
        # 「1) 회사가 리스이용자인 경우 회사는 …」: 머리 표시 뒤의 「…인 경우」는 제목이지 조건절이 아니다.
        label = _HEAD_LABEL_RE.match(hay, s0)  # 끝 경계는 아래에서 본다(주어가 인용 첫머리일 수 있다)
        if label and label.end() <= start:
            s0 = label.end()
    hi = min(len(hay), end + _SENT_REACH)
    m = _SENT_END_RE.search(hay, max(start, end - 1), hi)
    e0 = m.end() if m else hi
    nxt = _HEAD_MARK_RE.search(hay[end:e0])
    if nxt:
        e0 = end + nxt.start()
    return hay[s0:e0].strip()


def _locate(evidence: str, window: Window) -> str | None:
    """정규화한 근거가 창의 원문 조각 하나, 또는 이웃한 두 조각 안에 있으면 근거를 품은 온전한
    문장(들)을 낸다. 없으면 None.

    - 조각은 `Window.spans` 길이로 되돌린다(원문에 ` … `가 있어도 안전).
    - 이웃 두 조각은 ` … `·공백 하나·빈 문자열로 이어 본다.
    - 근거가 두 문장(`.` 뒤에서 나눔)을 이어 붙인 꼴이면 같은 조각(또는 이웃 쌍) 안에
      두 문장이 순서대로 있을 때 통과한다(가운데 문장 생략, 스파이크 #22).
    - 낸 문장으로 조건문·분류 목록 검사를 한다. 근거가 조건절을 잘라 낸 조각이어도 걸린다."""
    ev = _norm(evidence)
    pieces = [_norm(p) for p in window_pieces(window)]
    hays = list(pieces)
    for a, b in zip(pieces, pieces[1:]):
        hays += [a + JOINER + b, a + " " + b, a + b]
    for h in hays:
        i = h.find(ev)
        if i >= 0:
            return _sentence_around(h, i, i + len(ev))
    for m in _SENT_END_RE.finditer(ev):
        left, right = ev[: m.end()].strip(), ev[m.end():].strip()
        if len(left) < _MIN_PART or len(right) < _MIN_PART:
            continue
        for h in hays:
            i = h.find(left)
            j = h.find(right, i + len(left)) if i >= 0 else -1
            if j >= 0:
                return _sentence_around(h, i, i + len(left)) + " " + _sentence_around(h, j, j + len(right))
    return None


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

# 「인식하지 않」「분류되지 않」 같은 부정형은 필요한 말로 치지 않는다.
_NOT_NEG = r"(?!\s*(?:하지|되지)\s*않)"

_VALUE_WORDS: dict[tuple[str, str], re.Pattern] = {
    ("CONVERSION_OPTION_CLASS", "EQUITY"): re.compile(r"전환권대가|자본요소"),
    ("CONVERSION_OPTION_CLASS", "LIABILITY"): re.compile(r"파생상품부채|금융부채\s*(?:로\s*)?(?:분류|인식)" + _NOT_NEG),
    # 금액은 천 단위 쉼표가 있는 실제 금액만(「개발비(1)」 주석 번호·「20~30년」 범위는 아님).
    ("DEV_COST_CAPITALIZATION", "CAPITALIZED"): re.compile(
        r"개발비\s*(?:\(\*\d*\)\s*)?\(?\d{1,3}(?:,\d{3})+|개발비로\s*(?:인식|자산화)" + _NOT_NEG
    ),
    ("FVOCI_DESIGNATION", "NOT_DESIGNATED"): re.compile(r"지정하지 않"),
    ("LEASE_EXEMPTION", "NONE"): re.compile(r"적용하지 않"),
}
# 근거 문장 전체(넓힌 문장)에서 보는 필요한 말. 「부채요소와 자본요소를 분리」는 내재파생 분리가 아니다.
_SENTENCE_WORDS: dict[tuple[str, str], re.Pattern] = {
    ("EMBEDDED_DERIVATIVE_SEPARATION", "SEPARATED"): re.compile(r"내재|파생"),
}
# FVOCI 지정: 「지정한 지분상품에 대한 투자는 …」「선택을 할 수 있」 같은 기준서 꼴 문장은 회사의 지정 진술이
# 아니다. 같은 문장에 「지정하였·지정하여·선택권을 행사」가 있으면 그대로 둔다.
_FVOCI_TEMPLATE_RE = re.compile(r"지정한\s*지분상품(?:에\s*대한\s*투자는|에서\s*발생)|선택을\s*할\s*수\s*있")
_FVOCI_DESIGNATION_VERB_RE = re.compile(r"지정하였|지정하여|선택권을\s*행사")

# --- 7. 내재파생 분류 목록 문장 ---------------------------------------------------

_CLASSIFICATION_LIST_RE = re.compile(r"분리된 내재파생상품도")

# --- 투자부동산 공정가치(숫자) ---------------------------------------------------

_IP_FV_MODEL_RE = re.compile(r"공정가치\s*모형")
_IP_FV_MODEL_NEG_RE = re.compile(r"원가\s*모형|적용하지\s*않|아니")
_IP_FV_APPROX_RE = re.compile(r"차이가 중요하지 않|근사치|근사한")
_IP_FV_VALUES = ("FV_DIFFERS", "FV_EQUALS")
# 투자부동산 표의 열 머리 낱말. 주석 표시 「(*)」「(*1)」가 붙을 수 있다.
_IP_COL = r"(?:취득\s*원가|(?:감가\s*)?상각\s*누계액|손상\s*차손\s*누계액|(?:정부|국고)\s*보조금|장부\s*금액|공정\s*가치)"
_IP_COL_RUN_RE = re.compile(r"(?:" + _IP_COL + r"(?:\s*\(\*\d*\))?\s*){2,}")
_IP_COL_RE = re.compile(_IP_COL)
_CELL = r"(?:-|\(?\d{1,3}(?:,\d{3})*\)?)"
_IP_ROW_RE = re.compile(r"(?:합\s*계|투자부동산)((?:\s+" + _CELL + r")+)")
_CELL_RE = re.compile(_CELL)
_CURRENT_RE = re.compile(r"당\s*기|\(당\)")
_PRIOR_RE = re.compile(r"전\s*기|\(전\)")
_IP_PERIOD_REACH = 80  # 열 머리 앞에서 당기 표시를 찾는 거리
_IP_ROW_REACH = 400  # 열 머리에서 합계 행까지 최대 거리
_TABLE_END_RE = re.compile(r"다\.|\(\d{1,2}\)\s")


def _amount(tok: str) -> int:
    neg = tok.startswith("(")
    v = int(tok.strip("()").replace(",", ""))
    return -v if neg else v


def _col_name(tok: str) -> str:
    t = re.sub(r"\s", "", tok)
    return "장부금액" if t == "장부금액" else ("공정가치" if t == "공정가치" else "기타")


def _ip_from_piece(seg: str) -> tuple[str, str] | None:
    for run in _IP_COL_RUN_RE.finditer(seg):
        cols = [_col_name(m.group(0)) for m in _IP_COL_RE.finditer(run.group(0))]
        # 당기 열 묶음 안에서 장부금액·공정가치가 붙어 있는 첫 위치 k(당기·전기 묶음이 이어지면 첫 묶음이 당기).
        k = next((i for i in range(len(cols) - 1)
                  if {cols[i], cols[i + 1]} == {"장부금액", "공정가치"}), None)
        if k is None:
            continue
        before_lo = max(0, run.start() - _IP_PERIOD_REACH)
        before = seg[before_lo: run.start()]
        cur = list(_CURRENT_RE.finditer(before))
        if not cur:
            continue
        prior = _PRIOR_RE.search(before)
        if prior and prior.start() < cur[0].start():
            continue
        reach = min(len(seg), run.end() + _IP_ROW_REACH)
        stop = _TABLE_END_RE.search(seg, run.end(), reach)  # 표가 끝나면(문장 끝·다음 소제목) 거기서 멈춘다
        row = _IP_ROW_RE.search(seg, run.end(), stop.start() if stop else reach)
        if row is None:
            continue
        cells = _CELL_RE.findall(row.group(1))  # 「-」도 한 칸이다
        half = len(cols) // 2
        two_period = len(cols) >= 4 and len(cols) % 2 == 0 and cols[:half] == cols[half:]
        # 당기·전기 두 묶음 표에서 빈 칸(「-」도 아닌)이 있으면 열이 밀린다 → 칸 수가 머리 수와 같을 때만 읽는다.
        if two_period and len(cells) != len(cols):
            return None
        if len(cells) < k + 2 or "-" in (cells[k], cells[k + 1]):
            return None  # 열 위치를 확정할 수 없으면 숫자로 정하지 않는다
        a, b = _amount(cells[k]), _amount(cells[k + 1])
        book, fv = (a, b) if cols[k] == "장부금액" else (b, a)
        value = "FV_EQUALS" if book == fv else "FV_DIFFERS"
        return value, seg[before_lo + cur[0].start(): row.end()]
    return None


def ip_fair_value(window_text: str | Window) -> tuple[str, str] | None:
    """창의 투자부동산 표에서 당기 장부금액과 공정가치를 읽어 비교한다.

    보수적으로 읽는다: 열 머리 줄(취득원가·상각누계액·장부금액·공정가치 …)에서 장부금액과
    공정가치가 붙은 열 위치 k를 세고, 머리 앞 80자 안에 당기 표시가 있으며(전기 표시가
    있으면 당기가 먼저), 머리 뒤 400자 안의 「합계」(또는 「투자부동산」) 행에서 k·k+1번째
    칸(「-」도 한 칸)을 읽을 수 있을 때만 (값, 근거)를 낸다. 근거는 당기 표시부터 그
    행까지의 원문이다. 못 읽거나 칸 수가 모자라면 None.

    `Window`를 넘기면 spans로 되돌린 원문 조각마다 본다(원문 속 「 … 」에 안전)."""
    pieces = window_pieces(window_text) if isinstance(window_text, Window) else window_text.split(JOINER)
    for seg in pieces:
        found = _ip_from_piece(_norm(seg))
        if found is not None:
            return found
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
    sentence = _locate(evidence, window)
    if sentence is None:
        return _held(raw, "evidence_not_in_window")
    if _is_conditional(sentence):
        return _held(raw, "conditional")
    words = _VALUE_WORDS.get((defn.code, value))
    # 인용과 넓힌 문장 모두에서 맞아야 한다(인용이 「개발비로 인식」에서 끊기고 문장은 「…하지 않고」인 경우).
    if words is not None and not (words.search(evidence) and words.search(sentence)):
        return _held(raw, "value_words_missing")
    sent_words = _SENTENCE_WORDS.get((defn.code, value))
    if sent_words is not None and not sent_words.search(sentence):
        return _held(raw, "value_words_missing")
    if (defn.code, value) == ("FVOCI_DESIGNATION", "DESIGNATED") and _FVOCI_TEMPLATE_RE.search(sentence) \
            and not _FVOCI_DESIGNATION_VERB_RE.search(sentence):
        return _held(raw, "value_words_missing")
    if defn.code == "EMBEDDED_DERIVATIVE_SEPARATION" and _CLASSIFICATION_LIST_RE.search(sentence):
        return _held(raw, "classification_list")

    if defn.code == "IP_FAIR_VALUE":
        if _IP_FV_MODEL_RE.search(evidence) and not _IP_FV_MODEL_NEG_RE.search(sentence):
            return Verdict("FV_EQUALS", evidence, subject, None, raw)
        nums = ip_fair_value(window)
        if nums is not None:
            return Verdict(nums[0], nums[1], subject, None, raw)
        if value == "FV_EQUALS" and _IP_FV_APPROX_RE.search(evidence):
            return Verdict("FV_EQUALS", evidence, subject, None, raw)
        if value in _IP_FV_VALUES:
            return _held(raw, "ip_numbers_missing")

    return Verdict(value, evidence, subject, None, raw)
