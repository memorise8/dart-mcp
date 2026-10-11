"""1층 표 장부 — 공시 하나 → 표 기록 목록(스펙 §4.4)과 공시 파일(머리 + 문장 + 표), gz 쓰기·읽기.

표 하나의 상태(§4.5): 격자를 못 맞추면 read_failed(raw_text만), 검사(기간·단위·합계·등식) 중 하나라도
실패하면 needs_review, 아니면 verified. 해당 없는 검사는 None 이고 상태에 영향을 주지 않는다.
"""
from __future__ import annotations

import gzip
import json
import re
from dataclasses import asdict
from pathlib import Path

from . import _dart_agent
from .grid import _CELL_RE, _THEAD_RE, _TR_RE, Grid, parse_table
from .periods import classify_columns, marker_only
from .regions import Spot, plain, scan_file
from .units import Unit, column_units, own_column_units, same_unit, table_unit, value_krw

PARSER_VERSION = "ledger-t2"

_SCOPE_RE = re.compile(r"_(\d{5})\.xml$", re.I)
_TOTAL_RE = re.compile(r"^(합\s*계|총\s*계|계)$")
_SUBTOTAL_RE = re.compile(r"소\s*계")
_MARK_TAIL_RE = re.compile(r"\s*(?:\(\*\d*\)|\*\d*\)?)\s*$")
_ACODE_RE = re.compile(r'ACODE\s*=\s*"?(\d+)"?', re.I)
_ALEVEL_RE = re.compile(r'ALEVEL\s*=\s*"?(\d+)"?', re.I)
_INDENT_RE = re.compile(r"^(?:[ \t　 ]|&nbsp;|&#160;)*")
_INDENT_UNIT_RE = re.compile(r"[ \t　 ]|&nbsp;|&#160;")
_DASHES = {"-", "—", "－", "–"}
SUM_TOLERANCE = 1.0  # 원래 단위 1


# ---------- 공시 단위 ----------
def _acode(name: str) -> str:
    m = _SCOPE_RE.search(name)
    return m.group(1) if m else "main"


def _fs_scope(name: str, text: str) -> str:
    """_00760 별도, _00761 연결, 접미사 없으면 본문 앞 2,000자에 「연결재무제표」가 있으면 연결."""
    code = _acode(name)
    if code == "00760":
        return "separate"
    if code == "00761":
        return "consolidated"
    i = text.find("<BODY")
    head = _dart_agent.fs_table().squash(plain(text[i if i >= 0 else 0:])[:2000])
    return "consolidated" if "연결재무제표" in head else "separate"


def _amends(manifest: dict) -> str | None:
    """[기재정정] 공시이고 원공시 접수번호를 알 때만."""
    names = [manifest.get("report_name") or manifest.get("report_nm") or ""]
    names += [f.get("document_name", "") for f in manifest.get("files", []) if isinstance(f, dict)]
    if not any("[기재정정]" in (n or "") for n in names):
        return None
    orig = manifest.get("orig_rcept_no") or manifest.get("amends_rcept_no")
    return str(orig) if orig else None


def _iso(yyyymmdd: str | None) -> str | None:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return None
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"


# ---------- 행 ----------
def _raw_cells(table_xml: str) -> list[list[tuple[str, str]]]:
    """grid 행 순서대로 TR의 원문 칸 (속성, 안쪽 글). grid._raw_rows 와 같은 행 거르기를 쓴다."""
    out = []
    for tr in _TR_RE.finditer(table_xml):
        cells = [(m.group(2), m.group(3)) for m in _CELL_RE.finditer(tr.group(1))]
        if cells:
            out.append(cells)
    return out


def _cell_meta(grid: Grid, raw: list[list[tuple[str, str]]], origin: tuple[int, int]) -> tuple[int, str | None, int | None]:
    """격자 칸(origin) → 그 칸 원문의 (들여쓰기, ACODE, ALEVEL).

    TR의 k번째 원문 칸 = 그 행에서 시작하는 k번째 격자 칸(위 행 ROWSPAN 자리는 건너뛰고 왼쪽부터 놓이므로)."""
    r, c = origin
    starts = sorted({cell.origin[1] for cell in grid.rows[r] if cell.origin[0] == r})
    if r >= len(raw) or c not in starts or starts.index(c) >= len(raw[r]):
        return 0, None, None
    attrs, inner = raw[r][starts.index(c)]
    a = _ACODE_RE.search(attrs)
    lv = _ALEVEL_RE.search(attrs)
    indent = len(_INDENT_UNIT_RE.findall(_INDENT_RE.match(inner).group(0)))
    return indent, (a.group(1) if a else None), (int(lv.group(1)) if lv else None)


