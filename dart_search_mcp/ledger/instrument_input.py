"""2층 ② 사채·우선주 기록 — 대상 공시 고르기와 LLM 입력 묶음(스펙 §5.3, 브리프 T8).

입력 묶음은 칸 번호(`<table_id>:r<row>:c<col>`)와 문장 번호(1층 문단 id `s<n>`, 표 각주 `f:<table_id>:<k>`)를
붙인 글이다. LLM 은 이 번호로만 근거를 대고, instrument_validate 가 번호·값을 코드로 확인한다.
"""
from __future__ import annotations

import re

MAX_TABLES = 40
MAX_SENTENCES = 200

INSTRUMENT_FACT_CONCEPTS = frozenset({
    "HOST_LIABILITY", "CONV_OPTION_EQUITY", "CONV_OPTION_LIABILITY", "EMB_DERIV_LIABILITY", "EMB_DERIV_ASSET",
})
# 전환·신주인수권·교환·우선주 — 일반 사채만 있는 공시는 대상이 아니다(2층 ①도 일반 사채를 HOST 로 잡지 않는다).
_CONVERTIBLE_RE = re.compile(r"전환|신주인수권|교환|우선주")
_BOND_RE = re.compile(r"사채")
_TARGET_RE = re.compile(r"사채|전환|신주인수권|교환|우선주|파생|금융상품|공정가치|자본잉여금")
_ROW_RE = re.compile(r"전환|신주인수권|교환사채|교환권|우선주|파생")
_NOTE_BLOCK_RE = re.compile(r"법인세|이연|현금흐름|주당")
_HEADING_RE = re.compile(r"^\s*\d{1,3}\s*\.\s*(\S.{0,40})$")
_UNIT_HEAD_RE = re.compile(r"^\(?\s*단위\s*[:：]")
# 발행조건 같은 글 표(값 열 없음)의 「35,000,000,000원」 — 통화 낱말이 붙은 금액만 칸으로 낸다.
_TEXT_AMOUNT_RE = re.compile(r"^\s*(\()?\s*(-)?\s*(\d{1,3}(?:,\d{3})+|\d+)\s*(\))?\s*(원|천원|백만원)\s*$")
_TEXT_MULT = {"원": 1, "천원": 1000, "백만원": 1_000_000}


def needs_instruments(facts: list[dict], tables: list[dict]) -> bool:
    """사채·우선주 기록이 필요한 공시인가.

    HOST_LIABILITY·CONV_*·EMB_* 사실이 있거나, 주석 제목에 전환·신주인수권·교환·우선주가 있으면 True.
    「사채」만 있는 주석 제목(일반 사채)은 대상이 아니다.
    """
    if any(f.get("concept") in INSTRUMENT_FACT_CONCEPTS for f in facts):
        return True
    for t in tables:
        region = t.get("region") or {}
        if region.get("kind") == "note" and _CONVERTIBLE_RE.search(region.get("note_title") or ""):
            return True
    return False


def _heading_text(t: dict) -> str:
    region = t.get("region") or {}
    return " ".join([region.get("note_title") or "", *(region.get("sub_path") or []), t.get("title") or ""])


def _row_text(t: dict) -> str:
    return " ".join(" ".join(r.get("label_path") or [r.get("label") or ""]) for r in t.get("rows") or [])


def _table_tier(t: dict) -> int | None:
    """작을수록 먼저. None = 넣지 않음."""
    region = t.get("region") or {}
    kind = region.get("kind")
    head = _heading_text(t)
    rows = _row_text(t)
    if kind == "note":
        if _NOTE_BLOCK_RE.search(region.get("note_title") or ""):
            return None
        if _CONVERTIBLE_RE.search(head) or (_BOND_RE.search(head) and _ROW_RE.search(rows)):
            return 0
        if _BOND_RE.search(head) or _ROW_RE.search(rows):
            return 1
        if _TARGET_RE.search(head):
            return 2
        return None
    if kind and kind.startswith("statement_") and _ROW_RE.search(rows):
        return 3
    return None


def _cell_id(table_id: str, row: int, col: int) -> str:
    return f"{table_id}:r{row}:c{col}"


def _header_label(col: dict) -> str:
    parts = [p for p in (col.get("header_path") or []) if p and not _UNIT_HEAD_RE.match(p)]
    return "/".join(dict.fromkeys(parts))


def _period_tag(col: dict) -> str:
    """머리글 줄에 열 기간 코드를 붙인다(「c1=당기말 [current_end]」). 모르면 붙이지 않는다."""
    p = col.get("period")
    return f" [{p}]" if p and p != "unknown" else ""


def _unit_label(unit: dict) -> str:
    return (unit or {}).get("raw") or (unit or {}).get("kind") or ""


