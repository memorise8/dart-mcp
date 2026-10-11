"""1층 열 기간 판별 — 머리글 낱말 → 날짜 → 문맥(context) → unknown 순."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from . import _dart_agent
from .grid import Grid, header_paths

_WS = re.compile(r"\s+")
_DATE_RES = (
    re.compile(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})"),
    re.compile(r"(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
)
_FLOW = ("증가", "감소", "취득", "처분", "상각", "대체")
_TERM_RE = re.compile(r"제\s*\d+\s*\(\s*[당전]\s*\)\s*기|제\s*\d+\s*기|당\s*기|전\s*기")


@dataclass(frozen=True)
class ColumnInfo:
    col: int
    header_path: list[str]
    role: Literal["label", "value"]
    period: str
    date: str | None


def _squash(s: str) -> str:
    return _WS.sub("", s or "")


def _flow_period(context: str) -> str:
    """증가·기말 같은 열이 어느 기간인지 문맥에서(당기 중/전기 중)."""
    c = _squash(context)
    cur, prev = "당기중" in c, "전기중" in c
    if cur and not prev:
        return "current"
    if prev and not cur:
        return "prior"
    return ""


def _word_period(text: str, context: str) -> str:
    t = _squash(text)
    if any(w in t for w in ("당기말", "당분기말", "(당)기말")):
        return "current_end"
    if any(w in t for w in ("전기말", "(전)기말")):
        return "prior_end"
    if "기초" in t:
        return "opening"
    if "당기" in t or "(당)기" in t:
        return "current_period"
    if "전기" in t or "(전)기" in t:
        return "prior_period"
    if "기말" in t:
        return {"current": "current_end", "prior": "prior_end"}.get(_flow_period(context), "unknown")
    if any(w in t for w in _FLOW):
        return {"current": "current_period", "prior": "prior_period"}.get(_flow_period(context), "unknown")
    return "unknown"


def _find_date(text: str) -> str | None:
    for rx in _DATE_RES:
        m = rx.search(text)
        if m:
            y, mo, d = (int(x) for x in m.groups())
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _norm_end(period_end: str | None) -> str | None:
    if not period_end:
        return None
    digits = re.sub(r"\D", "", period_end)
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) == 8 else None


def _date_period(date: str | None, period_end: str | None) -> str:
    end = _norm_end(period_end)
    if not date or not end:
        return "unknown"
    if date == end:
        return "current_end"
    if date == f"{int(end[:4]) - 1:04d}{end[4:]}":
        return "prior_end"
    return "unknown"


def _context_period(context: str) -> str:
    """문맥 문장에 기간 낱말이 정확히 한 종류만 있을 때만 쓴다."""
    found = set()
    c = _squash(context)
    for w, p in (("당기말", "current_end"), ("당분기말", "current_end"), ("전기말", "prior_end")):
        if w in c:
            found.add(p)
            c = c.replace(w, "")
    if "당기중" in c:
        found.add("current_period")
    if "전기중" in c:
        found.add("prior_period")
    return found.pop() if len(found) == 1 else "unknown"


def _statement_order(context: str) -> list[str]:
    """FINANCE 표: 머리글 문장(「제 12(당) 기 … 제 11(전) 기」)의 기수 순서 → ['current','prior'] 순서.

    dart-agent fs_table.detect_columns 가 당·전기 판별(기수 번호 비교·교차 보정)을 맡는다.
    """
    terms = _TERM_RE.findall(context or "")
    if not terms:
        return []
    tr = "<TR><TD>과목</TD>" + "".join(f"<TD>{t}</TD>" for t in terms) + "</TR>"
    cols = _dart_agent.fs_table().detect_columns(tr)
    if cols is None:
        return []
    order = ["current" if i == cols.cur[0] - 1 else "prior" if cols.prev and i == cols.prev[0] - 1 else ""
             for i in range(len(terms))]
    return [o for o in order if o]


def _is_value_col(grid: Grid, c: int) -> bool:
    if c == 0:
        return False
    parse_amount = _dart_agent.fs_table().parse_amount
    body = grid.rows[grid.header_rows:]
    hits = sum(1 for r in body if parse_amount(r[c].text) is not None)
    if grid.fmt == "FINANCE":  # 빈 구분 칸이 끼어 있어 한 줄이라도 금액이 있으면 값 열
        return hits > 0
    return bool(body) and hits * 2 >= len(body)


def classify_columns(grid: Grid, period_end: str | None, context: str) -> list[ColumnInfo]:
    paths = header_paths(grid)
    is_value = [_is_value_col(grid, c) for c in range(grid.n_cols)]
    order = _statement_order(context) if grid.fmt == "FINANCE" and grid.header_rows == 0 else []
    ctx_period = _context_period(context)
    out: list[ColumnInfo] = []
    k = 0
    for c in range(grid.n_cols):
        path = paths[c]
        if not is_value[c]:
            out.append(ColumnInfo(c, path, "label", "unknown", None))
            continue
        date = next((d for d in map(_find_date, reversed(path)) if d), None)
        period = "unknown"
        for text in reversed(path):  # 가장 아래 머리글부터
            period = _word_period(text, context)
            if period != "unknown":
                break
        if period == "unknown":
            period = _date_period(date, period_end)
        if period == "unknown" and k < len(order):
            period = "current_end" if order[k] == "current" else "prior_end"
        if period == "unknown" and not order:
            period = ctx_period
        k += 1
        out.append(ColumnInfo(c, path, "value", period, date))
    return out