def _is_numberish(text: str, fs) -> bool:
    """금액·비율로 읽히는 글. fs = _dart_agent.fs_table()(호출자가 한 번만 꺼낸다)."""
    return fs.parse_amount(fs.squash(text).rstrip("%％")) is not None


def _stub_cols(cols, grid: Grid) -> tuple[list[int], int | None]:
    """행 머리(첫 값 열 왼쪽의 레이블 열들)와 평평한 표의 대표 레이블 열.

    행 머리에 ROWSPAN 칸이 있으면 계층 표다(대표 열 None → 행마다 가장 구체적인 칸).
    없으면 평평한 표다: 서로 다른 글이 가장 많은 열(같으면 왼쪽)이 대표 열이다
    (「구분=종속기업 / 회사명=…」에서는 회사명, 「회사명 / 소재국가」에서는 회사명)."""
    values = [c.col for c in cols if c.role == "value"]
    first_value = min(values) if values else grid.n_cols
    stub = [c.col for c in cols if c.role == "label" and c.col < first_value] or [0]
    body = grid.rows[grid.header_rows:]
    if len(stub) == 1:
        return stub, stub[0]
    if any(row[c].rowspan > 1 for row in body for c in stub):
        return stub, None
    fs = _dart_agent.fs_table()
    distinct = {c: len({row[c].text for row in body if row[c].text and not _is_numberish(row[c].text, fs)}) for c in stub}
    return stub, max(stub, key=lambda c: (distinct[c], -c))


def _label_cells(grid: Grid, r: int, stub: list[int], main_col: int | None):
    """(대표 레이블 칸, label_path, 레이블 칸들). 행 머리가 여럿이면 숫자·비율 칸은 글 칸이 없을 때만 쓴다.

    계층 표(main_col None)는 가장 구체적인 칸 = 이 행에서 시작한 마지막 비지 않은 칸,
    평평한 표는 대표 열의 칸(비었으면 첫 칸)."""
    cells = [grid.rows[r][c] for c in stub if grid.rows[r][c].text]
    if len(stub) > 1:  # 행 머리가 여럿일 때만 숫자·비율 칸을 뒤로 미룬다(전부 숫자면 그대로 쓴다)
        fs = _dart_agent.fs_table()
        cells = [cell for cell in cells if not _is_numberish(cell.text, fs)] or cells
    path: list[str] = []
    for cell in cells:
        if cell.text not in path:
            path.append(cell.text)
    if not cells:
        return None, path, cells
    if main_col is not None:
        return next((cell for cell in cells if cell.col == main_col), cells[0]), path, cells
    own = [cell for cell in cells if cell.origin[0] == r]
    return (own or cells)[-1], path, cells


def _row_kind(label: str) -> str:
    core = _MARK_TAIL_RE.sub("", label).strip()
    if _TOTAL_RE.match(core):
        return "total"
    if _SUBTOTAL_RE.search(core):
        return "subtotal"
    return "item"


def _value(raw: str) -> int | float | None:
    fs = _dart_agent.fs_table()
    if fs.squash(raw) in _DASHES:
        return 0
    v = fs.parse_amount(raw)
    if v is None:
        return None
    return int(v) if float(v).is_integer() else v


def _unit_dict(u: Unit) -> dict:
    return asdict(u)


# ---------- 검사 ----------
def _sum_ok(rows: list[dict], value_cols: list[int]) -> bool | None:
    """total·subtotal 행 = 바로 위 같은 단계 item 합(열별). 소계 뒤 합계는 소계들 + 마지막 소계 뒤 item 합."""
    results: list[bool] = []
    seg_start = sub_start = 0
    prev_total: dict | None = None
    for i, row in enumerate(rows):
        if row["kind"] == "item":
            continue
        if row["kind"] == "subtotal":
            subs, items = [], rows[sub_start:i]
        else:
            seg = rows[seg_start:i]
            subs = [r for r in seg if r["kind"] == "subtotal"]
            last = max((k for k, r in enumerate(seg) if r["kind"] == "subtotal"), default=-1)
            items = seg[last + 1:]
        items = [r for r in items if r["kind"] == "item"]
        # 해석 셋 중 하나라도 맞으면 통과: 구간 전체 / ROWSPAN 묶음 안(「투입시간 / 합계」) /
        # 합계 행과 같은 단계로 바로 위에 이어진 행들(손익계산서 「계」 ALEVEL 2)
        # 바로 앞 빈 머리 행(「급여비용:」) 아래 행들 / 앞 합계 + 그 뒤 조정 행(「계 / 차감:유동성대체 / 계」)
        readings = [(subs, items), (_same_group(row, subs), _same_group(row, items)), (subs, _level_run(row, items)),
                    ([], _after_blank_parent(items, value_cols))]
        carry = [prev_total] if prev_total is not None and row["kind"] == "total" and not subs and items else []
        for c in value_cols:
            total = _cell_value(row, c)
            if total is None:
                continue
            sums = []
            for r_subs, r_items in readings + ([(carry, items)] if carry else []):
                parts = [_cell_value(r, c) for r in r_subs if _cell_value(r, c) is not None]
                parts += _top_values([(r["level"], _cell_value(r, c)) for r in r_items])
                if parts:
                    sums.append(sum(parts))
            if sums:
                results.append(any(abs(x - total) <= SUM_TOLERANCE for x in sums))
        sub_start = i + 1
        if row["kind"] == "total":
            seg_start = i + 1
            prev_total = row
    return all(results) if results else None


