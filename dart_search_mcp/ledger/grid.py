"""1층 칸 격자 — NORMAL·FINANCE 표 XML을 병합(ROWSPAN·COLSPAN)을 펼친 격자로 바꾼다."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from . import _dart_agent

MAX_HEADER_ROWS = 3

_TR_RE = re.compile(r"<TR\b[^>]*>(.*?)</TR>", re.S | re.I)
_CELL_RE = re.compile(r"<(TH|TD|TE|TU)\b([^>]*)>(.*?)</\1>", re.S | re.I)
_THEAD_RE = re.compile(r"<THEAD\b[^>]*>(.*?)</THEAD>", re.S | re.I)
_COLSPAN_RE = re.compile(r'COLSPAN\s*=\s*"?(\d+)"?', re.I)
_ROWSPAN_RE = re.compile(r'ROWSPAN\s*=\s*"?(\d+)"?', re.I)


@dataclass(frozen=True)
class Cell:
    text: str
    row: int
    col: int
    rowspan: int
    colspan: int
    is_header: bool
    origin: tuple[int, int]


@dataclass
class Grid:
    rows: list[list[Cell]]
    n_cols: int
    header_rows: int
    fmt: Literal["NORMAL", "FINANCE"]


@dataclass(frozen=True)
class _RawCell:
    text: str
    rowspan: int
    colspan: int
    is_header: bool
    is_te: bool = False


def _span(pattern: re.Pattern[str], attrs: str) -> int:
    m = pattern.search(attrs)
    return int(m.group(1)) if m else 1


_PSEUDO_TAG_RE = re.compile(r"<\s*([가-힣][^<>]{0,30}?)\s*>")


def clean_text(s: str) -> str:
    """fs_table.clean_text 앞에 한글 꺾쇠 표지(원문 「<당기>」「<금융자산>」 — 태그가 아니라 글)를 〈…〉로 지킨다."""
    return _dart_agent.fs_table().clean_text(_PSEUDO_TAG_RE.sub(r"〈\1〉", s or ""))


def _raw_rows(xml: str) -> list[list[_RawCell]]:
    rows: list[list[_RawCell]] = []
    for tr in _TR_RE.finditer(xml):
        cells = [
            _RawCell(
                clean_text(m.group(3)),
                _span(_ROWSPAN_RE, m.group(2)),
                _span(_COLSPAN_RE, m.group(2)),
                m.group(1).upper() == "TH",
                m.group(1).upper() == "TE",
            )
            for m in _CELL_RE.finditer(tr.group(1))
        ]
        if cells:
            rows.append(cells)
    return rows


def _expand(tr_cells: list[list[_RawCell]]) -> list[list[Cell]] | None:
    grid: dict[tuple[int, int], Cell] = {}
    for r, cells in enumerate(tr_cells):
        c = 0
        for raw in cells:
            while (r, c) in grid:  # 위 행 ROWSPAN이 차지한 칸 건너뛰기
                c += 1
            if raw.rowspan < 1 or raw.colspan < 1:
                return None
            for dr in range(raw.rowspan):
                for dc in range(raw.colspan):
                    grid[(r + dr, c + dc)] = Cell(
                        raw.text, r + dr, c + dc, raw.rowspan, raw.colspan, raw.is_header, (r, c)
                    )
            c += raw.colspan
    if not grid:
        return None
    n_rows = max(k[0] for k in grid) + 1
    n_cols = max(k[1] for k in grid) + 1
    return [
        [grid.get((r, c), Cell("", r, c, 1, 1, False, (r, c))) for c in range(n_cols)]
        for r in range(n_rows)
    ]


def _ragged(raw_rows: list[list[_RawCell]], expanded: list[list[Cell]]) -> bool:
    """ROWSPAN 아래 행이 표 밖으로 넘치거나, 어떤 행에 격자 칸이 하나도 안 채워졌으면 열 수를 맞출 수 없다."""
    if len(expanded) != len(raw_rows):
        return True
    n_cols = len(expanded[0])
    for r, row in enumerate(raw_rows):
        # 이 행이 직접 놓은 칸 + 위 행에서 내려온 칸이 n_cols를 정확히 채워야 한다.
        placed = sum(c.colspan for c in row)
        carried = sum(1 for c in expanded[r] if c.origin[0] < r)
        if placed + carried != n_cols:
            return True
    return False


def _header_rows(grid_rows: list[list[Cell]], thead_rows: int) -> int:
    if thead_rows:
        return min(thead_rows, len(grid_rows))
    parse_amount = _dart_agent.fs_table().parse_amount
    count = 0
    for row in grid_rows[:MAX_HEADER_ROWS]:
        if any(parse_amount(c.text) is not None for c in row):
            break
        count += 1
    return min(count, len(grid_rows) - 1)  # 본문 행이 최소 하나는 남아야 한다


def _finance_td_header_rows(raw: list[list[_RawCell]], grid_rows: list[list[Cell]]) -> int:
    """THEAD 없는 FINANCE 표에서 TE 칸이 하나도 없고 금액도 없는 앞 행들(감사참여자 표처럼 머리글은 TD, 숫자는 TE).
    본문 재무제표(모든 행이 TE)는 0 그대로다."""
    parse_amount = _dart_agent.fs_table().parse_amount
    count = 0
    for r, row in enumerate(raw[:MAX_HEADER_ROWS]):
        if any(c.is_te for c in row) or any(parse_amount(c.text) is not None for c in grid_rows[r]):
            break
        count += 1
    return min(count, len(grid_rows) - 1)


def parse_table(table_xml: str) -> Grid | None:
    """표 XML → Grid. 열 수를 맞출 수 없으면 None."""
    raw = _raw_rows(table_xml)
    if not raw:
        return None
    fmt: Literal["NORMAL", "FINANCE"] = (
        "FINANCE" if re.search(r"<TE\b", table_xml, re.I) else "NORMAL"
    )
    expanded = _expand(raw)
    if expanded is None or _ragged(raw, expanded):
        return None
    if fmt == "FINANCE":
        header_rows = 0 if _THEAD_RE.search(table_xml) else _finance_td_header_rows(raw, expanded)
    else:
        thead_rows = 0
        for m in _THEAD_RE.finditer(table_xml):
            thead_rows += len(_raw_rows(m.group(1)))
        header_rows = _header_rows(expanded, thead_rows)
    return Grid(expanded, len(expanded[0]), header_rows, fmt)


def header_paths(grid: Grid) -> list[list[str]]:
    """열마다 위에서 아래로 머리글 문자열(빈 것·바로 위와 같은 것 제거)."""
    paths: list[list[str]] = []
    for c in range(grid.n_cols):
        path: list[str] = []
        for r in range(grid.header_rows):
            text = grid.rows[r][c].text
            if text and (not path or path[-1] != text):
                path.append(text)
        paths.append(path)
    return paths
