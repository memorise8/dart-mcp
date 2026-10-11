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


_PAREN_RE = re.compile(r"\((?![당전]\))[^)]*\)")
_END_RE = re.compile(r"^(?:제\d+)?\(?([당전])\)?(?:분)?기말$")
_TERM_ONLY_RE = re.compile(r"^(?:제\d+)?\(?([당전])\)?(?:분)?기(?:중)?$")
_FLOW_RE = re.compile("^(?:" + "|".join(_FLOW) + ")$")


def _kind(text: str) -> str:
    """머리글 글자 전체가 기간 낱말일 때만 종류를 돌려준다(부분 일치 금지: 당기순이익·기초자산 등)."""
    t = _PAREN_RE.sub("", _squash(text))
    t = re.sub(r"(?:잔액|액)$", "", t)
    m = _END_RE.match(t)
    if m:
        return "current_end" if m.group(1) == "당" else "prior_end"
    m = _TERM_ONLY_RE.match(t)
    if m:
        return "current_term" if m.group(1) == "당" else "prior_term"
    if t == "기초":
        return "opening"
    if t == "기말":
        return "close"
    if _FLOW_RE.match(t):
        return "flow"
    return ""


def _path_period(path: list[str], context: str) -> str:
    """머리글 경로를 아래에서 위로 보며 판별. 기말·증가 같은 낱말은 위쪽 당기/전기와 합친다."""
    ctx = _flow_period(context)
    for i in range(len(path) - 1, -1, -1):
        k = _kind(path[i])
        if k in ("current_end", "prior_end", "opening"):
            return k
        if k in ("close", "flow"):
            side = ""
            for up in reversed(path[:i]):
                uk = _kind(up)
                if uk in ("current_term", "prior_term"):
                    side = "current" if uk == "current_term" else "prior"
                    break
                if uk in ("current_end", "prior_end"):
                    break
            side = side or ctx
            if not side:
                return "unknown"
            if k == "close":
                return "current_end" if side == "current" else "prior_end"
            return "current_period" if side == "current" else "prior_period"
        if k == "current_term":
            return "current_period"
        if k == "prior_term":
            return "prior_period"
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


_DASHES = {"-", "—", "－", "–"}


def _is_value_col(grid: Grid, c: int) -> bool:
    if c == 0:
        return False
    fs = _dart_agent.fs_table()
    body = grid.rows[grid.header_rows:]
    # 「-」(0)도 금액 칸으로 센다: 잔액이 전부 없는 열(예: FVOCI 지분상품 「-」)도 값 열이다
    hits = sum(1 for r in body if fs.parse_amount(r[c].text) is not None or fs.squash(r[c].text) in _DASHES)
    if grid.fmt == "FINANCE":  # 빈 구분 칸이 끼어 있어 한 줄이라도 금액이 있으면 값 열
        return hits > 0
    return bool(body) and hits * 2 >= len(body)


def _finance_groups(grid: Grid, is_value: list[bool]) -> dict[int, int]:
    """FINANCE 표의 THEAD(TH만인 첫 행)가 「제 n(당) 기」를 COLSPAN으로 두 칸 이상 덮으면,
    값 열을 머리글 칸 단위로 묶는다(열 → 묶음 순번). THEAD가 없으면 빈 dict(열마다 따로)."""
    if not grid.rows or not all(c.is_header for c in grid.rows[0]):
        return {}
    out: dict[int, int] = {}
    seen: list[tuple[int, int]] = []
    for c in range(grid.n_cols):
        if not is_value[c]:
            continue
        origin = grid.rows[0][c].origin
        if origin not in seen:
            seen.append(origin)
        out[c] = seen.index(origin)
    return out


def classify_columns(grid: Grid, period_end: str | None, context: str) -> list[ColumnInfo]:
    paths = header_paths(grid)
    is_value = [_is_value_col(grid, c) for c in range(grid.n_cols)]
    order = _statement_order(context) if grid.fmt == "FINANCE" and grid.header_rows == 0 else []
    groups = _finance_groups(grid, is_value) if order else {}
    ctx_period = _context_period(context)
    out: list[ColumnInfo] = []
    k = 0
    for c in range(grid.n_cols):
        path = paths[c]
        if not is_value[c]:
            out.append(ColumnInfo(c, path, "label", "unknown", None))
            continue
        date = next((d for d in map(_find_date, reversed(path)) if d), None)
        period = _path_period(path, context)
        if period == "unknown":
            period = _date_period(date, period_end)
        g = groups.get(c, k)
        if period == "unknown" and g < len(order):
            period = "current_end" if order[g] == "current" else "prior_end"
        if period == "unknown" and not order:
            period = ctx_period
        k += 1
        out.append(ColumnInfo(c, path, "value", period, date))
    return out