def _after_blank_parent(items: list[dict], value_cols: list[int]) -> list[dict]:
    """마지막 빈 머리 행(값 칸이 모두 빈 item) 뒤 행들. 빈 머리 행이 없거나 맨 끝이면 []."""
    blank = [k for k, r in enumerate(items) if all(_cell_value(r, c) is None for c in value_cols)]
    if not blank or blank[-1] == len(items) - 1:
        return []
    return items[blank[-1] + 1:]


def _level_run(total: dict, items: list[dict]) -> list[dict]:
    """합계 행이 item들보다 깊은 단계면(손익계산서 「계」 ALEVEL 2) 바로 위로 이어진 그 단계 이상 행들만."""
    if not items or total["level"] <= min(r["level"] for r in items):
        return items
    run: list[dict] = []
    for r in reversed(items):
        if r["level"] < total["level"]:
            break
        run.append(r)
    return list(reversed(run)) or items


def _same_group(total: dict, rows: list[dict]) -> list[dict]:
    """합계 행이 ROWSPAN 묶음 안에 있으면(label_path 「투입시간 / 합계」) 같은 묶음(첫 레이블이 같은) 행만.
    같은 묶음 행이 없으면 그대로."""
    path = total.get("label_path") or []
    if len(path) < 2:
        return rows
    mine = [r for r in rows if (r.get("label_path") or [None])[0] == path[0]]
    return mine or rows


def _top_values(items: list[tuple[int, object]]) -> list:
    """가장 얕은 단계 item 값들. 값이 빈 상위 행은 그 아래(더 깊은 단계) 행들로 대신한다."""
    if not items:
        return []
    top = min(lv for lv, _ in items)
    out: list = []
    i = 0
    while i < len(items):
        lv, v = items[i]
        j = i + 1
        while j < len(items) and items[j][0] > top:
            j += 1
        if lv == top:
            out += [v] if v is not None else _top_values(items[i + 1:j])
        else:  # 첫 상위 행보다 앞선 깊은 행들
            out += _top_values(items[i:j])
        i = j
    return out


def _cell_value(row: dict, col: int):
    for c in row["cells"]:
        if c["col"] == col:
            return c["value"]
    return None


def _identity_ok(rows: list[dict], columns: list[dict]) -> bool | None:
    """재무상태표: 자산 = 부채 + 자본(fs_table.check_identities, 원래 단위로 허용 오차 1)."""
    fs = _dart_agent.fs_table()
    cur = [c["col"] for c in columns if c.get("period") == "current_end"]
    prev = [c["col"] for c in columns if c.get("period") == "prior_end"]
    if not cur:
        return None

    def pick(row: dict, cols: list[int]):
        for col in reversed(cols):  # fs_table._pick_amount 처럼 오른쪽부터
            for c in row["cells"]:
                if c["col"] == col and c["value"] is not None and fs.parse_amount(c["raw"]) is not None:
                    return c["raw"], float(c["value"])
        return "", None

    fs_rows = []
    for i, r in enumerate(rows):
        label = fs.squash(r["label"])
        craw, cnum = pick(r, cur)
        praw, pnum = pick(r, prev)
        fs_rows.append(fs.Row(i, label, fs.normalize_account_name(label), r["acode"], r["level"] if r["acode"] else None,
                              craw, cnum, praw, pnum))
    fs.canonicalize_totals(fs_rows)
    return fs.check_identities(fs_rows, 1).identity_ok


