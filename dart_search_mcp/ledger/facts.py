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
mode "table_total"(c6, T6b): 행 이름이 아니라 표 제목(+ 바로 아래 소제목 행)으로 개념을 정하고, 그 표의 합계 행
  하나(합계 행이 없으면 금액 행이 하나뿐인 표의 그 행)를 쓴다. 더하지 않는다. 열은 잔액 열(장부금액·공정가치·기간
  낱말)만, 취득원가·평가손익·지분율 등은 안 쓴다. 같은 표에 행 이름으로 이미 그 개념이 걸렸으면 쓰지 않는다.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from .periods import _kind as _period_word

CONCEPTS_PATH = Path(__file__).with_name("concepts.json")

_LEAD_RE = re.compile(r"^(?:\s|[-–—·•※*▶▷○●□■◇◆]|\(\d{1,2}\)|\d{1,2}[.)]|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?|[IVX]+\.|"
                      r"[A-Za-z][.)]\s|[①-⑳]|[가-하][.)]|\([가-하]\)|(?:차감|가산|공제|차가감)\s*[:：])+")
_TAIL_RE = re.compile(r"(?:\s|,|\(\*\d*\)|\*\d*\)?|\(주(?:석)?[\d,\s~-]*\)|주\d+\)|[:：])+$")
_MARK_RE = re.compile(r"(\(\*\d*\)|\*\d*\)?|주\d+\))\s*$")
_WS_RE = re.compile(r"\s+")
_AMOUNT_UNITS = {"krw", "foreign", "unknown"}
_PREFERRED_COL_RE = re.compile(r"^(?:합계|총계|계|(?:기말)?순?장부(?:금|가)액|기말(?:잔액|금액)?|당기말(?:잔액)?)$")
_FLOW_HEADER_RE = re.compile(r"취득|증가|감소|증감|변동|상각|처분|대체|손상|환입|발생|상환|전환|행사|기초|발행|최초|인식시|창출|자본화")
# 금액이 아닌 숫자 열(공정가치 서열 「수준」, 주당 가격, 비율·이자율, 만기 연도) — 단위가 원으로 잡혀도 금액 개념에 안 쓴다
_NON_AMOUNT_HEADER_RE = re.compile(r"^(수준|Level|서열)$|주당|가격|단가|전환가액|행사가액|비율|이자율|할인율|만기일|^만기$|연도|^기간$|잔존기간|상환기간|주식수|%")
# 공정가치 서열 「수준」 열은 머리글 어느 단계에 있어도 금액 열이 아니다(1층이 [수준, 구획 이름] 순서로 읽은 표, T11b)
_LEVEL_HEADER_RE = re.compile(r"^(수준|Level|서열)$")
# 「수준」 아래 하위 머리글이 수준 번호·합계면 「수준」은 묶음 머리글이다(수준1·2·3·합계 열은 금액)
_LEVEL_CHILD_RE = re.compile(r"^(수준|Level|level)\d|합계|총계|^계$")
# target(내재파생 분리 대상): 구체적인 옵션 낱말이 상품 이름 속 「전환」「상환」보다 앞선다.
_T_CONV_OPTION = re.compile(r"전환권|신주인수권|교환권")
_T_REDEMPTION_OPTION = re.compile(r"조기상환청구권|상환청구권|조기상환권|상환권|매도청구권|콜옵션|풋옵션|수의상환|[Cc]all|[Pp]ut")
_T_NON_CONVERTIBLE = re.compile(r"신종자본증권|영구채|일반사채")
_T_CONV_GENERIC = re.compile(r"전환|Convertible|CB|CPS|BW")
_T_REDEMPTION_GENERIC = re.compile(r"상환")
# 잔액 개념에 쓰지 않는 행(최초 인식·발행 시점 금액)
_BALANCE_ROW_EXCLUDE = re.compile(r"최초|(?<!할인)발행|인식시")
# 표 제목으로 구간을 맞추지 않는 주석(제목에 「전환사채」가 나와도 사채 금액 표가 아니다)
_TITLE_REGION_BLOCK = re.compile(r"법인세|이연|주식기준보상|주식보상|금융수익|금융비용|금융원가|사용권자산|현금흐름|주당|위험|유동성|부문")
# 어느 개념도 맞추지 않는 주석: 항목 이름을 다른 뜻으로 되풀이한다(일시적차이·현금흐름·주당이익)
_NOTE_BLOCK = re.compile(r"법인세|이연|현금흐름|주당")
_PATH_PERIOD = {"current_end": "current_end", "prior_end": "prior_end", "current_term": "current_period",
                "prior_term": "prior_period"}
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
    title_region: bool = False
    table_context: bool = False  # 참이면 context_patterns 를 같은 표의 다른 행 이름에도 맞춘다
    mode: str = ""  # "table_total": 표 제목 + 합계 행(행 이름 매칭을 쓰지 않음)
    table_patterns: tuple = ()  # table_total: 표 제목 + 첫 소제목 행 글에 모두 있어야 하는 패턴
    table_exclude_patterns: tuple = ()  # table_total: 표 제목·소제목에 있으면 안 됨
    row_exclude_patterns: tuple = ()  # 표의 어느 행 이름에도 있으면 안 됨(table_total: 합계가 다른 것을 포함, 행 개념: c11)
    column_exclude_patterns: tuple = ()  # 열 머리글 어느 단계에도 있으면 안 됨(table_total, 행 이름 개념 모두)
    section_patterns: tuple = ()  # table_total: 값 빈 구획 머리 행은 모두 이 중 하나에 맞아야 함(지분·중립 머리)
    replaces: tuple = ()  # table_total: 같은 칸에 이 개념만 걸려 있으면 대신한다
    note_title_required: bool = False  # c12: 주석 제목으로만 구간을 맞춘다(주석 제목 모름·표 제목만 맞음은 안 받음)
    table_text_note: bool = False  # c12 table_total: table_patterns 를 주석 제목에도 맞춘다(주석 제목이 table_exclude 에 안 걸릴 때)
    entity_rows: bool = False  # c12 table_total: 금액 행 절반 이상이 투자처 이름(㈜·주식회사·조합·Inc…)인 표만
    yields_to_total: bool = False  # c12 행 개념: 같은 표에서 같은 id 의 table_total 이 걸리면 그 합계 행에 양보한다
    pl_column_patterns: tuple = ()  # c12: 손익·평가 열(어느 단계든) 칸은 이 개념이 아니라 DERIV_PL_COLUMN 대리 사실(orig_concept)
    no_movement_table: bool = False  # c12 수정 1차: 변동표(기초 열·행 + 흐름 열)에서는 잔액을 읽지 않는다
    subtitle_period: bool = False  # c12 수정 1차: 표 제목이 기간 소제목뿐(「〈전기〉」「② 당기」)이면 그 기간을 쓴다
    since: str = ""  # c12 수정 1차: 이 판에서 새로 생긴 개념 항목이면 그 판(사실에 since 로 단다)
    c11_label_patterns: tuple = ()  # c12 수정 1차: c11 때의 행 이름 패턴 — 이것으로 안 걸리면 c12 새 사실
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
                           own_label_only=bool(c.get("own_label_only")), title_region=bool(c.get("title_region")),
                           table_context=bool(c.get("table_context")),
                           mode=c.get("mode", ""), table_patterns=tuple(c.get("table_patterns") or ()),
                           table_exclude_patterns=tuple(c.get("table_exclude_patterns") or ()),
                           row_exclude_patterns=tuple(c.get("row_exclude_patterns") or ()),
                           column_exclude_patterns=tuple(c.get("column_exclude_patterns") or ()),
                           section_patterns=tuple(c.get("section_patterns") or ()),
                           replaces=tuple(c.get("replaces") or ()),
                           note_title_required=bool(c.get("note_title_required")),
                           table_text_note=bool(c.get("table_text_note")),
                           entity_rows=bool(c.get("entity_rows")),
                           yields_to_total=bool(c.get("yields_to_total")),
                           pl_column_patterns=tuple(c.get("pl_column_patterns") or ()),
                           no_movement_table=bool(c.get("no_movement_table")),
                           subtitle_period=bool(c.get("subtitle_period")),
                           since=c.get("since", ""),
                           c11_label_patterns=tuple(c.get("c11_label_patterns") or ()),
                           topic=c.get("topic", ""),
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
@lru_cache(maxsize=None)
def _compiled(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def _region_match(concept: Concept, table: dict) -> tuple[bool, bool, bool]:
    """(맞음, 주석 제목 모름, 표 제목으로만 맞음).

    note:<re> 는 주석 제목에 맞춘다. title_region 개념(사채·전환권·내재파생)만 주석 제목이 안 맞을 때 표 제목에도
    맞춘다(일동 「금융부채」 주석의 전환사채 표). 법인세·주식보상·금융수익비용·사용권자산 등 주석은 표 제목을 안 본다."""
    region = table.get("region") or {}
    kind = region.get("kind")
    title = _WS_RE.sub("", table.get("title") or "")
    nt = _WS_RE.sub("", region.get("note_title") or "")
    if kind == "note" and nt and _NOTE_BLOCK.search(nt):
        return False, False, False
    title_ok = concept.title_region and bool(title) and not (nt and _TITLE_REGION_BLOCK.search(nt))
    title_only = unknown = False
    for spec in concept.regions:
        if not spec.startswith("note:"):
            if spec == kind:
                return True, False, False
            continue
        if kind != "note":
            continue
        rx = _compiled(spec[5:])
        if nt and rx.search(nt):
            return True, False, False
        if title_ok and rx.search(title):
            title_only = True
        elif not nt:
            unknown = True
    if concept.note_title_required:  # c12: 주석 제목이 맞을 때만(위에서 이미 돌려줌)
        return False, False, False
    if title_only:
        return True, False, True
    if unknown:
        return True, True, False
    return False, False, False


def _region_ok(concept: Concept, table: dict) -> tuple[bool, bool]:
    """(맞음, 주석 제목 모름)."""
    return _region_match(concept, table)[:2]


def _note_region_ok(concepts: list[Concept], table: dict) -> bool:
    """주석 표가 이 개념들의 note: 구간에 드는지(미매칭·매칭률 측정용). 주석 제목을 모르는 표는 넣지 않는다."""
    if (table.get("region") or {}).get("kind") != "note":
        return False
    for c in concepts:
        note_only = Concept(c.id, c.label_patterns, (), tuple(s for s in c.regions if s.startswith("note:")), (),
                            c.value_kind, None, title_region=c.title_region)
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
    for k, r in enumerate(rows):
        lv = r.get("level") or 0
        name = norm_label(r.get("label"))
        while stack and stack[-1][0] >= lv:
            stack.pop()
        ps = [norm_label(t) for _, t in stack]
        if header and header not in ps and header != name:
            ps.append(header)
        out.append(ps)
        stack.append((lv, r.get("label") or ""))
        if r.get("kind") in ("subtotal", "total"):
            header = None
        elif _is_blank_header(r) and _starts_section(rows, k):
            header = name
    return out


def _starts_section(rows: list[dict], k: int) -> bool:
    """값 빈 행이 구획 머리인지: 앞 행과 이름이 같으면 아니다(여러 줄로 이어 쓴 칸). 다음 행이 더 깊거나
    이름이 다를 때만 머리다."""
    name = norm_label(rows[k].get("label"))
    if k > 0 and norm_label(rows[k - 1].get("label")) == name:
        return False
    if k + 1 >= len(rows):
        return False
    nxt = rows[k + 1]
    return (nxt.get("level") or 0) > (rows[k].get("level") or 0) or norm_label(nxt.get("label")) != name


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


def _target_of(text: str) -> str | None:
    """글 하나에서 분리 대상. 구체 옵션 낱말(전환권 / 조기상환청구권·매도청구권·콜옵션…) > 비전환 상품 이름
    (신종자본증권) > 상품 이름 속 「전환」·「상환」. 전환권과 상환 옵션이 함께 있으면(「전환권 및 상환권」) conversion."""
    if not text:
        return None
    if _T_CONV_OPTION.search(text):
        return "conversion"
    if _T_REDEMPTION_OPTION.search(text):
        return "redemption"
    if _T_NON_CONVERTIBLE.search(text):
        return "other"
    if _T_CONV_GENERIC.search(text):
        return "conversion"
    if _T_REDEMPTION_GENERIC.search(text):
        return "redemption"
    return None


def _target(concept: Concept, label: str, path_parents: list[str], row_context: list[str],
            title: str) -> tuple[str | None, str | None, str | None]:
    """(target, target_basis, target_hint). target_basis ∈ label|path|context|default. 행 이름 → 경로 부모(가까운 것부터) → 상위 행·각주(context).
    표 제목만 근거면 target 은 비우고 target_hint 에만 적는다(제목은 표 전체 이야기라 행 근거가 아니다)."""
    if concept.target != "by_label":
        return concept.target, ("concept" if concept.target else None), None
    for basis, texts in (("label", [label]), ("path", list(reversed(path_parents))), ("context", row_context)):
        for text in texts:
            t = _target_of(text)
            if t:
                return t, basis, None
    hint = _target_of(title)
    if hint:
        return None, None, hint
    return "other", "default", None  # 행·경로·문맥 어디에도 대상 낱말이 없다(행 이름이 정한 것이 아님)


def _cell_unit(table: dict, cell: dict) -> dict:
    return cell.get("unit_override") or table.get("unit") or {}


def _is_amount_row(table: dict, row: dict) -> bool:
    """금액 item 행: 금액 단위이고 금액 아닌 머리글(주식수·가격·수준…)이 아닌 열에 값이 하나 이상."""
    if row.get("kind") != "item":
        return False
    cols = {c["col"]: c for c in table.get("columns") or [] if c.get("role") == "value"}
    return any(c.get("value") is not None and _cell_unit(table, c).get("kind") in _AMOUNT_UNITS
               and not _non_amount_col(cols.get(c["col"]) or {})
               for c in row.get("cells") or [])


def _header_last(col: dict) -> str:
    hp = col.get("header_path") or []
    return _WS_RE.sub("", hp[-1]) if hp else ""


def _non_amount_col(col: dict) -> bool:
    """금액이 아닌 숫자 열: 마지막 머리글이 비금액 낱말이거나, 머리글 어느 단계든 「수준」(공정가치 서열 수준 번호).
    마지막 머리글이 수준 번호·합계면 「수준」은 그 위 묶음 머리글이라 금액 열이다."""
    last = _header_last(col)
    if _NON_AMOUNT_HEADER_RE.search(last):
        return True
    if _LEVEL_CHILD_RE.search(last):
        return False
    return any(_LEVEL_HEADER_RE.match(_WS_RE.sub("", h or "")) for h in col.get("header_path") or [])


def _row_period(row: dict) -> str | None:
    """행 경로에 기간 낱말(「당기말」「전기」)만 있는 칸이 있으면 그 기간(기간이 행으로 나뉜 표)."""
    for p in row.get("label_path") or []:
        k = _PATH_PERIOD.get(_period_word(p or ""))
        if k:
            return k
    return None


def _cell_period(concept: Concept, col: dict, has_end: bool, has_period: bool,
                 row_by_col: bool, row_period: str | None = None) -> tuple[str | None, bool]:
    """(사실 기간, 추정 여부). None 이면 이 칸을 쓰지 않는다."""
    p = col.get("period") or "unknown"
    if p == "unknown" and row_period:
        p = row_period
    if p == "opening":
        return None, False
    if row_by_col:  # 열이 무엇인지 이미 머리글로 정했다 — 기간은 그 열의 것
        if concept.value_kind == "balance" and p in PERIOD and not has_end:
            return _TO_END[p], True
        return (p if p in END | PERIOD | {"unknown"} else None), False
    if concept.value_kind == "balance":
        if p in END:
            return p, False
        if p in PERIOD:  # 기말 열 없는 표의 당기·전기 열 = 그 기말. 증감·취득 같은 흐름 열은 아니다
            if has_end or _FLOW_HEADER_RE.search(_header_last(col)):
                return None, False
            return _TO_END[p], True
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
    headers = [_WS_RE.sub("", "".join(c.get("header_path") or [])) for c in cols.values()]
    row_names = [norm_label(r.get("label")) for r in rows]
    parents = _parents(rows)
    title = _WS_RE.sub("", table.get("title") or "")
    regions = [(_region_ok(c, table)) for c in concepts]
    # 행 이름 개념의 row_exclude_patterns(c11): 표의 어느 행에든 걸리면 그 표에는 이 개념을 쓰지 않는다
    # (「감가상각누계액」 행이 있는 내역 표의 토지·건물 행은 취득원가다)
    all_names = [n for r in rows for n in (norm_label(r.get("label")), *(norm_label(x) for x in r.get("label_path") or []))
                 if n]
    regions = [(ok and not (c.row_exclude_patterns and not c.mode
                            and any(rx.search(n) for rx in c.rx("row_exclude_patterns") for n in all_names)), unk)
               for c, (ok, unk) in zip(concepts, regions)]
    movement = _is_movement_table(table)
    regions = [(ok and not (c.no_movement_table and movement), unk) for c, (ok, unk) in zip(concepts, regions)]
    # 행 이름 개념의 table_exclude_patterns(c12): 표 제목에 걸리면 그 표에는 이 개념을 쓰지 않는다(변동·손익 표)
    regions = [(ok and not (c.table_exclude_patterns and not c.mode
                            and any(rx.search(title) for rx in c.rx("table_exclude_patterns"))), unk)
               for c, (ok, unk) in zip(concepts, regions)]
    out: dict = {}
    for i, row in enumerate(rows):
        label, path_parents, joined = _row_texts(row)
        if not label and not joined:
            continue
        foot = _WS_RE.sub("", _footnote_text(table, row))
        context = [*path_parents, title, *parents[i], foot]
        row_period = _row_period(row)
        for order, c in enumerate(concepts):
            ok, _unknown = regions[order]
            if not ok or c.mode:
                continue
            level = _label_level(c, label, path_parents, joined)
            if level is None:
                continue
            if level == 1 and row.get("kind") in ("subtotal", "total"):
                continue  # 「전환사채 / 소계」: 부모 이름만으로 소계·합계를 개념에 걸지 않는다
            if c.value_kind == "balance" and _BALANCE_ROW_EXCLUDE.search(label):
                continue  # 「최초 인식시점 장부금액」「발행시」는 잔액이 아니다
            if any(rx.search(label) or rx.search(joined) for rx in c.rx("exclude_patterns")):
                continue
            if c.context_patterns and not any(rx.search(t) for rx in c.rx("context_patterns")
                                              for t in [label, *context, *headers,
                                                        *(row_names if c.table_context else [])] if t):
                continue
            if c.context_exclude_patterns and any(rx.search(t) for rx in c.rx("context_exclude_patterns")
                                                  for t in [*path_parents, *parents[i]] if t):
                continue
            col_rx = c.rx("column_patterns")
            col_ex = c.rx("column_exclude_patterns")
            pl_rx = c.rx("pl_column_patterns")
            picked, pl_picked = [], []
            for cell in row.get("cells") or []:
                col = cols.get(cell["col"])
                if col is None or cell.get("value") is None:
                    continue
                if _cell_unit(table, cell).get("kind") not in _AMOUNT_UNITS:
                    continue
                if _non_amount_col(col):
                    continue
                if col_ex and any(rx.search(_WS_RE.sub("", h or "")) for rx in col_ex for h in col.get("header_path") or []):
                    continue  # 「자산」 열의 내재파생상품 행은 부채 개념이 아니다(T11b 수정 1차)
                if pl_rx and any(rx.search(_WS_RE.sub("", h or "")) for rx in pl_rx for h in col.get("header_path") or []):
                    if not col_rx or any(rx.search(_header_last(col)) for rx in col_rx):
                        period, inferred = _cell_period(c, col, has_end, has_period, bool(col_rx), row_period)
                        if period is not None:
                            pl_picked.append((cell, col, period, inferred))
                    continue  # 손익 열(c12): 판정에 쓰지 않는 대리 사실로만 남긴다
                if col_rx and not any(rx.search(_header_last(col)) for rx in col_rx):
                    continue
                period, inferred = _cell_period(c, col, has_end, has_period, bool(col_rx), row_period)
                if period is None:
                    continue
                picked.append((cell, col, period, inferred))
            if not col_rx:
                picked = _prefer_total_columns(picked)
            for cell, col, period, inferred in picked:
                out.setdefault((i, cell["col"]), []).append((level, order, c, period, inferred, row, cell, col))
            for cell, col, period, inferred in pl_picked:
                out.setdefault((i, cell["col"]), []).append((level, order, _pl_proxy(c), period, inferred, row, cell, col,
                                                             None, {"orig_concept": c.id}))
    for order, c in enumerate(concepts):
        if c.mode == "table_total" and regions[order][0]:
            _table_total(c, order, table, cols, has_end, has_period, out)
    return out


_TOTAL_LABEL_RE = re.compile(r"^(합계|총계|계|총합계)$")
_TERM_KINDS = {"current_end", "prior_end", "current_term", "prior_term", "close"}


@lru_cache(maxsize=None)
def _pl_proxy(c: Concept) -> Concept:
    """손익 열 칸의 대리 개념: 판정 개념 묶음에 없는 id. 원래 개념은 사실의 orig_concept(전환권 값 동결 확인용, r5)."""
    return replace(c, id="DERIV_PL_COLUMN", pl_column_patterns=(), _rx={})


_OPENING_RE = re.compile(r"^(기초|당기초|전기초)(잔액|금액|장부(금|가)액|순장부(금|가)액)?$")
_CLOSING_RE = re.compile(r"^(기말|당기말|전기말)(잔액|금액|장부(금|가)액|순장부(금|가)액)?$")


def _is_movement_table(table: dict) -> bool:
    """변동표(c12 수정 1차): 값 열에 기초 열과 흐름 열(취득·처분·평가·증감…)이 함께 있거나, 행에 기초·기말 행이 함께 있다."""
    cols = [c for c in table.get("columns") or [] if c.get("role") == "value"]
    heads = [_WS_RE.sub("", _header_last(c)) for c in cols]
    opening = any(c.get("period") == "opening" or _OPENING_RE.match(h) for c, h in zip(cols, heads))
    if opening and any(_FLOW_HEADER_RE.search(h) or re.search(r"평가|법인세효과|실현", h) for h in heads
                       if not _OPENING_RE.match(h) and not _CLOSING_RE.match(h)):
        return True
    names = {norm_label(r.get("label")) for r in table.get("rows") or []}
    return any(_OPENING_RE.match(n) for n in names) and any(_CLOSING_RE.match(n) for n in names)


_SUBTITLE_PERIOD_RE = re.compile(r"^[-–·①-⑳\d.()〈〉<>\[\]]*(당기|전기)말?[〉>)\]]*$")


def _subtitle_side(table: dict) -> str | None:
    m = _SUBTITLE_PERIOD_RE.match(_WS_RE.sub("", table.get("title") or ""))
    return ("current" if m.group(1) == "당기" else "prior") if m else None


def _table_total(c: Concept, order: int, table: dict, cols: dict, has_end: bool, has_period: bool, out: dict) -> None:
    """mode table_total: 표 제목(+ 첫 소제목 행)이 개념을 정하는 표의 합계 행(또는 한 행짜리 표의 그 행)."""
    rows = table.get("rows") or []
    title = _WS_RE.sub("", table.get("title") or "")
    sub = norm_label(rows[0].get("label")) if rows and _is_blank_header(rows[0]) else ""
    text = title + " " + sub
    via_note = False
    if c.table_text_note:  # c12: 「(3) 시장성 있는 지분증권의 내역」 표는 주석 제목이 FVOCI 다(성우하이텍 s056)
        nt = _WS_RE.sub("", (table.get("region") or {}).get("note_title") or "")
        via_note = not all(rx.search(text) for rx in c.rx("table_patterns"))
        if nt and not any(rx.search(nt) for rx in c.rx("table_exclude_patterns")):
            text = nt + " " + text
    if not all(rx.search(text) for rx in c.rx("table_patterns")):
        return
    if c.c11_label_patterns and not via_note:  # c11 표 제목 패턴(옛 낱말)으로도 걸리는가
        tt = title + " " + sub
        via_note = not all(re.search(p, tt) for p in c.c11_label_patterns)
    if any(rx.search(t) for rx in c.rx("table_exclude_patterns") for t in (title, sub) if t):
        return
    for r in rows:
        names = [norm_label(r.get("label")), *(norm_label(p) for p in r.get("label_path") or [])]
        if any(rx.search(n) for rx in c.rx("row_exclude_patterns") for n in names if n):
            return
    sec_rx = c.rx("section_patterns")
    if sec_rx and any(_is_blank_header(r) and not any(rx.search(norm_label(r.get("label"))) for rx in sec_rx)
                      for r in rows):
        return  # 뒤쪽 구획(「수익증권」「기타」)이 지분이 아니면 합계가 다른 것을 담는다 — 첫 소제목만 보면 안 된다
    if any(x[2].id == c.id and not x[2].yields_to_total for cs in out.values() for x in cs):
        return  # 행 이름으로 이미 걸렸다(「기타포괄손익-공정가치측정 지분상품」 행 + 합계) — 겹쳐 내지 않는다
    if c.entity_rows:  # c12: 투자처 이름 표(삼라마이다스 s059) — 금액 행 둘 이상, 절반 이상이 회사·조합 이름
        items = [r for r in rows if r.get("kind") == "item" and _is_amount_row(table, r)]
        named = [r for r in items if _ENTITY_RE.search(r.get("label") or "")]
        if len(items) < 2 or 2 * len(named) < len(items):
            return
    totals = [i for i, r in enumerate(rows) if r.get("kind") == "total"]
    if totals:
        if len(totals) != 1 or not _TOTAL_LABEL_RE.match(norm_label(rows[totals[0]].get("label"))):
            return
        idx, basis = totals[0], "table_total"
    else:
        if any(r.get("kind") == "subtotal" for r in rows):
            return
        items = [i for i, r in enumerate(rows) if _is_amount_row(table, r)]
        if len(items) != 1:
            return  # 합계 없는 여러 행 표는 더하지 않는다
        idx, basis = items[0], "table_single_row"
    row = rows[idx]
    row_period = _row_period(row)
    extra = _total_parts(table, idx) if basis == "table_total" else {}
    if via_note:
        extra = {**extra, "since": "c12"}
    picked = []
    for cell in row.get("cells") or []:
        col = cols.get(cell["col"])
        if col is None or cell.get("value") is None or _cell_unit(table, cell).get("kind") not in _AMOUNT_UNITS:
            continue
        last = _header_last(col)
        if _non_amount_col(col):
            continue
        parts = [_WS_RE.sub("", h or "") for h in col.get("header_path") or []]
        if any(rx.search(h) for rx in c.rx("column_exclude_patterns") for h in parts if h):
            continue
        if not (any(rx.search(last) for rx in c.rx("column_patterns")) or _period_word(last) in _TERM_KINDS):
            continue
        period, inferred = _cell_period(c, col, has_end, has_period, False, row_period)
        if period is None:
            continue
        picked.append((cell, col, period, inferred))
    if any(p[2] != "unknown" for p in picked):
        picked = [p for p in picked if p[2] != "unknown"]  # 기간 열이 있으면 기간 모르는 공정가치 열은 안 쓴다
    by_period: dict = {}
    for p in picked:
        by_period.setdefault(p[2], []).append(p)
    if picked:  # c12: 양보하는 행 개념(FVOCI 주석의 지분 행)은 합계 행이 걸린 표에서 빠진다(합계 + 구성 행 이름이 근거)
        for key in list(out):
            out[key] = [x for x in out[key] if not (x[2].yields_to_total and x[2].id == c.id)]
            if not out[key]:
                del out[key]
    for group in by_period.values():
        if len(group) > 1:
            group = ([p for p in group if re.search(r"장부", _header_last(p[1]))]
                     or [p for p in group if re.search(r"공정가치", _header_last(p[1]))] or group)
        for cell, col, period, inferred in group:
            key = (idx, cell["col"])
            if any(x[2].id not in c.replaces for x in out.get(key, [])):
                continue  # 행 이름으로 걸린 다른 개념이 앞선다
            out[key] = [(0, order, c, period, inferred, row, cell, col, basis, extra)]


# 투자처 이름 표지(c12): 회사·조합·법인 이름
_ENTITY_RE = re.compile(r"㈜|\(주\)|주식회사|유한(책임)?회사|\(유\)|조합|협회|재단|공사$|Inc\b|INC\b|Ltd|LTD|L\.L\.C|LLC|Corp|CORP"
                        r"|Co\.|CO\.|GmbH|Limited|LIMITED|Pte|PTE|B\.V|S\.A|JSC|\bAG\b|Company|Holdings|Technologies")
# 합계가 더한 행 이름에서 뗄 뒤 표지(「(주1)」「(*1)」). 회사 이름의 「(주)」는 남긴다
_COMP_TAIL_RE = re.compile(r"(?:\s|\(\*+\d*\)|\*+\d*\)?|\(주(?:석[\d,\s~-]*|\d+)\)|주\d+\))+$")
_COMPONENTS_MAX = 30


def _clean_component(label: str) -> str:
    return _COMP_TAIL_RE.sub("", _WS_RE.sub(" ", (label or "").strip())).strip()


def _total_parts(table: dict, idx: int) -> dict:
    """합계 행이 더한 것(c12, 회계사 두 명 요청): 같은 표의 앞쪽(앞 합계 행 뒤부터) 금액 item 행 이름. 그 사이에 소계 행이
    있으면 소계가 묶은 구획 이름(값 빈 머리 행)을 대신 쓴다. 구획 이름은 합계 행 경로의 부모, 없으면 구획이 하나일 때 그
    머리 행. 근거 줄이 「합계」만 보이지 않게 한다."""
    rows = table.get("rows") or []
    start = 0
    for k in range(idx - 1, -1, -1):
        if rows[k].get("kind") == "total":
            start = k + 1
            break
    span = rows[start:idx]
    heads = [_clean_component(r.get("label")) for r in span if _is_blank_header(r)]
    if any(r.get("kind") == "subtotal" for r in span) and heads:
        comps, section = heads, None
    else:
        comps = [_clean_component(r.get("label")) for r in span if r.get("kind") == "item" and _is_amount_row(table, r)]
        section = heads[0] if len(heads) == 1 else None
    path = [p for p in (rows[idx].get("label_path") or [])[:-1] if p]
    if path:
        section = _clean_component(path[-1])
    return {"components": comps[:_COMPONENTS_MAX], "components_total": len(comps), "section": section}


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
            level, _order, c, period, inferred, row, cell, col = cs[0][:8]
            basis_kind = cs[0][8] if len(cs[0]) > 8 else None
            extra = cs[0][9] if len(cs[0]) > 9 else None
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
                "target": None, "target_basis": None,
                "source": {"table_id": table.get("table_id"), "page": table.get("page"), "row": row.get("row"),
                           "col": cell["col"]},
                "source_status": table.get("status"), "concepts_version": c.version,
            }
            target, basis, hint = _target(c, label, path_parents, [*reversed(parents[i]), foot], title)
            fact["target"], fact["target_basis"] = target, (basis if basis != "concept" else None)
            if hint:
                fact["target_hint"] = hint
            if unit.get("kind") == "foreign":
                fact["currency"] = unit.get("currency")
            if inferred:
                fact["period_inferred"] = True
            if basis_kind:
                fact["match_basis"] = basis_kind
            if extra:
                fact.update(extra)
            if c.since:
                fact["since"] = c.since
            elif c.c11_label_patterns and not basis_kind and not any(
                    re.search(p, t) for p in c.c11_label_patterns for t in (norm_label(row.get("label")), _joined_path(row))):
                fact["since"] = "c12"  # c12 낱말(「기타포괄손익인식」 등)로만 걸린 행
            side = _subtitle_side(table) if c.subtitle_period else None
            if side and period in END | PERIOD and not period.startswith(side):
                fact["period"] = f"{side}_end" if period in END else f"{side}_period"
                fact["period_from_title"] = True
            _ok, unknown, title_only = _region_match(c, table)
            if unknown:
                fact["region_unknown"] = True
            if title_only:
                fact["region_basis"] = "title"
                if stats is not None:
                    stats["facts_region_title_only"] += 1
            facts.append(fact)
            if stats is not None:
                stats[f"concept:{c.id}"] += 1
    return facts


