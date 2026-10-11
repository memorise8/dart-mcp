"""표 단위·열 단위 판별 — 단위 종류(원·외화·주·%·개수)와 원 환산 배수."""
import re
from dataclasses import dataclass
from typing import Literal

from ._dart_agent import fs_table

Kind = Literal["krw", "foreign", "shares", "percent", "count", "unknown"]

_UNIT_RE = re.compile(r"\(\s*단\s*위\s*[:：]?\s*([^)]*)\)")
_PAREN_RE = re.compile(r"\(([^()]*)\)\s*$")
_KRW_RE = re.compile(r"(십억|백만|천|억)?\s*원")
_FOREIGN_RE = re.compile(r"(천|백만)?\s*(USD|EUR|JPY|CNY|외화)", re.I)
_COUNT_RE = re.compile(r"^(명|개|건|매)$")
_KRW_MULT = {None: 1, "천": 1_000, "백만": 1_000_000, "십억": 1_000_000_000, "억": 100_000_000}
_FOREIGN_MULT = {None: 1, "천": 1_000, "백만": 1_000_000}


@dataclass(frozen=True)
class Unit:
    raw: str
    kind: Kind
    multiplier: int
    currency: str | None


UNKNOWN = Unit("", "unknown", 1, None)


def _parse(inner: str, raw: str) -> Unit | None:
    """단위 문구(괄호 안) → Unit. 원화 부분이 있으면 원화 우선. 못 읽으면 None."""
    text = re.sub(r"\s+", "", inner or "")
    if not text:
        return None
    m = _KRW_RE.search(text)
    if m:
        mult = _KRW_MULT[m.group(1)]
        if m.group(1) != "억":  # 억원 외에는 dart-agent 판독을 우선한다
            mult = fs_table().read_unit_multiplier(f"(단위:{text})") or mult
        return Unit(raw, "krw", mult, "KRW")
    m = _FOREIGN_RE.search(text)
    if m:
        cur = m.group(2).upper()
        return Unit(raw, "foreign", _FOREIGN_MULT[m.group(1)], "FOREIGN" if cur == "외화" else cur)
    if text == "주":
        return Unit(raw, "shares", 1, None)
    if text in ("%", "％"):
        return Unit(raw, "percent", 1, None)
    if _COUNT_RE.match(text):
        return Unit(raw, "count", 1, None)
    return None


def table_unit(context: str, header_text: str, doc_default: Unit | None) -> tuple[Unit, bool]:
    """표 직전 문구(context)·머리글 문구에서 마지막 「(단위: …)」를 읽는다. (단위, unit_found)."""
    for text in (context, header_text):
        for m in reversed(list(_UNIT_RE.finditer(text or ""))):
            u = _parse(m.group(1), m.group(0))
            if u is not None:
                return u, True
    return (doc_default or UNKNOWN), False


def column_units(columns, table_u: Unit) -> dict[int, Unit]:
    """열 머리글 마지막 요소의 「(주)」·「(천원)」·「(%)」가 표 단위를 덮어쓴다."""
    out: dict[int, Unit] = {}
    for c in columns:
        u = None
        if c.header_path:
            m = _PAREN_RE.search(c.header_path[-1].strip())
            if m:
                u = _parse(m.group(1), f"({m.group(1)})")
        out[c.col] = u or table_u
    return out


def value_krw(value: float, unit: Unit) -> int | None:
    """원화 단위일 때만 원 환산. 주식 수·비율·외화는 None."""
    if unit.kind != "krw":
        return None
    return int(round(value * unit.multiplier))