# ---------- 표 하나 ----------
def _header_text(grid: Grid, table_xml: str) -> str:
    texts = [c.text for row in grid.rows[:grid.header_rows] for c in row]
    if grid.fmt == "FINANCE":  # FINANCE 는 header_rows=0 이라 THEAD 글을 따로 붙인다
        texts += [plain(m.group(1)) for m in _THEAD_RE.finditer(table_xml)]
    return " ".join(t for t in texts if t)


def _title_fallback(grid: Grid) -> str | None:
    n = grid.header_rows or 1
    for row in grid.rows[:n]:
        seen: list[str] = []
        for c in row:
            if c.text and c.text not in seen:
                seen.append(c.text)
        t = " ".join(seen)
        if re.sub(r"\(\s*단\s*위[^)]*\)", "", t).strip():
            return t[:200]
    return None


def _table_record(spot: Spot, base: dict, doc_unit: Unit | None, period_end: str | None,
                  pair_unit: Unit | None = None) -> tuple[dict, Unit | None]:
    """pair_unit: 같은 주석의 바로 앞 표가 찾은 단위. 이 표 앞 글이 비었거나 「〈전기〉」 표지뿐이면(당기·전기로 나뉜
    한 공시 표) 그 단위를 이어받는다."""
    rec = dict(base)
    rec.update(page=spot.page, region=spot.region, title=spot.title_p, footnotes=spot.footnotes)
    grid = parse_table(spot.xml)
    if grid is None:
        rec.update(unit=None, columns=[], rows=[], status="read_failed", issues=["grid_unresolved"],
                   checks={"period_found": None, "unit_found": None, "sum_ok": None, "identity_ok": None},
                   raw_text=plain(spot.xml), parser_version=PARSER_VERSION)
        return rec, None
    header_text = _header_text(grid, spot.xml)
    if not rec["title"]:
        rec["title"] = _title_fallback(grid)
    # FINANCE 기수 순서는 THEAD(「제 26(당) 기 / 제 25(전) 기」)에서, 없으면 표 앞 제목 표에서 읽는다
    # NORMAL 표는 단위 표가 넘겨준 앞 문맥(lead)을 앞에 붙인다(마지막 줄 = 표 바로 앞 표지)
    near = "\n".join(x for x in (spot.lead, spot.context) if x)
    context = (header_text or spot.context) if grid.fmt == "FINANCE" and grid.header_rows == 0 else near
    cols = classify_columns(grid, period_end, context)
    t_unit, t_found = table_unit(spot.context, header_text, doc_unit)
    if not t_found and pair_unit is not None and marker_only(near):
        t_unit, t_found = pair_unit, True
    c_units = column_units(cols, t_unit)
    value_cols = [c.col for c in cols if c.role == "value"]
    own_units = own_column_units(cols)
    raw_cells = _raw_cells(spot.xml)
    stub, main_col = _stub_cols(cols, grid)

    rows = []
    for r in range(grid.header_rows, len(grid.rows)):
        row = grid.rows[r]
        if all(c.is_header for c in row):
            continue
        best, label_path, lcells = _label_cells(grid, r, stub, main_col)
        label = best.text if best is not None else ""
        cells = []
        for c in value_cols:
            cell = row[c]
            if cell.origin != (r, c):  # 병합 칸은 원래 자리에만 둔다
                continue
            v = _value(cell.text)
            u = c_units[c]
            cells.append({"col": c, "raw": cell.text, "value": v,
                          "value_krw": value_krw(v, u) if v is not None else None,
                          "unit_override": None if same_unit(u, t_unit) else _unit_dict(u)})
        if not label and all(x["value"] is None for x in cells):
            continue
        indent, acode, alevel = _cell_meta(grid, raw_cells, best.origin) if best is not None else (0, None, None)
        if acode is None and grid.fmt == "FINANCE":
            acode = _cell_meta(grid, raw_cells, grid.rows[r][0].origin)[1]
        kinds = {_row_kind(c.text) for c in lcells}
        kind = _row_kind(label)
        if kind == "item":  # 「합계」·「소계」가 다른 레이블 열에 있어도 잡는다
            kind = "total" if "total" in kinds else "subtotal" if "subtotal" in kinds else "item"
        rows.append({"row": r, "label": label, "label_path": label_path,
                     "level": alevel if alevel is not None else indent,
                     "acode": acode, "kind": kind, "cells": cells})

    columns = []
    for c in cols:
        d = {"col": c.col, "header_path": c.header_path, "role": c.role}
        if c.role == "value":
            d.update(period=c.period, date=c.date)
        columns.append(d)

    has_values = bool(value_cols)
    # 표 단위를 찾았거나, 모든 값 열이 머리글에 제 단위를 가졌으면 단위를 찾은 것
    unit_found = (t_found or all(c in own_units for c in value_cols)) if has_values else None
    checks = {
        "period_found": any(c.period != "unknown" for c in cols if c.role == "value") if has_values else None,
        "unit_found": unit_found,
        "sum_ok": _sum_ok(rows, value_cols),
        "identity_ok": _identity_ok(rows, columns) if spot.region["kind"] == "statement_bs" else None,
    }
    issues = [name for name, key in (("period_unknown", "period_found"), ("unit_missing", "unit_found"),
                                      ("sum_mismatch", "sum_ok"), ("identity_fail", "identity_ok"))
              if checks[key] is False]
    rec.update(unit=_unit_dict(t_unit), columns=columns, rows=rows, checks=checks, issues=issues,
               status="needs_review" if issues else "verified", parser_version=PARSER_VERSION)
    return rec, (t_unit if t_found else None)