def _joined_path(row: dict) -> str:
    return _row_texts(row)[2]


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


def _topic_row(spec: dict, table: dict, row: dict, context: list[str]) -> tuple[bool, str | None]:
    """정의 B: (주제 낱말 행인지, 뺀 규칙 이름). concepts.json topics:
    keywords 가 행 이름·경로에 있거나, column_rows(행 이름이 row 에 맞고 값 열 머리글 하나가 column 에 맞음)면 주제 행.
    exclusions 는 이름 붙은 규칙 목록 {name, pattern, on: row|context, why, when_context?} — 처음 걸린 규칙 이름.
    when_context 가 있으면 행 규칙은 주석·표 제목·상위 행에 그 낱말이 있을 때만 뺀다.
    on=context 는 주석 제목·표 제목·상위 행(각주 제외)에 맞춘다."""
    label, _parents_, joined = _row_texts(row)
    text = label + " " + joined
    hit = bool(spec.get("keywords") and re.search(spec["keywords"], text))
    cr = spec.get("column_rows")
    if not hit and cr and re.search(cr["row"], label):
        hit = any(re.search(cr["column"], _header_last(c)) for c in table.get("columns") or [] if c.get("role") == "value")
    if not hit:
        return False, None
    note_title = _WS_RE.sub("", (table.get("region") or {}).get("note_title") or "")
    ctx = [t for t in [note_title, *context[:-1]] if t]
    for rule in spec.get("exclusions") or []:
        rx = _compiled(rule["pattern"])
        if rule.get("on", "row") == "row":
            when = rule.get("when_context")
            if rx.search(text) and (not when or any(_compiled(when).search(t) for t in ctx)):
                return True, rule["name"]
        elif any(rx.search(t) for t in ctx):
            return True, rule["name"]
    return True, None


