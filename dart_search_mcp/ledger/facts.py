"""2층 ① 항목 금액 사실 — 1층 표 기록 + 개념 사전(concepts.json) → 사실(스펙 §5.2), 미매칭 행 보고, 주제별 매칭률.

매칭(브리프 T6):
  - 행 이름: 앞 번호(「(1)」「Ⅰ.」「①」「가.」)·뒤 표지(「(*1)」「(주석4,14)」)를 떼고 공백을 지운 글.
    ROWSPAN 부모 글은 label_path 에만 있으므로 label_patterns 는 행 이름, label_path 각 요소, 이어 붙인 경로에 맞춘다.
    행 이름 자체에 걸린 개념이 부모 경로로만 걸린 개념보다 앞선다.
  - context_patterns: 행 이름·경로, 표 제목, 상위 행(더 얕은 단계의 앞 행들), 행 표지로 이어진 각주 중 하나에 있어야 한다.
  - regions: statement_bs 등 구간 종류, note:<정규식> 은 주석 제목 또는 표 제목(소제목 문장)에 맞춘다.
    주석 제목을 못 읽은 표(1층 note_title 없음)는 표 제목이 안 맞아도 받고 사실에 region_unknown 을 단다.
  - exclude_patterns: 행 이름·경로에 없어야 한다.
  - column_patterns 가 있으면 「행 × 열」 개념: 열 header_path 마지막 요소가 맞는 칸만.
  - 같은 칸이 개념 둘 이상에 걸리면 사전 순서상 먼저인 하나만 쓰고 stats 에 센다.
기간: balance 는 *_end 열. 표에 *_end 열이 없으면 current_period/prior_period 열을 그 기말로 보고 period_inferred.
  flow 는 *_period 열(「행 × 열」 개념은 그 열의 기간). opening 열은 쓰지 않는다. 기간을 모르는 열은 period "unknown"
  (balance 는 흐름 머리글 열이면 버린다).
단위: shares·percent·count 칸은 금액 개념에 쓰지 않는다. krw 만 amount_krw 가 있다.
같은 기간 값 열이 여럿이면(공정가치 서열 표의 수준 1·2·3·합계) 합계·장부금액 열이 있으면 그 열만 쓴다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

CONCEPTS_PATH = Path(__file__).with_name("concepts.json")

_LEAD_RE = re.compile(r"^(?:\s|[-–—·•※*▶▷○●□■◇◆]|\(\d{1,2}\)|\d{1,2}[.)]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?|[IVX]+\.|"
                      r"[A-Za-z][.)]\s|[①-⑳]|[가-하][.)]|\([가-하]\)|(?:차감|가산|공제|차가감)\s*[:：])+")
_TAIL_RE = re.compile(r"(?:\s|,|\(\*\d*\)|\*\d*\)?|\(주(?:석)?[\d,\s~-]*\)|주\d+\)|[:：])+$")
_MARK_RE = re.compile(r"(\(\*\d*\)|\*\d*\)?|주\d+\))\s*$")
_WS_RE = re.compile(r"\s+")
_AMOUNT_UNITS = {"krw", "foreign", "unknown"}
_PREFERRED_COL_RE = re.compile(r"^(?:합계|총계|계|(?:기말)?순?장부(?:금|가)액|기말(?:잔액|금액)?|당기말(?:잔액)?)$")
_FLOW_HEADER_RE = re.compile(r"취득|증가|감소|상각|처분|대체|손상|환입|발생|상환|전환|행사|기초|발행|최초|인식시")
# 금액이 아닌 숫자 열(공정가치 서열 「수준」, 주당 가격, 비율·이자율, 만기 연도) — 단위가 원으로 잡혀도 금액 개념에 안 쓴다
_NON_AMOUNT_HEADER_RE = re.compile(r"^(수준|Level|서열)$|주당|가격|단가|전환가액|행사가액|비율|이자율|할인율|만기일|^만기$|연도|^기간$|잔존기간|상환기간|%")
_TARGET_RULES = (("conversion", re.compile(r"전환|Convertible|CB|CPS|BW")), ("redemption", re.compile(r"상환|매도청구|콜옵션")))
_HANGUL_RE = re.compile(r"[가-힣]")
END = {"current_end", "prior_end"}
PERIOD = {"current_period", "prior_period"}
_TO_END = {"current_period": "current_end", "prior_period": "prior_end"}


@dataclass(frozen=True)
class Concept:
    id: str
    label_patterns: tuple
    context_patterns: tuple
    regions: tuple
    exclude_patterns: tuple
    value_kind: str
    target: str | None
    column_patterns: tuple = ()
    context_exclude_patterns: tuple = ()
    own_label_only: bool = False
    topic: str = ""
    version: str = ""
    _rx: dict = field(default_factory=dict, compare=False, hash=False, repr=False)

    def rx(self, name: str) -> list[re.Pattern]:
        if name not in self._rx:
            self._rx[name] = [re.compile(p) for p in getattr(self, name)]
        return self._rx[name]


def _load_raw(path: Path | None) -> dict:
    return json.loads(Path(path or CONCEPTS_PATH).read_text(encoding="utf-8"))


def load_concepts(path: Path | None = None) -> list[Concept]:
    raw = _load_raw(path)
    version = raw.get("concepts_version", "")
    out = []
    for c in raw["concepts"]:
        out.append(Concept(id=c["id"], label_patterns=tuple(c["label_patterns"]),
                           context_patterns=tuple(c.get("context_patterns") or ()),
                           regions=tuple(c["regions"]), exclude_patterns=tuple(c.get("exclude_patterns") or ()),
                           value_kind=c["value_kind"], target=c.get("target"),
                           column_patterns=tuple(c.get("column_patterns") or ()),
                           context_exclude_patterns=tuple(c.get("context_exclude_patterns") or ()),
                           own_label_only=bool(c.get("own_label_only")), topic=c.get("topic", ""),
                           version=version))
    return out


def load_topics(path: Path | None = None) -> dict:
    return _load_raw(path).get("topics", {})


# ---------- 글 다듬기 ----------
def norm_label(text: str | None) -> str:
    t = (text or "").strip()
    t = _LEAD_RE.sub("", t)
    t = _TAIL_RE.sub("", t)
    return _WS_RE.sub("", t)


def _mark(label: str) -> str | None:
    m = _MARK_RE.search((label or "").strip())
    return m.group(1) if m else None


def _row_texts(row: dict) -> tuple[str, list[str], str]:
    """(행 이름, 경로 요소들(행 이름 제외), 이어 붙인 경로) — 모두 다듬은 글."""
    label = norm_label(row.get("label"))
    path = [norm_label(p) for p in row.get("label_path") or []]
    parents = [p for p in path if p and p != label]
    joined = "".join(p for p in path if p) or label
    return label, parents, joined


# ---------- 구간 ----------
def _region_ok(concept: Concept, table: dict) -> tuple[bool, bool]:
    """(맞음, 주석 제목 모름)."""
    region = table.get("region") or {}
    kind = region.get("kind")
    title = _WS_RE.sub("", table.get("title") or "")
    for spec in concept.regions:
        if not spec.startswith("note:"):
            if spec == kind:
                return True, False
            continue
        if kind != "note":
            continue
        rx = re.compile(spec[5:])
        nt = _WS_RE.sub("", region.get("note_title") or "")
        if (nt and rx.search(nt)) or (title and rx.search(title)):
            return True, False
        if not nt:
            return True, True
    return False, False


def _note_region_ok(concepts: list[Concept], table: dict) -> bool:
    """주석 표가 이 개념들의 note: 구간에 드는지(미매칭·매칭률 측정용). 주석 제목을 모르는 표는 넣지 않는다."""
    if (table.get("region") or {}).get("kind") != "note":
        return False
    for c in concepts:
        note_only = Concept(c.id, c.label_patterns, (), tuple(s for s in c.regions if s.startswith("note:")), (),
                            c.value_kind, None)
        if note_only.regions:
            ok, unknown = _region_ok(note_only, table)
            if ok and not unknown:
                return True
    return False


# ---------- 표 안 ----------
def _is_blank_header(row: dict) -> bool:
    return row.get("kind") == "item" and bool(row.get("label")) and \
        all(c.get("value") is None for c in row.get("cells") or [])


def _parents(rows: list[dict]) -> list[list[str]]:
    """행마다 상위 행 이름들: 앞쪽에서 단계가 더 얕은 행들 + 값이 빈 머리 행(「당기손익-공정가치측정금융부채」
    아래 「전환권」처럼 같은 단계로 적힌 구획 머리). 머리 행은 소계·합계 행에서 끝난다."""
    out = []
    stack: list[tuple[int, str]] = []
    header: str | None = None
    for r in rows:
        lv = r.get("level") or 0
        while stack and stack[-1][0] >= lv:
            stack.pop()
        ps = [norm_label(t) for _, t in stack]
        if header and header not in ps and header != norm_label(r.get("label")):
            ps.append(header)
        out.append(ps)
        stack.append((lv, r.get("label") or ""))
        if r.get("kind") in ("subtotal", "total"):
            header = None
        elif _is_blank_header(r):
            header = norm_label(r.get("label"))
    return out


def _footnote_text(table: dict, row: dict) -> str:
    mark = _mark(row.get("label") or "")
    if not mark:
        return ""
    return " ".join(f.get("text", "") for f in table.get("footnotes") or [] if f.get("mark") == mark)


def _label_level(concept: Concept, label: str, parents: list[str], joined: str) -> int | None:
    """0 = 행 이름에 걸림, 1 = 경로(부모·이어 붙인 경로)로만 걸림, None = 안 걸림."""
    rxs = concept.rx("label_patterns")
    if any(rx.search(label) for rx in rxs):
        return 0
    if concept.own_label_only:  # 행 이름이 글이 아닌 칸(날짜·기간)일 때만 경로 첫 요소들을 행 이름으로 본다
        if _HANGUL_RE.search(label):
            return None
        return 0 if any(rx.search(p) for rx in rxs for p in parents) else None
    if any(rx.search(p) for rx in rxs for p in parents) or (parents and any(rx.search(joined) for rx in rxs)):
        return 1
    return None


def _target(concept: Concept, own: str, context: list[str]) -> str | None:
    if concept.target != "by_label":
        return concept.target
    for text in [own, *context]:
        for name, rx in _TARGET_RULES:
            if text and rx.search(text):
                return name
    return "other"


def _cell_unit(table: dict, cell: dict) -> dict:
    return cell.get("unit_override") or table.get("unit") or {}


def _is_amount_row(table: dict, row: dict) -> bool:
    if row.get("kind") != "item":
        return False
    return any(c.get("value") is not None and _cell_unit(table, c).get("kind") in _AMOUNT_UNITS
               for c in row.get("cells") or [])


def _header_last(col: dict) -> str:
    hp = col.get("header_path") or []
    return _WS_RE.sub("", hp[-1]) if hp else ""


def _cell_period(concept: Concept, col: dict, has_end: bool, has_period: bool,
                 row_by_col: bool) -> tuple[str | None, bool]:
    """(사실 기간, 추정 여부). None 이면 이 칸을 쓰지 않는다."""
    p = col.get("period") or "unknown"
    if p == "opening":
        return None, False
    if row_by_col:  # 열이 무엇인지 이미 머리글로 정했다 — 기간은 그 열의 것
        if concept.value_kind == "balance" and p in PERIOD and not has_end:
            return _TO_END[p], True
        return (p if p in END | PERIOD | {"unknown"} else None), False
    if concept.value_kind == "balance":
        if p in END:
            return p, False
        if p in PERIOD:
            return (_TO_END[p], True) if not has_end else (None, False)
        if p == "unknown" and not _FLOW_HEADER_RE.search(_header_last(col)):
            return "unknown", False
        return None, False
    if p in PERIOD:
        return p, False
    if p == "unknown":
        return "unknown", False
    return None, False


def _candidates(table: dict, concepts: list[Concept]):
    """칸마다 걸린 개념 후보. → {(row_index, col): [(level, order, concept, period, inferred, row)]}"""
    rows = table.get("rows") or []
    cols = {c["col"]: c for c in table.get("columns") or [] if c.get("role") == "value"}
    # 기말 열이 있는지는 금액 열만 본다(「이자율(%)」 열이 당기말이어도 금액 열은 당기 유동·비유동일 수 있다)
    amount_cols = {cell["col"] for row in rows for cell in row.get("cells") or []
                   if cell.get("value") is not None and _cell_unit(table, cell).get("kind") in _AMOUNT_UNITS}
    periods = {c.get("period") for k, c in cols.items() if k in amount_cols}
    has_end, has_period = bool(periods & END), bool(periods & PERIOD)
    parents = _parents(rows)
    title = _WS_RE.sub("", table.get("title") or "")
    regions = [(_region_ok(c, table)) for c in concepts]
    out: dict = {}
    for i, row in enumerate(rows):
        label, path_parents, joined = _row_texts(row)
        if not label and not joined:
            continue
        foot = _WS_RE.sub("", _footnote_text(table, row))
        context = [*path_parents, title, *parents[i], foot]
        for order, c in enumerate(concepts):
            ok, _unknown = regions[order]
            if not ok:
                continue
            level = _label_level(c, label, path_parents, joined)
            if level is None:
                continue
            if any(rx.search(label) or rx.search(joined) for rx in c.rx("exclude_patterns")):
                continue
            if c.context_patterns and not any(rx.search(t) for rx in c.rx("context_patterns")
                                              for t in [label, *context] if t):
                continue
            if c.context_exclude_patterns and any(rx.search(t) for rx in c.rx("context_exclude_patterns")
                                                  for t in [*path_parents, *parents[i]] if t):
                continue
            col_rx = c.rx("column_patterns")
            picked = []
            for cell in row.get("cells") or []:
                col = cols.get(cell["col"])
                if col is None or cell.get("value") is None:
                    continue
                if _cell_unit(table, cell).get("kind") not in _AMOUNT_UNITS:
                    continue
                if _NON_AMOUNT_HEADER_RE.search(_header_last(col)):
                    continue
                if col_rx and not any(rx.search(_header_last(col)) for rx in col_rx):
                    continue
                period, inferred = _cell_period(c, col, has_end, has_period, bool(col_rx))
                if period is None:
                    continue
                picked.append((cell, col, period, inferred))
            if not col_rx:
                picked = _prefer_total_columns(picked)
            for cell, col, period, inferred in picked:
                out.setdefault((i, cell["col"]), []).append((level, order, c, period, inferred, row, cell, col))
    return out


def _prefer_total_columns(picked: list) -> list:
    """같은 기간 칸이 여럿이면 합계·장부금액 열만(있을 때)."""
    by_period: dict = {}
    for p in picked:
        by_period.setdefault(p[2], []).append(p)
    out = []
    for group in by_period.values():
        if len(group) > 1:
            pref = [p for p in group if _PREFERRED_COL_RE.match(_header_last(p[1]))]
            group = pref or group
        out.extend(group)
    return out


def extract_facts(tables: list[dict], concepts: list[Concept], *, stats: Counter | None = None) -> list[dict]:
    facts: list[dict] = []
    for table in tables:
        if table.get("status") == "read_failed":
            if stats is not None:
                stats["tables_read_failed_skipped"] += 1
            continue
        cands = _candidates(table, concepts)
        rows = table.get("rows") or []
        parents = _parents(rows)
        title = _WS_RE.sub("", table.get("title") or "")
        conflict_rows: set[int] = set()
        for (i, _col), cs in sorted(cands.items()):
            cs.sort(key=lambda x: (x[0], x[1]))
            level, _order, c, period, inferred, row, cell, col = cs[0]
            rivals = sorted({x[2].id for x in cs[1:] if x[0] == level and x[2].id != c.id})
            if rivals and stats is not None:
                if i not in conflict_rows:
                    stats["conflict_rows"] += 1
                    for r in rivals:
                        stats[f"conflict:{c.id}>{r}"] += 1
                conflict_rows.add(i)
            label, path_parents, _joined = _row_texts(row)
            foot = _WS_RE.sub("", _footnote_text(table, row))
            unit = _cell_unit(table, cell)
            fact = {
                "rcept_no": table.get("rcept_no"), "amends_rcept_no": table.get("amends_rcept_no"),
                "fs_scope": table.get("fs_scope"), "concept": c.id, "period": period,
                "amount": cell["value"], "unit_kind": unit.get("kind"),
                "amount_krw": cell.get("value_krw") if unit.get("kind") == "krw" else None,
                "label": row.get("label"), "label_path": row.get("label_path") or [],
                "column": (col.get("header_path") or [""])[-1],
                "target": _target(c, "".join([*path_parents, label]), [title, *parents[i], foot]),
                "source": {"table_id": table.get("table_id"), "page": table.get("page"), "row": row.get("row"),
                           "col": cell["col"]},
                "source_status": table.get("status"), "concepts_version": c.version,
            }
            if unit.get("kind") == "foreign":
                fact["currency"] = unit.get("currency")
            if inferred:
                fact["period_inferred"] = True
            if _region_ok(c, table)[1]:
                fact["region_unknown"] = True
            facts.append(fact)
            if stats is not None:
                stats[f"concept:{c.id}"] += 1
    return facts


def _row_matches(table: dict, concepts: list[Concept]) -> dict[int, set[str]]:
    """행 번호(목록 위치) → 걸린 개념 id들(칸 하나라도)."""
    out: dict[int, set[str]] = {}
    for (i, _col), cs in _candidates(table, concepts).items():
        out.setdefault(i, set()).update(x[2].id for x in cs)
    return out


def _row_key(row: dict) -> str:
    path = [norm_label(p) for p in row.get("label_path") or [] if p]
    label = norm_label(row.get("label"))
    if len(path) > 1:
        return "/".join(path)
    return label or "/".join(path)


def unmatched_rows(tables: list[dict], concepts: list[Concept]) -> Counter:
    """관련 주석(개념 note: 구간) 안의 금액 item 행 중 어느 개념에도 안 걸린 행 이름 빈도."""
    out: Counter = Counter()
    for table in tables:
        if table.get("status") == "read_failed" or not _note_region_ok(concepts, table):
            continue
        matched = _row_matches(table, concepts)
        for i, row in enumerate(table.get("rows") or []):
            if _is_amount_row(table, row) and i not in matched:
                out[_row_key(row)] += 1
    return out


def _topic_row(spec: dict, table: dict, row: dict, context: list[str]) -> bool:
    """정의 B: 이 행이 주제 낱말 행인지(concepts.json topics). keywords 가 행 이름·경로에 있고 kw_exclude 가 없으며,
    require_context 가 있으면 행·경로·표 제목·상위 행·각주 중 하나에 있어야 한다.
    context_exclude 가 주석 제목·표 제목·상위 행에 있으면 뺀다(투자자 쪽 표: 「당기손익-공정가치측정금융자산」).
    column_rows 가 있으면 「행 이름이 row 에 맞고 값 열 머리글 하나가 column 에 맞는」 행도 넣는다(투자부동산 표의 토지·건물)."""
    label, parents, joined = _row_texts(row)
    text = label + " " + joined
    hit = bool(spec.get("keywords") and re.search(spec["keywords"], text))
    cr = spec.get("column_rows")
    if not hit and cr and re.search(cr["row"], label):
        hit = any(re.search(cr["column"], _header_last(c)) for c in table.get("columns") or [] if c.get("role") == "value")
    if not hit:
        return False
    if spec.get("kw_exclude") and re.search(spec["kw_exclude"], text):
        return False
    if spec.get("require_context") and not any(re.search(spec["require_context"], t) for t in [text, *context] if t):
        return False
    note_title = _WS_RE.sub("", (table.get("region") or {}).get("note_title") or "")
    if spec.get("context_exclude") and any(re.search(spec["context_exclude"], t)
                                           for t in [note_title, *context[:-1]] if t):
        return False
    return True


def coverage(tables: list[dict], concepts: list[Concept], topics: dict | None = None,
             unmatched: dict[str, Counter] | None = None) -> dict:
    """주제별 매칭률 재료(주석 제목을 읽은 주석 표만, read_failed 제외).

    rows/matched: 주제 개념들의 note: 구간에 드는 주석 표의 금액 item 행 / 그중 주제 개념에 걸린 행(정의 A).
    kw_rows/kw_matched: 그중 주제 낱말 행(_topic_row) / 걸린 행(정의 B).
    unmatched 를 주면 주제별 정의 B 미매칭 행 이름을 센다."""
    topics = topics if topics is not None else load_topics()
    by_topic: dict[str, list[Concept]] = {}
    for c in concepts:
        by_topic.setdefault(c.topic, []).append(c)
    ids = {c.id: c.topic for c in concepts}
    out = {t: {"rows": 0, "matched": 0, "kw_rows": 0, "kw_matched": 0} for t in by_topic}
    for table in tables:
        if table.get("status") == "read_failed" or (table.get("region") or {}).get("kind") != "note":
            continue
        rel = {t: _note_region_ok(cs, table) for t, cs in by_topic.items()}
        if not any(rel.values()):
            continue
        matched = _row_matches(table, concepts)
        rows = table.get("rows") or []
        parents = _parents(rows)
        title = _WS_RE.sub("", table.get("title") or "")
        for i, row in enumerate(rows):
            if not _is_amount_row(table, row):
                continue
            hit_topics = {ids[x] for x in matched.get(i, ())}
            context = [title, *parents[i], _WS_RE.sub("", _footnote_text(table, row))]
            for t, ok in rel.items():
                if not ok:
                    continue
                m = t in hit_topics
                s = out[t]
                s["rows"] += 1
                s["matched"] += m
                if _topic_row(topics.get(t) or {}, table, row, context):
                    s["kw_rows"] += 1
                    s["kw_matched"] += m
                    if not m and unmatched is not None:
                        unmatched.setdefault(t, Counter())[_row_key(row)] += 1
    return out
