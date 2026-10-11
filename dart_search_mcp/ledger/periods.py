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
    """증가·기말 같은 열이 어느 기간인지 문맥에서(표 표지 「<당기>」 → 당기 중/전기 중)."""
    mark = _table_marker(context)
    if mark:
        return "current" if mark.startswith("current") else "prior"
    c = _squash(context)
    if any(w in c for w in _BOTH):
        return ""
    cur, prev = "당기중" in c, "전기중" in c
    if cur and not prev:
        return "current"
    if prev and not cur:
        return "prior"
    return ""


# ---- 표 단위 기간 표지: 문맥 마지막 줄(단위 문구를 뗀 뒤)이 「(당기)」「<전기말>」「- 전기」「나. 당기」 같은 표지 ----
_UNIT_PHRASE_RE = re.compile(r"\([^()\n]{0,6}단\s*위\s*[:：]?[^)\n]*\)|단\s*위\s*[:：][^\n]*$", re.M)
_OPEN, _CLOSE = r"[\(\[<〈【《「『]", r"[\)\]>〉】》」』]"
_TERM = r"(?:\d{4}\s*년\s*)?(?:제\s*\d+\s*)?\(?\s*([당전])\s*\)?\s*(?:분|반)?\s*기\s*(말|중)?\s*(?:현재)?"
_ENUM = r"(?:[①-⑳]|\(\d{1,2}\)|\d{1,2}(?:[-.]\d{1,2})+\.?|\d{1,2}[.)]|[가-하][.)])"
_MARK_LINE_RE = re.compile(
    r"^(?:[-–ㆍ·•]\s*)?" + _ENUM + r"?\s*" + _OPEN + r"?\s*" + _TERM
    + r"\s*" + _CLOSE + r"?\s*[:：.]?$")
_MARK_TAIL_RE = re.compile(_OPEN + r"\s*" + _TERM + r"\s*" + _CLOSE + r"\s*[:：.]?$")
_MARK_SENT_TAIL_RE = re.compile(r"다\.\s*" + _TERM + r"\s*[:：]?$")  # 「…다음과 같습니다. 당기」
_MARK_ENUM_TAIL_RE = re.compile(r"(?:^|(?<=[\s.:)\]>〉】]))" + _ENUM + r"\s*" + _TERM + r"\s*[:：]?$")


def _lines(context: str) -> list[str]:
    """문맥 → 단위 문구를 뗀 비지 않은 줄(문단)들."""
    return [ln.strip() for ln in _UNIT_PHRASE_RE.sub("", context or "").splitlines() if ln.strip()]


def marker_only(context: str) -> bool:
    """문맥이 비었거나 표 표지 한 줄(「〈전기〉」「- 전기」)뿐인가 — 앞 표와 짝인 이어지는 표."""
    lines = _lines(context)
    return not lines or (len(lines) == 1 and _MARK_LINE_RE.match(lines[0]) is not None)


def _table_marker(context: str) -> str:
    """문맥 마지막 줄이 표 단위 기간 표지면 그 기간(말 → *_end, 그 밖 → *_period), 아니면 ""."""
    lines = _lines(context)
    if not lines:
        return ""
    m = _MARK_LINE_RE.match(lines[-1]) or _MARK_TAIL_RE.search(lines[-1]) or _MARK_ENUM_TAIL_RE.search(lines[-1]) \
        or _MARK_SENT_TAIL_RE.search(lines[-1])
    if not m:
        return ""
    side = "current" if m.group(1) == "당" else "prior"
    return f"{side}_end" if m.group(2) == "말" else f"{side}_period"


_PAREN_RE = re.compile(r"\((?![당전]\))[^)]*\)")
_END_RE = re.compile(r"^(?:제\d+기?)?\(?([당전])\)?(?:분)?기기?말$")  # 「제49기(당)기말」「제24(당)기기말」 포함
_TERM_ONLY_RE = re.compile(r"^(?:제\d+기?)?\(?([당전])\)?(?:분)?기(?:중)?$")
_FLOW_RE = re.compile("^(?:" + "|".join(_FLOW) + ")$")