def coverage(tables: list[dict], concepts: list[Concept], topics: dict | None = None,
             unmatched: dict[str, Counter] | None = None) -> dict:
    """주제별 매칭률 재료(주석 제목을 읽은 주석 표만, read_failed 제외).

    rows/matched: 주제 개념들의 note: 구간에 드는 주석 표의 금액 item 행 / 그중 주제 개념에 걸린 행(정의 A).
    kw_all_rows/kw_all_matched: 그중 주제 낱말 행 전부(제외 규칙 되돌림).
    kw_rows/kw_matched: 그중 제외 규칙에 안 걸린 행(정의 B). excluded_by_rule: 규칙별로 뺀 행 수.
    unmatched 를 주면 주제별 정의 B 미매칭 행 이름을 센다."""
    topics = topics if topics is not None else load_topics()
    by_topic: dict[str, list[Concept]] = {}
    for c in concepts:
        by_topic.setdefault(c.topic, []).append(c)
    ids = {c.id: c.topic for c in concepts}
    out = {t: {"rows": 0, "matched": 0, "kw_all_rows": 0, "kw_all_matched": 0, "kw_rows": 0, "kw_matched": 0,
               "excluded_by_rule": {}} for t in by_topic}
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
            hit_topics = {ids[x] for x in matched.get(i, ()) if x in ids}  # 대리 개념(DERIV_PL_COLUMN)은 주제 없음
            context = [title, *parents[i], _WS_RE.sub("", _footnote_text(table, row))]
            for t, ok in rel.items():
                if not ok:
                    continue
                m = t in hit_topics
                s = out[t]
                s["rows"] += 1
                s["matched"] += m
                is_kw, rule = _topic_row(topics.get(t) or {}, table, row, context)
                if not is_kw:
                    continue
                s["kw_all_rows"] += 1
                s["kw_all_matched"] += m
                if rule:
                    s["excluded_by_rule"][rule] = s["excluded_by_rule"].get(rule, 0) + 1
                    continue
                s["kw_rows"] += 1
                s["kw_matched"] += m
                if not m and unmatched is not None:
                    unmatched.setdefault(t, Counter())[_row_key(row)] += 1
    return out


