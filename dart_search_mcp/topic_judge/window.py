"""WS8 판정 창 고르기(스펙 §4.1).

원문 평문에서 「아직 시행되지 않은 기준서」 절을 빼고, 주제의 우선 위치를
중심으로 창을 자른다. 규칙은 스파이크 `build_inputs.py`의 NOTYET·HEAD를 따른다.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from pathlib import Path

from dart_search_mcp.audit_xml_parser import _strip_tags_to_text
from dart_search_mcp.tools.extract_facts import select_xml_path

from .topics import TopicDef

# 미시행 기준서 절 머리(스파이크 NOTYET 그대로).
NOTYET_RE = re.compile(
    r"아직 (?:시행일이 도래하지 않|적용하지 않|시행되지 않)|시행일이 도래하지 (?:않|아니)"
    r"|제정[ㆍ·]?공표되었으나 (?:아직 )?(?:시행|적용)되지|공표되었으나 시행일"
)
# 다음 번호 머리("3. 중요한 회계정책"). 미시행 절은 여기서 끝난다.
HEAD_RE = re.compile(r"(?:^|\s)\d{1,2}\.\s+[가-힣]{2,}")
# 다음 번호 머리를 못 찾을 때 미시행 절의 최대 길이(스파이크의 2,500자 되돌아보기와 같다).
NOTYET_MAX_SPAN = 2500

PRIORITY_RADIUS = 1500  # 우선 위치가 키워드에서 이만큼 안에 있어야 한다
MAIN_RADIUS = 450       # 중심 구간 반경
KEYWORD_RADIUS = 250    # 덧붙이는 키워드 구간 반경
EXTRA_KEYWORDS = 2      # 덧붙이는 키워드 위치 수
JOINER = " … "

_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")


@dataclass(frozen=True)
class Window:
    text: str
    spans: tuple[tuple[int, int], ...]  # 원문(doc_text) 좌표, 창에 놓인 순서
    anchor: str  # 고른 우선 위치 정규식의 pattern, 또는 "keyword"


def _unescape_entity(m: re.Match) -> str:
    s = html.unescape(m.group(0))
    # `<`·`>`로 풀리는 엔티티는 태그로 오인되지 않게 그대로 둔다(태그 제거기가 공백으로 지운다).
    return m.group(0) if s in ("<", ">") else s


def doc_text(docs_dir: Path, rcept_no: str) -> str | None:
    """공시 폴더에서 XML을 골라 태그를 지운 평문을 낸다. XML이 없으면 None.

    `_strip_tags_to_text`는 엔티티를 공백으로 지우므로, 그 전에 `html.unescape`로
    엔티티를 글자로 푼다(`&nbsp;`→공백, `&amp;`→&). 끝으로 공백을 하나로 모은다."""
    path = select_xml_path(docs_dir, rcept_no)
    if path is None:
        return None
    raw = path.read_bytes().decode("utf-8", errors="replace")
    raw = _ENTITY_RE.sub(_unescape_entity, raw)
    text = _strip_tags_to_text(raw.encode("utf-8"))
    return re.sub(r"\s+", " ", text).strip()


def excluded_ranges(text: str) -> list[tuple[int, int]]:
    """미시행 기준서 절 구간들. 절 머리에서 다음 번호 머리(없으면 2,500자)까지."""
    ranges: list[tuple[int, int]] = []
    for ny in NOTYET_RE.finditer(text):
        cap = min(len(text), ny.end() + NOTYET_MAX_SPAN)
        head = HEAD_RE.search(text, ny.end(), cap)
        end = head.start() if head else cap
        if ranges and ny.start() <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((ny.start(), end))
    return ranges


def _eligible(pos: int, ranges: list[tuple[int, int]]) -> bool:
    return not any(a <= pos < b for a, b in ranges)


def _span(text: str, m: re.Match, radius: int) -> tuple[int, int]:
    return max(0, m.start() - radius), min(len(text), m.end() + radius)


def pick_window(defn: TopicDef, text: str, *, limit: int = 2000) -> Window | None:
    """주제 창을 고른다. 미시행 절 밖에 키워드가 없으면 None(창 없음)."""
    ranges = excluded_ranges(text)
    keywords = [m for m in defn.keyword_re.finditer(text) if _eligible(m.start(), ranges)]
    if not keywords:
        return None

    main: tuple[int, int] | None = None
    anchor = "keyword"
    for pat in defn.priority_res:
        for pm in pat.finditer(text):
            if not _eligible(pm.start(), ranges):
                continue
            if any(abs(pm.start() - k.start()) <= PRIORITY_RADIUS for k in keywords):
                main = _span(text, pm, MAIN_RADIUS)
                anchor = pat.pattern
                break
        if main is not None:
            break

    rest = keywords
    if main is None:
        main = _span(text, keywords[0], MAIN_RADIUS)
        rest = keywords[1:]
    extras = [
        _span(text, k, KEYWORD_RADIUS)
        for k in rest
        if not (main[0] <= k.start() and k.end() <= main[1])
    ][:EXTRA_KEYWORDS]

    # 겹치거나 맞닿은 구간을 합친다. 중심 구간을 품은 묶음이 창 맨 앞에 온다.
    merged: list[list[int]] = []
    for a, b in sorted([main, *extras]):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    first = next(i for i, (a, b) in enumerate(merged) if a <= main[0] and main[1] <= b)
    ordered = [merged[first]] + merged[:first] + merged[first + 1:]

    # limit 안에서 구간을 잇는다. 넘치는 구간은 잘라 내고 spans도 그만큼 줄인다.
    pieces: list[str] = []
    spans: list[tuple[int, int]] = []
    used = 0
    for a, b in ordered:
        room = limit - used - (len(JOINER) if pieces else 0)
        if room <= 0:
            break
        b = min(b, a + room)
        pieces.append(text[a:b])
        spans.append((a, b))
        used += (b - a) + (len(JOINER) if len(pieces) > 1 else 0)
    return Window(text=JOINER.join(pieces), spans=tuple(spans), anchor=anchor)