_BRACKETS_RE = re.compile(r"[〈〉\[\]<>【】《》「」『』]")
_SUFFIX_RE = re.compile(r"(?:장부가액|장부금액|잔액|금액|가액|합계|액|계)$")
_PAREN_TERM_RE = re.compile(r"\(((?:당|전)(?:분|반)?기(?:말)?)\)")
_END_PREFIX_RE = re.compile(r"^([당전])(?:분)?기말(?!\d)")


def _kind(text: str) -> str:
    """머리글 글자 전체가 기간 낱말일 때만 종류를 돌려준다(부분 일치 금지: 당기순이익·기초자산 등).

    「2023년(전기)」처럼 괄호 안이 기간 낱말이고 나머지가 연도·날짜뿐이면 괄호 낱말을 쓴다.
    끝의 「금액·잔액·합계·계·액」은 떼고 본다(「당기말 금액」)."""
    s = _BRACKETS_RE.sub("", _squash(text))
    m = _PAREN_TERM_RE.search(s)
    if m:  # 「2023년(전기)」「최소리스료(당기말)」: 괄호 안 기간 낱말이 열의 기간
        s = m.group(1)
    m = _END_PREFIX_RE.match(s)
    if m:  # 「당기말 원화환산금액」: 당기말·전기말로 시작(당기순이익 같은 낱말은 「말」이 없어 안 걸린다)
        return "current_end" if m.group(1) == "당" else "prior_end"
    t0 = _PAREN_RE.sub("", s)
    t = _SUFFIX_RE.sub("", t0)
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
    if _FLOW_RE.match(re.sub(r"(?:잔액|액)$", "", t0)):  # 흐름 낱말은 T2 규칙대로(「취득가액」은 흐름이 아니다)
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


_BOTH = ("당기와전기", "당기및전기", "전기와당기", "당기ㆍ전기", "당기·전기", "당기,전기")
_T2_WORDS = (("당기말", "current_end"), ("당분기말", "current_end"), ("전기말", "prior_end"))
_TITLE_WORDS = _T2_WORDS + (("보고기간종료일현재", "current_end"), ("보고기간말현재", "current_end"),
                            ("재무상태표일현재", "current_end"), ("재무제표일현재", "current_end"),
                            ("기말현재", "current_end"), ("당기중", "current_period"), ("전기중", "prior_period"))


def _words_period(text: str, words) -> str:
    """기간 낱말이 정확히 한 종류면 그 기간, 여러 종류·「당기와 전기」면 "unknown", 없으면 ""."""
    c = _squash(text)
    if any(w in c for w in _BOTH):
        return "unknown"
    found = set()
    for w, p in words:
        if w in c:
            found.add(p)
            c = c.replace(w, "")
    if not found:
        return ""
    return found.pop() if len(found) == 1 else "unknown"


_SENT_END_RE = re.compile(r"(?<=다\.)|(?<=다\))|(?<=으며,)|(?<=이며,)")


def _context_period(context: str) -> str:
    """① 마지막 줄의 표 표지 ② 마지막 줄(표 제목)의 기간 낱말(보고기간종료일 현재·당기 중 등 포함)
    ③ 문맥 전체의 당기말·전기말(T2). 두 기간을 함께 말하면 정하지 않는다."""
    mark = _table_marker(context)
    if mark:
        return mark
    lines = _lines(context)
    if lines:
        sentences = [x for x in _SENT_END_RE.split(lines[-1]) if x.strip()]
        for text in (sentences[-1] if sentences else "", lines[-1]):  # 마지막 문장(표 제목) → 마지막 줄
            p = _words_period(text, _TITLE_WORDS)
            if p:
                return p
    return _words_period(context, _T2_WORDS) or "unknown"


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