# ---------- 문장 사실(c11) ----------
# 투자부동산 공정가치를 표가 아니라 문장으로 공시한 회사가 많다(회계사 v2 표본 s115·s116·s119).
# 「(당기말|보고기간말) 현재 [회사의] 투자부동산의 공정가치는 N(단위)[(전기말: M단위)]」 한 문장 → IP_FAIR_VALUE 사실.
# 「투자부동산 토지의」「투자부동산 중 토지의」「공정가치는 토지 N 및 건물 M」처럼 일부만 적은 문장은 쓰지 않는다(더하지 않음).
_SENT_UNITS = {"원": 1, "천원": 1000, "백만원": 1000000, "억원": 100000000}
_AMT = r"\d{1,3}(?:,\d{3})+|\d+"
_UNIT = r"백만원|천원|억원|원"
_IP_SUBJECT = r"[^.\d]{0,20}?투자부동산(?:\s*\(토지\s*및\s*건물\))?(?:의|에\s*대한)?\s*공정가치는\s*"
_IP_FV_ONE = re.compile(
    r"(?P<when>당기말|보고기간\s*말|보고기간\s*종료일|전기말)\s*현재" + _IP_SUBJECT
    + r"(?:총\s*|약\s*)?(?P<amt>" + _AMT + r")\s*(?P<unit>" + _UNIT + r")"
    + r"(?:\s*\(\s*전기말?\s*[:：]?\s*(?P<pamt>" + _AMT + r")\s*(?P<punit>" + _UNIT + r")?\s*\))?")
