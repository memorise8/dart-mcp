"""2층 정책 문장·예외 문장 표시 (스펙 §5.4).

주제 낱말이 든 문장마다 주제별로 policy | company_statement | exception 을 표시한다.
3층은 이것으로 「정책 문장만 있음 → 판단불가」를 말하고, 예외 문장 몇 가지를 근거로 받는다.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

RULES_PATH = Path(__file__).with_name("rules.json")
MARKS_VERSION = "p1"

_PAST_RE = re.compile(r"(?:하였|했|되었|였|않았|아니하였)(?:습니다|다)\s*\.?\s*$")
_TABLE_NOTE_RE = re.compile(r"무형자산|내용연수")
_YEARS_RE = re.compile(r"\d+\s*년")


@lru_cache(maxsize=4)
def _load_exceptions(path: str) -> tuple[tuple[str, str, re.Pattern], ...]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))["exception_patterns"]
    return tuple((name, d["topic"], re.compile(d["pattern"])) for name, d in raw.items())


def load_exception_patterns(path: Path | None = None) -> tuple[tuple[str, str, re.Pattern], ...]:
    return _load_exceptions(str(path or RULES_PATH))


def _topics():
    from dart_search_mcp.topic_judge.topics import TOPIC_DEFS
    return TOPIC_DEFS


def _kind_of(text: str, in_block: bool, is_footnote: bool) -> str | None:
    from dart_search_mcp.policy_sentences import KIND_COMPANY, classify_sentence
    kind, _subject = classify_sentence(text)
    if kind == KIND_COMPANY and _PAST_RE.search(text.strip()):
        return "company_statement"
    if is_footnote:
        return "company_statement"  # 각주는 그 표(회사 수치)에 붙은 회사 고유 설명이다
    if kind in ("company_statement", "standard_description", "conditional", "list_preamble"):
        return "policy"
    return "policy" if in_block or re.search(r"(?:합니다|있습니다|수 있다)\.?\s*$", text.strip()) else None


def _row(rcept_no, topic, kind, sid, text, exc):
    return {"rcept_no": rcept_no, "topic": topic, "kind": kind, "sentence_id": sid, "text": text, "exception": exc}


def _mark_sentence(rcept_no, sid, text, in_block, is_footnote, topics, exceptions, out):
    base = _kind_of(text, in_block, is_footnote)
    for code, td in topics.items():
        if not td.keyword_re.search(text):
            continue
        exc = next((n for n, t, p in exceptions if t == code and p.search(text)), None)
        kind = base
        if exc and kind != "company_statement":
            kind = "exception"
        if kind is None:
            continue
        out.append(_row(rcept_no, code, kind, sid, text, exc))


def mark_policies(rcept_no: str, paragraphs: list[dict], tables: list[dict], *,
                  rules_path: Path | None = None) -> list[dict]:
    from dart_search_mcp.policy_blocks import split_policy_blocks

    topics = _topics()
    exceptions = load_exception_patterns(rules_path)
    # 문단을 줄바꿈으로 이어 붙여 정책 블록을 찾고, 문단 시작 위치가 블록 안인지 본다.
    text, starts = "", []
    for p in paragraphs:
        starts.append(len(text))
        text += p["text"] + "\n"
    spans = [(b.start, b.start + len(b.text)) for b in split_policy_blocks(text)]
    out: list[dict] = []
    for p, pos in zip(paragraphs, starts):
        in_block = any(a <= pos < b for a, b in spans)
        _mark_sentence(rcept_no, p["id"], p["text"], in_block, False, topics, exceptions, out)

    seen: set[str] = set()
    dev_topic = next((t for n, t, _p in exceptions if n == "dev_amortization"), "DEV_COST_CAPITALIZATION")
    for t in tables:
        tid = t.get("table_id", "")
        for k, fn in enumerate(t.get("footnotes") or [], start=1):
            ftext = (fn.get("text") or "").strip()
            if not ftext or ftext in seen:
                continue
            seen.add(ftext)
            _mark_sentence(rcept_no, f"t:{tid}:f{k}", ftext, False, True, topics, exceptions, out)
        region = t.get("region") or {}
        head = f"{region.get('note_title') or ''} {t.get('title') or ''}"
        if not _TABLE_NOTE_RE.search(head):
            continue
        for r in t.get("rows") or []:
            label = r.get("label") or ""
            if "개발비" not in label:
                continue
            raws = [c.get("raw") or "" for c in r.get("cells") or []]
            if any(_YEARS_RE.search(x) for x in raws):
                out.append(_row(rcept_no, dev_topic, "exception", f"t:{tid}:r{r.get('row')}",
                                " | ".join([label] + raws), "dev_amortization"))
    return out