_TERM_NO_RE = re.compile(r"^제(\d+)기(말)?$")
_YEAR_HEAD_RE = re.compile(r"^(\d{4})년?(말)?$")


def _pair_periods(paths: list[list[str]], cols: list[int], period_end: str | None) -> dict[int, str]:
    """「제24기 / 제23기」(기수 차 1) 또는 「2025년 / 2024년」(보고기간 연도와 그 전 해) 짝 머리글 → 당기·전기.
    짝이 아니면(상환 연도 등) 비운다."""
    found: dict[int, tuple[str, int, bool]] = {}
    for c in cols:
        for el in reversed(paths[c]):
            t = _BRACKETS_RE.sub("", _squash(el))
            m = _TERM_NO_RE.match(t)
            if m:
                found[c] = ("term", int(m.group(1)), bool(m.group(2)))
                break
            m = _YEAR_HEAD_RE.match(t)
            if m:
                found[c] = ("year", int(m.group(1)), bool(m.group(2)))
                break
    end = _norm_end(period_end)
    out: dict[int, str] = {}
    for kind in ("term", "year"):
        mine = {c: v for c, v in found.items() if v[0] == kind}
        nums = sorted({v[1] for v in mine.values()})
        if len(nums) != 2 or nums[1] - nums[0] != 1:
            continue
        if kind == "year" and (not end or nums[1] != int(end[:4])):
            continue
        for c, (_k, n, is_end) in mine.items():
            side = "current" if n == nums[1] else "prior"
            out[c] = f"{side}_end" if is_end else f"{side}_period"
    return out


def _corner_period(paths: list[list[str]], label_cols: list[int]) -> str:
    """레이블 열 머리글(표 왼쪽 위 칸)이 「전기」「〈당기말〉」「제27(당)기」 같은 표지면 그 기간(한 종류일 때만)."""
    found = set()
    for c in label_cols:
        for el in paths[c]:
            k = _kind(el)
            if k in ("current_end", "prior_end"):
                found.add(k)
            elif k in ("current_term", "prior_term"):
                found.add(k.replace("_term", "_period"))
    return found.pop() if len(found) == 1 else "unknown"


def classify_columns(grid: Grid, period_end: str | None, context: str) -> list[ColumnInfo]:
    paths = header_paths(grid)
    is_value = [_is_value_col(grid, c) for c in range(grid.n_cols)]
    order = _statement_order(context) if grid.fmt == "FINANCE" and grid.header_rows == 0 else []
    groups = _finance_groups(grid, is_value) if order else {}
    ctx_period = _context_period(context)
    first: dict[int, tuple[str, str | None]] = {}
    k = 0
    for c in range(grid.n_cols):
        if not is_value[c]:
            continue
        path = paths[c]
        date = next((d for d in map(_find_date, reversed(path)) if d), None)
        period = _path_period(path, context)
        if period == "unknown":
            period = _date_period(date, period_end)
        g = groups.get(c, k)
        if period == "unknown" and g < len(order):
            period = "current_end" if order[g] == "current" else "prior_end"
        k += 1
        first[c] = (period, date)
    if not order:  # 열 머리글로 못 정한 값 열: 짝 머리글 → 왼쪽 위 표지 → 문맥(표 단위 기간)
        unknown = [c for c, (p, _d) in first.items() if p == "unknown"]
        pairs = _pair_periods(paths, unknown, period_end)
        corner = _corner_period(paths, [c for c in range(grid.n_cols) if not is_value[c]])
        table_level = corner if corner != "unknown" else ctx_period
        for c in unknown:
            first[c] = (pairs.get(c, table_level), first[c][1])
    out: list[ColumnInfo] = []
    for c in range(grid.n_cols):
        if c in first:
            out.append(ColumnInfo(c, paths[c], "value", *first[c]))
        else:
            out.append(ColumnInfo(c, paths[c], "label", "unknown", None))
    return out