_IP_FV_EACH = re.compile(
    r"당기말\s*(?:및|과|와)\s*전기말\s*현재" + _IP_SUBJECT
    + r"각각\s*(?P<amt>" + _AMT + r")\s*(?P<unit>" + _UNIT + r")?\s*(?:및|과|와|,)\s*(?P<pamt>" + _AMT + r")\s*(?P<punit>"
    + _UNIT + r")")
_SENT_WHEN = {"전기말": "prior_end"}
# 공정가치 모형 문장(회계사 v2 s120): 이 회사는 장부금액이 곧 공정가치다 → IP_FV_MODEL 표시 사실(금액 없음).
# 「원가모형에서 공정가치모형으로 변경」은 공정가치 모형이다. 원가모형 적용·건설중 예외·신뢰성 없음 문장은 아니다.
_IP_FV_MODEL = re.compile(
    r"투자부동산[^.]{0,60}(공정가치\s*(평가\s*)?모형|후속\s*측정[^.]{0,15}공정가치로"
    r"|최초\s*인식\s*(이\s*)?후[^.]{0,40}공정가치로\s*(측정|계상))"
    r"|최초\s*인식\s*(이\s*)?후[^.]{0,10}투자부동산[^.]{0,40}공정가치로\s*(측정|계상)"
    r"|투자부동산을\s*공정가치로\s*(평가|측정|계상)")