def _footnotes(t: dict) -> list[tuple[str, str, str]]:
    """[(id, mark, text)] — 같은 표에서 같은 각주 글은 처음 하나만."""
    out, seen = [], set()
    for k, fn in enumerate(t.get("footnotes") or [], start=1):
        text = (fn.get("text") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append((f"f:{t['table_id']}:{k}", fn.get("mark") or "", text))
    return out


def _text_amount(text: str) -> tuple[int, int] | None:
    """「35,000,000,000원」 → (35000000000, 1). 통화 낱말이 없거나 금액이 아니면 None."""
    m = _TEXT_AMOUNT_RE.match(text or "")
    if not m:
        return None
    value = int(m.group(3).replace(",", ""))
    if m.group(2) or (m.group(1) and m.group(4)):
        value = -value
    return value, _TEXT_MULT[m.group(5)]


def _is_text_table(t: dict) -> bool:
    return not any(c.get("role") == "value" for c in t.get("columns") or [])


def _render_text_row(t: dict, r: dict, path: list[str], cells: dict, meta: dict) -> str:
    """값 열 없는 글 표의 행: 첫 칸은 행 이름, 그 뒤 칸 중 통화 금액은 `<표 id>:r<행>:v<k>` 칸으로 낸다
    (k = 행 이름 다음부터 1, 2, …; 병합 칸이 줄어 있어 열 번호와 맞지 않으므로 c 대신 v)."""
    parts = [f"r{r['row']} {path[0]}".rstrip()]
    for k, text in enumerate(path[1:], start=1):
        amt = _text_amount(text)
        if amt is None:
            parts.append(text)
            continue
        vid = f"{t['table_id']}:r{r['row']}:v{k}"
        parts.append(f"v{k}={text.strip()}")
        cells[vid] = {"raw": text.strip(), "value": amt[0], "unit_kind": "krw", "multiplier": amt[1],
                      "period": None, "label": path[0], "label_path": [path[0]], **meta}
    return " | ".join(parts)


def _render_table(t: dict, cells: dict) -> tuple[list[str], list[tuple[str, str, str]]]:
    """(본문 줄, [(각주 id, 표시 줄, 각주 글)]) — 각주 줄은 build_input 이 상한 안에 든 것만 붙인다."""
    tid = t["table_id"]
    region = t.get("region") or {}
    unit = t.get("unit") or {}
    head = [f"[{tid}]", f"({t.get('fs_scope') or '?'})"]
    if region.get("note_title"):
        head.append(f"주석: {region['note_title']}")
    if t.get("title"):
        head.append(f"제목: {t['title']}")
    if _unit_label(unit):
        head.append(f"단위: {_unit_label(unit)}")
    lines = [" / ".join([" ".join(head[:2]), *head[2:]])]
    cols = {c["col"]: c for c in t.get("columns") or []}
    value_cols = [c for c in t.get("columns") or [] if c.get("role") == "value"]
    if value_cols:
        lines.append("머리글 | " + " | ".join(f"c{c['col']}={_header_label(c)}{_period_tag(c)}" for c in value_cols))
    text_table = _is_text_table(t)
    if text_table and len(t.get("columns") or []) > 1:
        lines.append("열 | " + " | ".join(_header_label(c) for c in t.get("columns") or []))
    # 검증기용 표 모양: 값 열 수(기간 모르는 칸은 값 열이 하나인 표에서만 받는다), 영역 종류(현금흐름표 줄 제외).
    # 글 표는 금액이 놓인 자리(v 번호) 수가 값 열 수다.
    n_value_cols = len(value_cols) if value_cols else len({
        k for r in t.get("rows") or [] if not r.get("cells")
        for k, p in enumerate((r.get("label_path") or [])[1:], start=1) if _text_amount(p)})
    meta = {"table_title": t.get("title"), "note_title": region.get("note_title"), "n_value_cols": n_value_cols,
            "region_kind": region.get("kind")}
    for r in t.get("rows") or []:
        path = r.get("label_path") or [r.get("label") or ""]
        if text_table and not r.get("cells") and len(path) > 1:
            lines.append(_render_text_row(t, r, path, cells, meta))
            continue
        label = " > ".join(path) if len(path) > 1 else (r.get("label") or path[-1])
        parts = [f"r{r['row']} {label}".rstrip()]
        for c in r.get("cells") or []:
            raw = (c.get("raw") or "").strip()
            if not raw:
                continue
            parts.append(f"c{c['col']}={raw}")
            u = c.get("unit_override") or unit
            col = cols.get(c["col"]) or {}
            cells[_cell_id(tid, r["row"], c["col"])] = {
                "raw": raw, "value": c.get("value"), "unit_kind": u.get("kind"),
                "multiplier": u.get("multiplier"), "period": col.get("period"),
                "label": r.get("label"), "label_path": list(path), "col_header": _header_label(col),
                **meta,
            }
        lines.append(" | ".join(parts))
    notes = [(fid, f"{fid} {mark} {text}".replace("  ", " "), text) for fid, mark, text in _footnotes(t)]
    return lines, notes


def _has_cells(t: dict) -> bool:
    if any((c.get("raw") or "").strip() for r in t.get("rows") or [] for c in r.get("cells") or []):
        return True
    return _is_text_table(t) and any(_text_amount(p) for r in t.get("rows") or [] for p in (r.get("label_path") or [])[1:])


def _select_tables(tables: list[dict]) -> list[dict]:
    """대상 표를 단계 순(같은 단계는 문서 순)으로 MAX_TABLES 개까지. 값 칸이 하나도 없는 표는 맨 뒤로 민다."""
    ranked = [(tier if _has_cells(t) else tier + 10, i, t)
              for i, t in enumerate(tables) if (tier := _table_tier(t)) is not None]
    ranked.sort(key=lambda x: (x[0], x[1]))
    keep = sorted(ranked[:MAX_TABLES], key=lambda x: x[1])
    return [t for _tier, _i, t in keep]


def _sentence_tier(text: str, heading: str) -> int | None:
    if _CONVERTIBLE_RE.search(text) or _CONVERTIBLE_RE.search(heading):
        return 0
    if _BOND_RE.search(heading) or _ROW_RE.search(text) or _BOND_RE.search(text):
        return 1
    if _TARGET_RE.search(heading) or _TARGET_RE.search(text):
        return 2
    return None


def _select_sentences(paragraphs: list[dict], budget: int) -> list[tuple[str, str]]:
    heading = ""
    ranked = []
    for i, p in enumerate(paragraphs):
        text = (p.get("text") or "").strip()
        m = _HEADING_RE.match(text)
        if m and not text.endswith("다."):
            heading = m.group(1)
        if not text:
            continue
        tier = _sentence_tier(text, heading)
        if tier is not None:
            ranked.append((tier, i, p["id"], text))
    ranked.sort(key=lambda x: (x[0], x[1]))
    keep = sorted(ranked[:max(budget, 0)], key=lambda x: x[1])
    return [(sid, text) for _t, _i, sid, text in keep]


_SCOPE_SUFFIX = {"00760": "separate", "00761": "consolidated"}
_FILE_CODE_RE = re.compile(r"_(\d{5})\.xml$", re.I)


def input_scopes(tables: list[dict]) -> list[str]:
    """입력에 넣을 표가 있는 fs_scope 목록(정렬). 비면 입력 묶음에 표가 0개다."""
    return sorted({t.get("fs_scope") or "" for t in tables if _table_tier(t) is not None})


def _paragraph_scope(p: dict, file_scope: dict[str, str]) -> str | None:
    name = p.get("file") or ""
    if name in file_scope:
        return file_scope[name]
    m = _FILE_CODE_RE.search(name)
    return _SCOPE_SUFFIX.get(m.group(1)) if m else None


def build_input(rcept_no: str, tables: list[dict], paragraphs: list[dict], fs_scope: str | None = None) -> dict:
    """LLM 입력 묶음. cells 의 value 는 원래 단위 값이다(원 환산 아님).

    fs_scope 를 주면 그 범위(별도·연결) 표만 넣고, 문장도 그 범위 파일(범위를 모르는 파일은 포함)만 넣는다.
    각주는 문장 상한(MAX_SENTENCES) 안에서 먼저 채우고, 상한 밖 각주는 tables_text 에도 쓰지 않는다.
    """
    if fs_scope is not None:
        file_scope = {t["file"]: t.get("fs_scope") for t in tables if t.get("file")}
        tables = [t for t in tables if (t.get("fs_scope") or "") == fs_scope]
        paragraphs = [p for p in paragraphs if _paragraph_scope(p, file_scope) in (None, fs_scope)]
    cells: dict[str, dict] = {}
    rendered = [_render_table(t, cells) for t in _select_tables(tables)]
    kept = set(list(dict.fromkeys(fid for _lines, notes in rendered for fid, _l, _t in notes))[:MAX_SENTENCES])
    blocks: list[str] = []
    footnotes: list[tuple[str, str]] = []
    for lines, notes in rendered:
        lines = list(lines)
        for fid, line, text in notes:
            if fid in kept:
                lines.append(line)
                footnotes.append((fid, text))
        blocks.append("\n".join(lines))
    sentences = dict(_select_sentences(paragraphs, MAX_SENTENCES - len(footnotes)))
    sentences.update(footnotes)
    out = {"rcept_no": rcept_no, "cells": cells, "tables_text": "\n\n".join(blocks), "sentences": sentences}
    if fs_scope is not None:
        out["fs_scope"] = fs_scope
    return out