# ---------- 공시 ----------
def _decode(raw: bytes) -> str:
    return _dart_agent.note_section().decode_xml(raw)


def _filing_parts(rcept_no: str, files: list[tuple[str, bytes]], manifest: dict):
    amends = _amends(manifest or {})
    tables: list[dict] = []
    paragraphs: list[tuple[str, int, str]] = []
    period_end: str | None = None
    texts = [(name, _decode(raw)) for name, raw in files]
    scans = []
    for name, text in texts:
        scan = scan_file(text)
        scans.append((name, text, scan))
        pe = _dart_agent.fs_table().read_period_end(scan.cover_text)
        if pe and (period_end is None or pe > period_end):
            period_end = pe
    for name, text, scan in scans:
        scope = _fs_scope(name, text)
        acode = _acode(name)
        doc_unit: Unit | None = None
        prev: tuple[dict, Unit | None] | None = None  # (앞 표 region, 앞 표가 찾은 단위)
        for spot in scan.spots:
            pair_unit = None
            # 같은 주석 안(소제목 ①·② 는 당기·전기 나눔일 수 있어 보지 않는다 — 앞 글이 표지뿐인지는 _table_record가 본다)
            if prev is not None and spot.region["kind"] == "note" and prev[0]["kind"] == "note" \
                    and prev[0]["note_no"] == spot.region["note_no"]:
                pair_unit = prev[1]
            base = {"table_id": f"{rcept_no}:{acode}:t{spot.index + 1:03d}", "rcept_no": rcept_no,
                    "amends_rcept_no": amends, "file": name, "fs_scope": scope}
            try:
                rec, found_unit = _table_record(spot, base, doc_unit, period_end, pair_unit)
            except Exception as exc:  # 표 하나 때문에 공시 전체를 잃지 않는다
                rec = dict(base, page=spot.page, region=spot.region, title=spot.title_p, footnotes=spot.footnotes,
                           unit=None, columns=[], rows=[], status="read_failed", issues=[f"error:{type(exc).__name__}"],
                           checks={"period_found": None, "unit_found": None, "sum_ok": None, "identity_ok": None},
                           raw_text=plain(spot.xml), parser_version=PARSER_VERSION)
                found_unit = None
            if doc_unit is None and found_unit is not None and spot.region["kind"].startswith("statement_"):
                doc_unit = found_unit  # 공시 기본 단위 = 본문 재무제표에서 처음 찾은 단위
            tables.append(rec)
            prev = (spot.region, found_unit)
        paragraphs.extend((name, pg, t) for pg, t in scan.paragraphs)
    return amends, _iso(period_end), paragraphs, tables


def build_filing_tables(rcept_no: str, files: list[tuple[str, bytes]], manifest: dict) -> list[dict]:
    return _filing_parts(rcept_no, files, manifest)[3]


def build_filing(rcept_no: str, files: list[tuple[str, bytes]], manifest: dict) -> dict:
    from dart_search_mcp.policy_sentences import split_sentences

    amends, period_end, paras, tables = _filing_parts(rcept_no, files, manifest)
    sentences = []
    for name, page, text in paras:
        for s in split_sentences(text):
            t = s.text.strip()
            if t:
                sentences.append({"id": f"s{len(sentences) + 1}", "file": name, "page": page, "text": t})
    return {"rcept_no": rcept_no, "amends_rcept_no": amends, "period_end": period_end,
            "parser_version": PARSER_VERSION, "paragraphs": sentences, "tables": tables}


def write_filing(out_dir: Path, rcept_no: str, filing: dict) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{rcept_no}.json.gz"
    tmp = path.with_suffix(".gz.tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(filing, fh, ensure_ascii=False, separators=(",", ":"))
    tmp.replace(path)
    return path


def load_filing(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)