# 수정 1차: 부정문(「공정가치로 측정하지는 않지만」)·다른 회사(피투자회사·종속기업·관계기업)의 모형 문장도 아니다
_IP_FV_MODEL_EXCLUDE = re.compile(r"원가\s*모형(?!에서)|건설\s*중인|신뢰성\s*있게\s*(산정|측정)할\s*수\s*없|하는\s*경우|할\s*수\s*있"
                                  r"|측정하지\s*(는\s*)?(않|아니)|피투자|종속기업|관계기업"
                                  r"|토지에\s*대해\s*(서는\s*)?공정가치")  # 토지만 공정가치(수정 3차)  # 부정은 측정 동사에만(수정 2차)
# 같은 범위에 원가모형 근거(투자부동산 표의 감가상각누계 행, 원가모형 문장)가 있으면 모형 표시를 내지 않는다
_IP_COST_ROW = re.compile(r"상각누계|상가누계")
_IP_COST_SENT = re.compile(r"투자부동산[^.]{0,60}원가\s*모형(?!에서)|원가\s*모형(?!에서)[^.]{0,60}투자부동산")
# 수정 2차: 전환 회사의 과거 원가모형 문장, 공정가치 모형 회사의 「원가모형으로 평가되었을 경우의 장부금액」 공시는 근거가 아니다
_IP_COST_SENT_EXCLUDE = re.compile(r"과거에|경우의|되었을\s*경우")
# 수정 3차: 투자부동산 문장 속 원가 표시·정액법 상각(「원가에서 감가상각누계액…을 차감」「토지를 제외한 … 정액법으로 상각」)
_IP_COST_SENT2 = re.compile(r"원가에서\s*감가상각누계|정액법으로\s*상각")
_IP_DEPR = re.compile(r"감가상각(?!누계)|상각비")
_IP_FV_GAIN = re.compile(r"공정가치\s*(평가|변동)")  # 같은 표에 0 아닌 평가 열·행이 있으면 감가상각은 근거가 아니다(수정 4차)
_IP_COST_TABLE_EXCLUDE = re.compile(r"원가\s*모형|경우")


