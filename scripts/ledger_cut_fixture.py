"""1층 테스트 픽스처 자르기 — 공시 원문에서 문자열이 든 표와 그 주변만 원문 그대로 꺼낸다.

꺼내는 것(문서 순서, 원문 조각 그대로):
  DOCUMENT-NAME 줄(ACODE로 별도·연결 구분) → 표지 보고기간 표 묶음(PERIODTO) → 「주석」 TITLE
  → 가까운 주석 제목 문단 → 표 앞 문단 2개 → 표 → 표 바로 뒤 각주 문단.

개인 이름이 들 수 있는 표(성명·주주·임원·대표이사 머리글)는 자르지 않는다.

    python scripts/ledger_cut_fixture.py --rcept 20260318001076 --contains 자본요소 --name cb
    python scripts/ledger_cut_fixture.py --rcept R --contains S --list
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_DOCS = ROOT.parent / "dart-search-mcp" / "dart_collected" / "docs"
DEFAULT_OUT = ROOT / "tests" / "fixtures" / "ledger"

TABLE_RE = re.compile(r"<TABLE(?=[\s>])[^>]*>.*?</TABLE>", re.S | re.I)
P_RE = re.compile(r"<P\b[^>]*>(.*?)</P>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
DOCNAME_RE = re.compile(r"<DOCUMENT-NAME\b[^>]*>.*?</DOCUMENT-NAME>", re.S | re.I)
COVER_GROUP_RE = re.compile(r"<TABLE-GROUP\b[^>]*>(?:(?!</TABLE-GROUP>).)*?PERIODTO.*?</TABLE-GROUP>", re.S | re.I)
TITLE_RE = re.compile(r"<TITLE\b[^>]*>.*?</TITLE>", re.S | re.I)
NOTE_TITLE_RE = re.compile(r"<TITLE[^>]*>\s*주\s*석\s*</TITLE>", re.I)
NOTE_HEAD_RE = re.compile(r"^\s*\d{1,2}\s*\.\s*\S")
FOOTNOTE_RE = re.compile(r"^\(\*\d*\)|^\*\d*\)|^주\d*\)")
PERSON_RE = re.compile(r"성\s*명|주\s*주\s*명|주주\s*현황|임\s*원|대\s*표\s*이\s*사")


def _plain(s: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", s)).strip()


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("euc-kr", "replace")


def _prev_paragraphs(text: str, end: int, n: int, floor: int) -> list[re.Match[str]]:
    """end 앞(표 밖) 비어 있지 않은 <P> n개. floor(이전 표 끝) 앞으로는 가지 않는다."""
    hits = [m for m in P_RE.finditer(text, floor, end) if _plain(m.group(1))]
    return hits[-n:]


def _footnotes(text: str, pos: int) -> list[re.Match[str]]:
    out = []
    tok = re.compile(r"\s*(?:<P\b[^>]*>(.*?)</P>|<PGBRK\b[^>]*>(?:</PGBRK>)?)", re.S | re.I)
    while True:
        m = tok.match(text, pos)
        if not m:
            break
        body = m.group(1)
        if body is not None and _plain(body):
            if not FOOTNOTE_RE.match(_plain(body)):
                break
            out.append(m)
        pos = m.end()
    return out


def find_tables(text: str, contains: str) -> list[re.Match[str]]:
    """문자열이 표 안이나 표 바로 뒤 각주에 있는 표."""
    out = []
    needle = re.sub(r"\s+", "", contains)
    for m in TABLE_RE.finditer(text):
        body = m.group(0) + "".join(f.group(0) for f in _footnotes(text, m.end()))
        if needle in re.sub(r"\s+", "", _plain(body)):
            out.append(m)
    return out


def cut(text: str, tables: list[re.Match[str]]) -> str:
    """표마다 주변 조각을 문서 순서로 모아 하나의 작은 XML로."""
    spans: list[tuple[int, int]] = []
    m = DOCNAME_RE.search(text)
    if m:
        spans.append(m.span())
    m = COVER_GROUP_RE.search(text)
    if m:
        spans.append(m.span())
    note = NOTE_TITLE_RE.search(text)
    if note:
        spans.append(note.span())
    all_tables = list(TABLE_RE.finditer(text))
    for t in tables:
        if PERSON_RE.search(_plain(t.group(0))):
            raise SystemExit(f"개인 이름이 들 수 있는 표라 자르지 않음: {_plain(t.group(0))[:60]}")
        prev_end = max((x.end() for x in all_tables if x.end() <= t.start()), default=0)
        if note and note.end() <= t.start():
            heads = [p for p in P_RE.finditer(text, note.end(), t.start()) if NOTE_HEAD_RE.match(_plain(p.group(1)))]
            if heads:
                spans.append(heads[-1].span())
        titles = [x for x in TITLE_RE.finditer(text, 0, t.start())]
        if titles:  # 표가 속한 구간 제목과 (첨부) 제목
            spans.append(titles[-1].span())
            att = [x for x in titles if "(첨부)" in _plain(x.group(0))]
            if att:
                spans.append(att[-1].span())
        prev = [x for x in all_tables if x.end() <= t.start()]
        if prev and not _plain(text[prev[-1].end():t.start()]) and re.search(r'BORDER="0"', prev[-1].group(0)[:200]) \
                and not PERSON_RE.search(_plain(prev[-1].group(0))):
            spans.append(prev[-1].span())  # 바로 붙은 제목 표(기간·단위)
        spans.extend(p.span() for p in _prev_paragraphs(text, t.start(), 2, prev_end))
        spans.append(t.span())
        spans.extend(f.span() for f in _footnotes(text, t.end()))
    spans = sorted(set(spans))
    parts = [text[a:b].strip("\r\n") for a, b in spans]
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<!-- scripts/ledger_cut_fixture.py 로 원문에서 잘라 냄 — 조각 안의 글자는 고치지 않았다 -->\n"
        "<DOCUMENT>\n<BODY>\n" + "\n".join(parts) + "\n</BODY>\n</DOCUMENT>\n"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--docs", type=Path, default=DEFAULT_DOCS)
    ap.add_argument("--rcept", required=True)
    ap.add_argument("--contains", required=True)
    ap.add_argument("--acode", help="파일 접미사(00760·00761)로 좁히기")
    ap.add_argument("--index", type=int, action="append", help="맞는 표 중 몇 번째(0부터)만; 여러 번 줄 수 있음")
    ap.add_argument("--name", help="픽스처 설명(파일명 <rcept>_<name>.xml)")
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--list", action="store_true", help="맞는 표만 보여 주고 끝")
    args = ap.parse_args(argv)

    files = sorted((args.docs / args.rcept).glob("*.xml"))
    if args.acode:
        files = [f for f in files if f.stem.endswith("_" + args.acode)]
    for f in files:
        text = _decode(f.read_bytes())
        hits = find_tables(text, args.contains)
        if args.index:
            hits = [hits[i] for i in args.index if i < len(hits)]
        if args.list or not args.name:
            for i, h in enumerate(hits):
                print(f"{f.name}\t#{i}\t{_plain(h.group(0))[:120]}")
            continue
        if not hits:
            continue
        args.out_dir.mkdir(parents=True, exist_ok=True)
        out = args.out_dir / f"{args.rcept}_{args.name}.xml"
        out.write_text(cut(text, hits), encoding="utf-8")
        print(f"wrote {out} ({len(hits)} tables from {f.name})")
        return 0
    return 0 if (args.list or not args.name) else 1


if __name__ == "__main__":
    raise SystemExit(main())
