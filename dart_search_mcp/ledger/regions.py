"""1층 문서 훑기 — 파일 하나에서 표 위치·구간(본문 재무제표·주석·감사인 보고서)·페이지·제목·각주·문단을 잡는다.

표 안의 숫자는 보지 않는다(tables.py 몫). 구간 경계는 dart-agent 파서를 그대로 쓴다:
  - 본문 재무제표: fs_table.find_attachment_regions(「(첨부)」) → 없으면 find_body_statement_regions,
    표 종류는 fs_table.classify_table(표 앞 글)
  - 주석: 「주석」 TITLE 뒤 ~ 다음 TITLE(note_section.extract_note_section과 같은 경계),
    주석 번호·제목은 note_heading.split_by_headings
  - 감사인 보고서: 「…감사인의 감사보고서」 TITLE 뒤 ~ 다음 TITLE
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from . import _dart_agent

TABLE_RE = re.compile(r"<TABLE(?=[\s>])([^>]*)>(.*?)</TABLE>", re.S | re.I)
P_RE = re.compile(r"<P\b[^>]*>(.*?)</P>", re.S | re.I)
PGBRK_RE = re.compile(r"<PGBRK\b", re.I)
TITLE_RE = re.compile(r"<TITLE\b[^>]*>(.*?)</TITLE>", re.S | re.I)
_TEXT_TAG_RE = re.compile(r"<(P|TD|TH|TE|SUMMARY)\b[^>]*>(.*?)</\1>", re.S | re.I)
_AFTER_TOKEN_RE = re.compile(r"\s*(?:<P\b[^>]*>(.*?)</P>|<PGBRK\b[^>]*>(?:\s*</PGBRK>)?)", re.S | re.I)
FOOTNOTE_RE = re.compile(r"^(\(\*\d*\)|\*\d*\)|주\d*\))")
_UNIT_RE = re.compile(r"\(\s*단\s*위\s*[:：]?[^)]*\)")
_SUB1_RE = re.compile(r"^\(\d{1,2}\)")
_SUB2_RE = re.compile(r"^(?:[①-⑳]|\d{1,2}\)|[가-하]\.)")
_BORDER0_RE = re.compile(r'BORDER\s*=\s*"?0"?', re.I)

TITLE_MAX = 200
CLASSIFY_LOOKBACK = 2500  # fs_table.parse_document 와 같은 거리
CONTEXT_PARAS = 3

SJ_KIND = {"BS": "statement_bs", "IS": "statement_is", "CIS": "statement_is",
           "SCE": "statement_ce", "CF": "statement_cf"}


@dataclass
class Spot:
    """파일 안 표 하나의 자리."""
    index: int
    start: int
    end: int
    xml: str
    page: int
    region: dict
    title_p: str | None
    context: str
    footnotes: list[dict]


@dataclass
class FileScan:
    spots: list[Spot]
    paragraphs: list[tuple[int, str]]  # (페이지, 문단 글) — 표 밖 <P>만
    cover_text: str                    # 보고기간 종료일을 읽을 앞부분(주석 앞까지)


def plain(s: str) -> str:
    return _dart_agent.fs_table().clean_text(s)


def _squash(s: str) -> str:
    return _dart_agent.fs_table().squash(s)


def _sections(text: str) -> tuple[list, list, list]:
    """(본문 재무제표 구간들, 주석 구간들, 감사인 보고서 구간들). 구간 = (시작, 끝, Region|None)."""
    fs = _dart_agent.fs_table()
    titles = [(m.start(), m.end(), _squash(m.group(1))) for m in TITLE_RE.finditer(text)]
    stmts = [(r.start, r.end, r) for r in fs.find_attachment_regions(text)]
    if not stmts:
        stmts = [(r.start, r.end, r) for r in fs.find_body_statement_regions(text)]
    notes, auditor = [], []
    for i, (_s, e, t) in enumerate(titles):
        nxt = titles[i + 1][0] if i + 1 < len(titles) else len(text)
        if t == "주석":
            notes.append((e, nxt, None))
        elif "감사인의감사보고서" in t or "감사인의검토보고서" in t:
            auditor.append((e, nxt, None))
    return stmts, notes, auditor


def _in(spans: list, pos: int):
    for s, e, r in spans:
        if s <= pos < e:
            return (s, e, r)
    return None


def _note_headings(text: str, start: int, end: int) -> list[tuple[int, str, str]]:
    """주석 구간의 표제 (원문 위치, 번호, 제목). 글을 이어 붙인 뒤 split_by_headings로 찾고 위치를 되짚는다."""
    parts: list[str] = []
    offsets: list[int] = []  # 이어 붙인 글에서 각 조각의 시작
    xml_pos: list[int] = []
    n = 0
    for m in _TEXT_TAG_RE.finditer(text, start, end):
        t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2).replace("&cr;", " "))).strip()
        if not t:
            continue
        offsets.append(n)
        xml_pos.append(m.start())
        parts.append(t)
        n += len(t) + 1
    linear = " ".join(parts)
    out = []
    for h, _body in _dart_agent.note_heading().split_by_headings(linear):
        i = max(bisect.bisect_right(offsets, h.start) - 1, 0)
        out.append((xml_pos[i] if xml_pos else start, str(h.note_no), h.title))
    return out


def _sub_path(text: str, start: int, pos: int, table_spans: list[tuple[int, int]]) -> list[str]:
    """주석 표제 뒤 ~ 표 앞의 「(2)」·「①」 소제목 표지."""
    lvl1 = lvl1_pos = None
    lvl2 = None
    for m in P_RE.finditer(text, start, pos):
        if _inside(table_spans, m.start()):
            continue
        t = plain(m.group(1))
        m1 = _SUB1_RE.match(t)
        if m1:
            lvl1, lvl1_pos, lvl2 = m1.group(0), m.start(), None
            continue
        m2 = _SUB2_RE.match(t)
        if m2 and (lvl1_pos is None or m.start() > lvl1_pos):
            lvl2 = m2.group(0)
    return [x for x in (lvl1, lvl2) if x]


def _inside(spans: list[tuple[int, int]], pos: int) -> bool:
    i = bisect.bisect_right(spans, (pos, float("inf"))) - 1
    return i >= 0 and spans[i][0] <= pos < spans[i][1]


def _footnotes(text: str, pos: int) -> list[dict]:
    """표 바로 뒤에 이어지는 각주 문단(빈 문단·페이지 나눔은 건너뜀, 다른 문단이 나오면 멈춤)."""
    out = []
    while True:
        m = _AFTER_TOKEN_RE.match(text, pos)
        if not m:
            break
        pos = m.end()
        if m.group(1) is None:
            continue
        t = plain(m.group(1))
        if not t:
            continue
        fm = FOOTNOTE_RE.match(t)
        if not fm:
            break
        out.append({"mark": fm.group(1), "text": t[fm.end():].strip()})
    return out


def _title_and_context(text: str, gap_start: int, start: int, prev_xml: str | None) -> tuple[str | None, str]:
    paras = [plain(m.group(1)) for m in P_RE.finditer(text, gap_start, start)]
    paras = [p for p in paras if p]
    title = None
    for p in reversed(paras):
        if FOOTNOTE_RE.match(p) or not _UNIT_RE.sub("", p).strip():
            continue
        title = p if len(p) <= TITLE_MAX else None
        break
    ctx = paras[-CONTEXT_PARAS:]
    gap_plain = plain(text[gap_start:start])
    # 바로 붙은 테두리 없는 제목 표(「재무상태표 / 제 n 기 … / (단위: 원)」)는 문맥에 넣는다
    if prev_xml is not None and not gap_plain and _BORDER0_RE.search(prev_xml[:300]):
        ctx = [plain(prev_xml)] + ctx
    elif not paras and gap_plain:
        ctx = [gap_plain[-500:]]
    return title, " ".join(ctx)


def scan_file(text: str) -> FileScan:
    fs = _dart_agent.fs_table()
    tables = list(TABLE_RE.finditer(text))
    table_spans = [(m.start(), m.end()) for m in tables]
    breaks = [m.start() for m in PGBRK_RE.finditer(text)]
    stmts, notes, auditor = _sections(text)
    heads = {s: _note_headings(text, s, e) for s, e, _ in notes}

    def page(pos: int) -> int:
        return bisect.bisect_left(breaks, pos) + 1

    spots: list[Spot] = []
    for i, m in enumerate(tables):
        pos = m.start()
        region: dict = {"kind": "other", "note_no": None, "note_title": None, "sub_path": []}
        note = _in(notes, pos)
        stmt = _in(stmts, pos)
        if note:
            region["kind"] = "note"
            hs = [h for h in heads[note[0]] if h[0] <= pos]
            sub_from = note[0]
            if hs:
                region["note_no"], region["note_title"] = hs[-1][1], hs[-1][2]
                sub_from = hs[-1][0]
            region["sub_path"] = _sub_path(text, sub_from, pos, table_spans)
        elif stmt:
            s, _e, r = stmt
            cl = fs.classify_table(text[max(s, pos - CLASSIFY_LOOKBACK):pos])
            if cl is None and getattr(r, "sj_hint", None):
                cl = (r.is_consolidated, r.sj_hint)
            region["kind"] = SJ_KIND.get(cl[1], "other") if cl else "other"
        elif _in(auditor, pos):
            region["kind"] = "auditor_report"
        gap_start = tables[i - 1].end() if i else 0
        title, context = _title_and_context(text, gap_start, pos, tables[i - 1].group(0) if i else None)
        spots.append(Spot(i, pos, m.end(), m.group(0), page(pos), region, title, context,
                          _footnotes(text, m.end())))

    paragraphs = []
    for m in P_RE.finditer(text):
        if _inside(table_spans, m.start()):
            continue
        t = plain(m.group(1))
        if t:
            paragraphs.append((page(m.start()), t))
    cover_end = min((s for s, _e, _r in notes), default=len(text))
    return FileScan(spots, paragraphs, text[:cover_end])