def _cost_row_table(t: dict) -> bool:
    """투자부동산 주석의 당기말 장부금액 표에 0 아닌 감가상각누계 칸이 있는가(전기·비교 열, 원가모형 가정 표는 빼고)."""
    if "투자부동산" not in ((t.get("region") or {}).get("note_title") or "") or \
            _IP_COST_TABLE_EXCLUDE.search(_WS_RE.sub("", t.get("title") or "")):
        return False
    vals = [c for c in t.get("columns") or [] if c.get("role") == "value"]
    cur = {c.get("col") for c in vals if c.get("period") == "current_end"}
    cur_p = {c.get("col") for c in vals if c.get("period") == "current_period"}
    depr_cols = {c.get("col") for c in vals if c.get("period") == "current_period"
                 and _IP_DEPR.search(_WS_RE.sub("", "".join(c.get("header_path") or [])))}
    gain_cols = {c.get("col") for c in vals if _IP_FV_GAIN.search(_WS_RE.sub("", "".join(c.get("header_path") or [])))}
    fv_gain = any((c.get("col") in gain_cols or any(_IP_FV_GAIN.search(_WS_RE.sub("", x or ""))
                                                     for x in [r.get("label"), *(r.get("label_path") or [])]))
                  and c.get("value") for r in t.get("rows") or [] for c in r.get("cells") or [])
    for r in t.get("rows") or []:
        names = [_WS_RE.sub("", x or "") for x in [r.get("label"), *(r.get("label_path") or [])]]
        cells = r.get("cells") or []
        if any(_IP_COST_ROW.search(n) for n in names):
            if any(c.get("col") in cur and c.get("value") for c in cells):
                return True
        elif fv_gain:
            continue
        elif any(_IP_DEPR.search(n) for n in names):  # 당기 변동표의 감가상각 행(수정 3차)
            if any(c.get("col") in cur_p and c.get("value") for c in cells):
                return True
        if not fv_gain and any(c.get("col") in depr_cols and c.get("value") for c in cells):  # 감가상각 열(행 = 토지·건물)
            return True
    return False
# 「투자부동산의 장부금액은 공정가치와 중요한 차이가 없습니다」(금액 없음) — 회계사 Q4 대기(s118 부류) → 판정은 확인 필요
_IP_FV_NO_DIFF = re.compile(r"투자부동산[^.]{0,40}(장부\s*(금|가)액|공정가치)[^.]{0,30}(중요한|유의적인|유의한|중요하지\s*않은)?\s*차이가?\s*"
                            r"(없|미미|중요하지\s*않)")


def _file_scope(name: str, by_file: dict) -> str | None:
    if name in by_file:
        return by_file[name]
    code = re.search(r"_(\d{5})\.xml$", name or "")
    return {"00760": "separate", "00761": "consolidated"}.get(code.group(1) if code else "")


def _sentence_fact(filing: dict, p: dict, scope: str, period: str, amt: str, unit: str, version: str) -> dict:
    amount = int(amt.replace(",", ""))
    mult = _SENT_UNITS[unit]
    return {"rcept_no": filing.get("rcept_no"), "amends_rcept_no": filing.get("amends_rcept_no"), "fs_scope": scope,
            "concept": "IP_FAIR_VALUE", "period": period, "amount": amount, "unit_kind": "krw",
            "amount_krw": amount * mult, "label": "투자부동산의 공정가치", "label_path": [], "column": "",
            "target": None, "target_basis": None,
            "source": {"sentence_id": p.get("id"), "page": p.get("page"), "file": p.get("file")},
            "source_status": "sentence", "concepts_version": version, "match_basis": "sentence",
            "text": (p.get("text") or "").strip()[:300]}


_MARKER_LABEL = {"IP_FV_MODEL": "투자부동산 공정가치 모형", "IP_FV_NO_MATERIAL_DIFF": "투자부동산 공정가치·장부금액 차이 없음(문장)",
                 "IP_FV_LEADIN": "투자부동산 공정가치 공시 머리 문장",
                 "IP_FV_MODEL_BLOCKED": "투자부동산 공정가치 모형 문장(원가모형 근거와 충돌)",
                 "FVOCI_DESIGNATION_STMT": "기타포괄손익-공정가치 지정 문장"}
# 「투자부동산의 공정가치는 다음과 같습니다」 — 공정가치 표가 뒤따른다(금액을 못 읽어도 공시는 했다)
_IP_FV_LEADIN = re.compile(r"투자부동산[^.]{0,30}공정가치[^.]{0,30}다음과\s*같")


def _model_fact(filing: dict, p: dict, scope: str, version: str, concept: str = "IP_FV_MODEL") -> dict:
    """금액 없는 표시 사실(공정가치 모형 / 차이 없음 문장)."""
    return {"rcept_no": filing.get("rcept_no"), "amends_rcept_no": filing.get("amends_rcept_no"), "fs_scope": scope,
            "concept": concept, "period": "current_end", "amount": None, "unit_kind": None, "amount_krw": None,
            "label": _MARKER_LABEL[concept], "label_path": [], "column": "", "target": None, "target_basis": None,
            "source": {"sentence_id": p.get("id"), "page": p.get("page"), "file": p.get("file")},
            "source_status": "sentence", "concepts_version": version, "match_basis": "sentence",
            "text": (p.get("text") or "").strip()[:300]}


# ---------- FVOCI 지정 문장(c12) ----------
# 회사가 보유 지분상품을 FVOCI 로 지정했다는 직접 문장(회계사 두 명: s055·s056·s058 근거). 일반 정책 「지정할 수 있다」는
# 근거가 아니다(두 명 모두). 보유 내역 주석(회계정책·작성기준 주석이 아닌 주석)의 문장만 쓴다. 문단은 앞쪽 「N. 제목」
# 문단으로 주석을 알고, 값 열 없는 글 표는 그 표의 주석 제목을 쓴다. 주석을 모르면 쓰지 않는다.
_FV_DESIG = re.compile(r"취소\s*(불가능한|불가)\s*선택(권)?을\s*(적용|하였|했|선택)"
                       r"|기타포괄손익[^.]{0,30}?(으로|로)\s*(인식|표시)하도록\s*지정(하였|했)"
                       r"|기타포괄손익[^.]{0,40}?(으로|로)\s*지정(하였|했|하고\s*있)")
_FV_DESIG_NEG = re.compile(r"할\s*수\s*있|지정하지|지정되지|않았|않습니다|피투자|관계기업|종속기업")
_FV_DESIG_EQ = re.compile(r"지분|주식")
_NOTE_HEAD = re.compile(r"^\s*(\d{1,2})\s*\.\s*([^\d\s].{1,})")
_POLICY_NOTE = re.compile(r"회계정책|작성\s*기준|재무제표의?\s*작성|회계처리\s*방침|회계기준|일반\s*사항|회사의\s*개요")
_SENT_END = re.compile(r"(?<=[다\)]\.)\s*")


def _fv_designation(text: str) -> str | None:
    """글 하나에서 FVOCI 지정 문장(문장 하나)."""
    if "기타포괄손익" not in (text or ""):
        return None
    for sent in _SENT_END.split(text):
        if "기타포괄손익" in sent and _FV_DESIG_EQ.search(sent) and _FV_DESIG.search(sent) \
                and not _FV_DESIG_NEG.search(sent):
            return sent.strip()
    return None


def _fv_designation_facts(filing: dict, by_file: dict, version: str) -> list[dict]:
    out, seen = [], set()
    head: dict = {}
    cands = []
    for p in filing.get("paragraphs") or []:
        text = p.get("text") or ""
        m = _NOTE_HEAD.match(text)
        if m and len(text) < 150:
            head[p.get("file")] = m.group(2)
        sent = _fv_designation(text)
        if sent:
            cands.append((head.get(p.get("file")), _file_scope(p.get("file") or "", by_file),
                          {"sentence_id": p.get("id"), "page": p.get("page"), "file": p.get("file")}, sent))
    for t in filing.get("tables") or []:
        if any(c.get("role") == "value" for c in t.get("columns") or []):
            continue  # 값 열 없는 글 표의 제목만(효성티앤씨 t614)
        sent = _fv_designation(t.get("title") or "")
        if sent:
            region = t.get("region") or {}
            cands.append((region.get("note_title") if region.get("kind") == "note" else None,
                          t.get("fs_scope") or _file_scope(t.get("file") or "", by_file),
                          {"sentence_id": f"t:{t.get('table_id')}:title", "page": t.get("page"), "file": t.get("file")},
                          sent))
    for note, scope, src, sent in cands:
        if not note or not scope or _POLICY_NOTE.search(note) or scope in seen:
            continue
        seen.add(scope)
        out.append({"rcept_no": filing.get("rcept_no"), "amends_rcept_no": filing.get("amends_rcept_no"), "fs_scope": scope,
                    "concept": "FVOCI_DESIGNATION_STMT", "period": "current_end", "amount": None, "unit_kind": None,
                    "amount_krw": None, "label": _MARKER_LABEL["FVOCI_DESIGNATION_STMT"], "label_path": [], "column": "",
                    "target": None, "target_basis": None, "source": src, "source_status": "sentence",
                    "concepts_version": version, "match_basis": "sentence", "note_title": note, "text": sent[:300]})
    return out


def sentence_facts(filing: dict, *, concepts_path: Path | None = None) -> list[dict]:
    """1층 공시 기록(paragraphs·tables) → 문장에서 읽은 투자부동산 공정가치 사실(범위·기간마다 첫 문장 하나)과
    금액 없는 표시 사실 IP_FV_MODEL(공정가치 모형)·IP_FV_NO_MATERIAL_DIFF(차이 없음 문장)·IP_FV_LEADIN(공정가치
    공시 머리 문장)(범위마다 하나)."""
    version = _load_raw(concepts_path).get("concepts_version", "")
    by_file = {t.get("file"): t.get("fs_scope") for t in filing.get("tables") or [] if t.get("file") and t.get("fs_scope")}
    out: list[dict] = []
    seen: set = set()
    cost_scopes = set()  # 원가모형 근거가 있는 범위(수정 1차)
    for t in filing.get("tables") or []:
        if _cost_row_table(t):
            cost_scopes.add(t.get("fs_scope") or _file_scope(t.get("file") or "", by_file))
    for p in filing.get("paragraphs") or []:
        pt = p.get("text") or ""
        if (_IP_COST_SENT.search(pt) or ("투자부동산" in pt and _IP_COST_SENT2.search(pt))) \
                and not _IP_COST_SENT_EXCLUDE.search(pt):
            cost_scopes.add(_file_scope(p.get("file") or "", by_file))
    for p in filing.get("paragraphs") or []:
        text = p.get("text") or ""
        if "투자부동산" not in text or "공정가치" not in text:
            continue
        scope = _file_scope(p.get("file") or "", by_file)
        if not scope:
            continue
        if (scope, "model") not in seen and _IP_FV_MODEL.search(text) and not _IP_FV_MODEL_EXCLUDE.search(text):
            seen.add((scope, "model"))  # 원가모형 근거로 막혀도 문장은 남긴다(판정이 확인 필요로 보냄, 수정 4차)
            out.append(_model_fact(filing, p, scope, version,
                                   "IP_FV_MODEL_BLOCKED" if scope in cost_scopes else "IP_FV_MODEL"))
        if (scope, "leadin") not in seen and _IP_FV_LEADIN.search(text):
            seen.add((scope, "leadin"))
            out.append(_model_fact(filing, p, scope, version, "IP_FV_LEADIN"))
        if (scope, "nodiff") not in seen and _IP_FV_NO_DIFF.search(text):
            seen.add((scope, "nodiff"))
            out.append(_model_fact(filing, p, scope, version, "IP_FV_NO_MATERIAL_DIFF"))
        if "공정가치는" not in text:
            continue
        found = []
        m = _IP_FV_EACH.search(text)
        if m and not (m["unit"] and m["unit"] != m["punit"]):
            found = [("current_end", m["amt"], m["unit"] or m["punit"]), ("prior_end", m["pamt"], m["punit"])]
        else:
            m = _IP_FV_ONE.search(text)
            if m and m["punit"] and m["punit"] != m["unit"]:
                m = None  # 당기·전기 단위가 다르면 오기일 수 있다(「13,595,625원(전기말 12,834,777천원)」) — 쓰지 않는다
            if m:
                found.append((_SENT_WHEN.get(m["when"], "current_end"), m["amt"], m["unit"]))
                if m["pamt"] and m["when"] != "전기말":
                    found.append(("prior_end", m["pamt"], m["punit"] or m["unit"]))
        for period, amt, unit in found:
            if (scope, period) in seen:
                continue
            seen.add((scope, period))
            out.append(_sentence_fact(filing, p, scope, period, amt, unit, version))
    out += _fv_designation_facts(filing, by_file, version)  # c12
    return out
