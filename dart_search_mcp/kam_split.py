"""KAM(핵심감사사항) 원문(`kam_raw` 통짜 텍스트)을 항목 단위로 쪼개고,
항목마다 `item_kind`(실제 핵심감사사항인지 도입 문구인지)를 판정하는
순수 계산 모듈. 네트워크·시계·파일 I/O를 전혀 쓰지 않는다.

`temis-backend/app/services/dart_kam_split.py`(92줄)를 그대로 옮긴 것이다
(finov2 `docs/ops/37_dart_temis_boundary_20260902.md`: "DART 원문을 해석하는
일은 수집기가, 해석된 결과를 질의하는 일은 TEMIS 가 한다" — 통짜 텍스트를
항목으로 쪼개는 것과, 그 항목이 도입 문구인지 판정하는 것 둘 다 원문 해석
이라 수집기 쪽 일이다).

이 모듈이 원본에 더하는 것은 `ItemKind` 판정뿐이다 — 항목 제목이 실제
핵심감사사항인지, 아니면 감사보고서의 정형 도입 문구인지를 가른다. 회계사가
지적한 「도입 문구가 핵심감사사항으로 둔갑」 문제가 여기서 해결된다.

## kam-v2 (2026-09-08) — 항목 경계를 의도적으로 바꿨다

여기까지(`kam-v1`)는 분할 동작을 옮긴 원본과 완전히 동일하게 유지했었다.
회계사 검수에서 10건 중 5건이 틀린 것으로 확인되어(수집기 phase-1 작업 1,
analysis §2 A1) **그 유지 약속을 여기서 깬다**: 「핵심감사사항으로 결정된
이유」·「핵심감사사항이 감사에서 다루어진 방법」은 **항목이 아니라 항목의
속성**이다. 항목 = 제목 한 줄 + 이유 + 절차.

`kam-v1` 과 달라지는 것은 넷이다.

1. **소절 흡수** — 마커로 잘린 조각의 머리가 위 소절 문구면 그 조각은 직전
   항목의 본문에 합친다. 첫 조각부터 소절이거나 첫 마커가 첫 소절보다 뒤에
   있으면 그 마커 계층은 애초에 소절(또는 소절 안쪽) 계층이므로 항목 마커
   후보에서 뺀다(비나텍: 바깥 「1.」이 항목, 안쪽 「(1)(2)」가 소절).
2. **`N)` 마커** — 여는 괄호 없는 `1) 2)`(코이즈)도 항목 마커로 인정한다.
   단 이 표기는 감사절차 목록에도 흔해서, 조각마다 소절 문구를 갖췄을 때만
   인정한다(`_split_by`의 `require_subsection`).
3. **도입 문장 벗기기** — 마커가 없어 단일 항목으로 갈 때, 선두의 정형 도입
   문장(유형 A/B/C 및 유형 A 의 둘째 문장)을 문장 단위로 계속 걷어낸다.
   걷어낼 것밖에 남지 않으면(`_MIN_BODY_LEN` 미만) 그대로 둔다 — 그런 조각은
   여전히 도입 문구다.
4. **제목 = 소절 앞** — 제목은 첫 소절 문구 앞까지로 자른다(꼬리 마커 제거).

`item_kind` 쪽도 하나 좁힌다: `going_concern_notice` 는 **도입 문장뿐인
조각**에만 준다. 본문에 「결정된 이유」·「다루어진 방법」 소절이 있으면
제목이 「계속기업…」이어도 실제 핵심감사사항이다(코이즈 2번 항목
「계속기업가정」이 그 예다).

바뀐 판(version)은 `SPLITTER_VERSION` 으로 산출물 각 행에 실린다 —
temis-backend 에 이미 적재된 `kam-v1` 백필과 뒤섞이지 않게 하기 위해서다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# ---------------------------------------------------------------------------
# 항목 분할
# ---------------------------------------------------------------------------

# 이 모듈이 산출한 분할이 어느 규칙 판인지 나타내는 값. 산출물(`kam_items.jsonl`)
# 각 행에 실린다. 이 파일의 분할·판정 규칙을 바꿀 때마다 함께 올린다.
SPLITTER_VERSION: Final[str] = "kam-v2"

# 감사보고서 정형 문구. 마커가 없어 단일 항목으로 처리할 때만 제거한다.
# 길이 상한({0,400})이 없으면 본문 뒤쪽의 "않습니다"까지 삼켜 항목이 사라진다.
_PREAMBLE_RE: Final[re.Pattern[str]] = re.compile(
    r"^.{0,400}?핵심감사사항은.{0,400}?않습니다\s*[.]?\s*", re.S
)
_LEADING_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[-–—•]\s*|\(?\d{1,2}[.)]\s*|[가-힣][.)]\s*|[①-⑳]\s*)+"
)
# 제목 끝에 남은 소절 마커("재고자산의 평가(1)", "공사진행률의 적정성1)",
# "현금창출단위의 손상검사 가."). 숫자·괄호형은 원문에서 제목에 붙어 나오는
# 일이 잦아 앞 공백을 요구하지 않는다. 반면 한글 한 글자 마커는 앞이 공백이거나
# 문자열 처음일 때만 마커로 본다 — 그러지 않으면 "재고자산의 평가."의 "가."를
# 마커로 오인해 제목을 "재고자산의 평"으로 깎는다.
_TRAILING_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\(\s*\d{1,2}\s*\)|\d{1,2}[.)]|(?:(?<=\s)|^)(?:[-–—•]|[가-힣][.)]|[①-⑳]))\s*$"
)
_CIRCLED: Final[str] = "①②③④⑤⑥⑦⑧⑨⑩"
_PAREN_RE: Final[re.Pattern[str]] = re.compile(r"\((\d{1,2})\)\s*")
# 여는 괄호 없는 "1) 2)" 표기(코이즈 2025 등). "(1)"의 뒷부분에 걸리지 않도록
# 앞이 공백이거나 문자열 처음일 때만, 그리고 뒤에 제목 글자가 올 때만 인정한다.
# 이 표기는 감사절차 목록에도 흔히 쓰이므로(진흥기업·신세계인터내셔날 2025)
# 조각마다 소절 문구를 갖췄을 때만 항목 마커로 인정한다 — `_split_by`의
# `require_subsection` 참조.
_PAREN_CLOSE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:(?<=\s)|^)(\d{1,2})\)\s*(?=[가-힣A-Za-z])"
)
_DOT_RE: Final[re.Pattern[str]] = re.compile(r"(?:(?<=\s)|^)(\d{1,2})\.\s*(?=[가-힣A-Za-z])")
_CIRCLED_RE: Final[re.Pattern[str]] = re.compile(f"([{_CIRCLED}])\\s*")

# 소절 문구 — 항목이 아니라 항목의 속성이다(회계사 검수 analysis §2 A1).
# 원문은 "결정한 이유주석3에"처럼 소절 문구와 본문이 붙어 나오고 띄어쓰기
# 변형도 있어, 글자 사이에 공백을 허용하는 정규식으로 매칭한다. 긴 문구를
# 먼저 두어 같은 위치에서 긴 쪽이 잡히게 한다.
_SUBSECTION_PHRASES: Final[tuple[str, ...]] = (
    "핵심감사사항으로결정된이유",
    "핵심감사사항으로결정한이유",
    "핵심감사사항이감사에서다루어진방법",
    "감사에서다루어진방법",
    "결정된이유",
    "결정한이유",
    "다루어진방법",
)
_SUBSECTION_RE: Final[re.Pattern[str]] = re.compile(
    "|".join(r"\s*".join(re.escape(char) for char in phrase) for phrase in _SUBSECTION_PHRASES)
)

# 도입 문장을 문장 단위로 걷어낼 때 쓰는 문장 끝 표지. 감사보고서 정형 문구는
# 예외 없이 "...합니다." / "...하였습니다."로 끝난다. 원문 표에서 글자가 갈려
# "제공하지는 않습니 다."처럼 띄어쓰기가 끼는 실측 사례가 있어 허용한다
# (국영지앤엠 2025).
_SENTENCE_END_RE: Final[re.Pattern[str]] = re.compile(r"니\s*다\s*[.]\s*")

# 유형 A 선언의 둘째 문장("해당 사항들은 ... 우리는 이런 사항에 대하여 별도의
# 의견을 제공하지는 않습니다." / 수주산업 판 "... 별도의 의견을 제공하는 것은
# 아닙니다."). `classify_item_kind`는 제목만 보므로 이 문장을 도입 문구로
# 판정하지 못한다 — 도입 문장 벗기기에서만 쓰는 별도 표지다. `_PREAMBLE_RE`가
# "않습니다"를 못 찾아 통째로 실패한 보고서를 여기서 건진다.
_PREAMBLE_TAIL_PREFIX: Final[str] = "해당사항"
_PREAMBLE_TAIL_MARK: Final[str] = "별도의의견"
_PREAMBLE_TAIL_WINDOW: Final[int] = 120

_MIN_BODY_LEN: Final[int] = 50
_TITLE_SEPARATOR: Final[str] = " - "
_TITLE_SEPARATOR_WINDOW: Final[int] = 100
_TITLE_FALLBACK_LEN: Final[int] = 40
_TITLE_MAX_LEN: Final[int] = 255


class ItemKind(StrEnum):
    """항목이 실제 핵심감사사항인지, 감사보고서의 정형 도입 문구인지."""

    KAM = "kam"  # 실제 핵심감사사항
    PREAMBLE = "preamble"  # 도입 문구 (아래 유형 A · C)
    GOING_CONCERN_NOTICE = "going_concern_notice"  # 계속기업 경고 도입 문구 (유형 B)
    AUDIT_COMMUNICATION = "audit_communication"  # 「외부감사 실시내용」 표(유형 E). KAM 이 아니다


@dataclass(frozen=True, slots=True)
class KamItem:
    item_no: int
    title: str
    body: str
    kind: ItemKind


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _has_subsection_phrase(text: str) -> bool:
    """본문 어딘가에 「결정된 이유」/「다루어진 방법」 소절 문구가 있는가."""
    return _SUBSECTION_RE.search(text) is not None


def _is_subsection_head(body: str) -> bool:
    """조각의 머리가 소절 문구인가 — 그렇다면 항목이 아니라 직전 항목의 속성이다."""
    match = _SUBSECTION_RE.match(body.lstrip())
    return match is not None


def _extract_title(body: str) -> str:
    cleaned = _LEADING_MARKER_RE.sub("", body).strip()
    subsection = _SUBSECTION_RE.search(cleaned, 0, _TITLE_SEPARATOR_WINDOW)
    if subsection is not None:
        # 소절 문구 앞까지가 제목이다("재고자산의 평가(1) 핵심감사사항으로
        # 결정된 이유..." -> "재고자산의 평가"). 소절 문구로 시작하는 조각은
        # 잘라낼 제목이 없으므로 아래 기존 규칙으로 내려간다.
        head = _TRAILING_MARKER_RE.sub("", cleaned[: subsection.start()]).strip()
        if head:
            return head[:_TITLE_MAX_LEN]
    head = cleaned[:_TITLE_SEPARATOR_WINDOW]
    separator_index = head.find(_TITLE_SEPARATOR)
    if separator_index > 0:
        return cleaned[:separator_index].strip()[:_TITLE_MAX_LEN]
    return cleaned[:_TITLE_FALLBACK_LEN].strip()[:_TITLE_MAX_LEN]


def _split_by(
    regex: re.Pattern[str],
    text: str,
    to_no: Callable[[str], int],
    *,
    require_subsection: bool = False,
) -> list[KamItem] | None:
    """마커가 2개 이상일 때만 분할한다. 첫 마커 앞 내용은 버린다.

    소절 문구로 시작하는 조각은 항목이 아니라 직전 항목의 본문에 흡수한다.
    흡수할 직전 항목이 없거나(= 이 마커 계층이 곧 소절 계층이다) 흡수 후 항목이
    하나뿐이면(= 이 마커는 항목을 나누지 못했다) `None`을 돌려 다음 마커
    후보로 넘긴다.

    첫 마커가 첫 소절 문구보다 뒤에 있으면 그 마커는 소절 **안쪽**에서만 쓰이는
    표기다(금호석유화학 2025: 「(2) 다루어진 방법」 안의 감사절차 목록이
    「1) 2) 3) 4) 5)」로 매겨져 있다). 항목 마커로 인정하지 않는다.

    `require_subsection`이면 조각마다 소절 문구를 갖췄을 때만 인정한다. 소절이
    아예 없는 보고서에서 감사절차 목록이 같은 표기로 매겨진 경우를 걸러낸다
    (진흥기업·신세계인터내셔날 2025: 「우리가 수행한 주요 감사절차는 다음과
    같습니다. 1) ... 2) ...」).
    """
    matches = list(regex.finditer(text))
    if len(matches) < 2:
        return None
    subsection = _SUBSECTION_RE.search(text)
    if subsection is not None and matches[0].start() > subsection.start():
        return None
    items: list[KamItem] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = _collapse(text[match.end() : end])
        if not body:
            continue
        if _is_subsection_head(body):
            if not items:
                return None
            previous = items[-1]
            merged = _collapse(previous.body + " " + text[match.start() : end])
            items[-1] = KamItem(
                item_no=previous.item_no,
                title=previous.title,
                body=merged,
                kind=classify_item_kind(previous.title, merged),
            )
            continue
        title = _extract_title(body)
        items.append(
            KamItem(item_no=to_no(match.group(1)), title=title, body=body, kind=classify_item_kind(title, body))
        )
    if len(items) < 2:
        return None
    if require_subsection and not all(_has_subsection_phrase(item.body) for item in items):
        return None
    return items


def _is_preamble_tail(text: str) -> bool:
    """`text`가 유형 A 선언의 둘째 문장으로 시작하는가.

    "해당 사항들은 ... 우리는 이런 사항에 대하여 별도의 의견을 제공하지는
    않습니다." — 제목 40자에는 "별도의 의견"이 들어오지 않아 제목만으로는
    못 잡는다. 표지가 문장 안에 있어야 하므로 앞쪽 창(window)으로 제한한다.
    """
    normalized = _strip_all_whitespace(text)
    return normalized.startswith(_PREAMBLE_TAIL_PREFIX) and (
        _PREAMBLE_TAIL_MARK in normalized[:_PREAMBLE_TAIL_WINDOW]
    )


def _is_intro_sentence(sentence: str) -> bool:
    """이 한 문장이 감사보고서의 정형 도입 문구인가(= 항목 본문이 아닌가)."""
    return classify_item_kind(sentence) is not ItemKind.KAM or _is_preamble_tail(sentence)


def _strip_intro_sentences(text: str) -> str:
    """선두의 정형 도입 문장(유형 A/B/C)을 문장 단위로 걷어낸다.

    걷어낸 뒤 남는 것이 `_MIN_BODY_LEN` 미만이면 걷어내지 않는다 — 그런 조각은
    본문 없이 도입 문구뿐이라 그 자체가 판정 대상이어야 하기 때문이다.
    """
    while True:
        match = _SENTENCE_END_RE.search(text)
        if match is None:
            return text
        tail = text[match.end() :].strip()
        if len(tail) < _MIN_BODY_LEN:
            return text
        sentence = _collapse(text[: match.start()])
        if not _is_intro_sentence(sentence):
            return text
        text = tail


def split_kam_items(kam_raw: str) -> list[KamItem]:
    """핵심감사사항 원문을 개별 항목으로 나누고 각 항목에 `item_kind`를 붙인다.

    마커 표기가 보고서마다 달라 (N) -> N) -> N. -> 원문자 순으로 시도하고,
    어느 것도 항목을 2개 이상으로 나누지 못하면 전체를 단일 항목으로 반환한다.
    같은 입력은 항상 같은 결과를 낸다.
    """
    if not kam_raw or not kam_raw.strip():
        return []

    text = _collapse(kam_raw)
    if len(text) < _MIN_BODY_LEN:
        return []

    # (마커, 번호 변환, 조각마다 소절 문구를 요구할지). `N)`만 요구하는 이유는
    # `_PAREN_CLOSE_RE` 주석 참조 — 감사절차 목록이 즐겨 쓰는 표기라서다.
    strategies: tuple[tuple[re.Pattern[str], Callable[[str], int], bool], ...] = (
        (_PAREN_RE, int, False),
        (_PAREN_CLOSE_RE, int, True),
        (_DOT_RE, int, False),
        (_CIRCLED_RE, lambda char: _CIRCLED.index(char) + 1, False),
    )
    for regex, to_no, require_subsection in strategies:
        items = _split_by(regex, text, to_no, require_subsection=require_subsection)
        if items:
            return items

    single = _strip_intro_sentences(_PREAMBLE_RE.sub("", text, count=1))
    single = _LEADING_MARKER_RE.sub("", single).strip()
    if len(single) < _MIN_BODY_LEN:
        return []
    title = _extract_title(single)
    return [KamItem(item_no=1, title=title, body=single, kind=classify_item_kind(title, single))]


# ---------------------------------------------------------------------------
# item_kind 판정 (신규) — 2026-09-02 회계사 검토·실측 근거:
# finov2 `.superpowers/sdd/2026-09-02-dart-chat-ui/
# task-A-kam-split-collector.md`. 아래 상수는 그 실측에서 확인된 정형 문구
# 그대로다. 판정 전 제목의 공백을 모두 제거한 뒤 비교한다 — 원문에
# 띄어쓰기 변형이 있어(예: 이원컴포텍 2025 "...단락 에 기술된...") 단순
# 매칭으로는 놓친다.
# ---------------------------------------------------------------------------

# 유형 B (계속기업 도입, 정형 19건 — 아래 변형 패턴과 합쳐 21건): "우리는
# 계속기업 관련 중요한 불확실성 단락에 기술된 사항에 추가하여, 아래 사항을…"
_GOING_CONCERN_UNCERTAINTY_MARK: Final[str] = "계속기업관련중요한불확실성단락에기술된사항에추가"

# 유형 B 띄어쓰기·표현 변형(동진글로벌 2024 등): "…한정의견 근거 단락에
# 기술된 사항을 제외하고는…". 위 정형 19건과 합쳐 B 유형 21건.
_QUALIFIED_OPINION_BASIS_MARK: Final[str] = "한정의견근거단락에기술된사항을제외하고는"

# 유형 A (선정 선언, 74건): "핵심감사사항은 우리의 전문가적 판단에 따라
# 당기 재무제표감사에서 가장 유의적…" 또는 수주산업(건설 등) 특유의
# "수주산업핵심감사사항은…"으로 시작.
_KAM_DECLARATION_PREFIXES: Final[tuple[str, ...]] = ("핵심감사사항은", "수주산업핵심감사사항은")

# 유형 C (커뮤니케이션 선언, 25건): "우리는 아래에 기술된 사항을 이
# 감사보고서에서 커뮤니케이션할 핵심감사사항으로…"
_COMMUNICATION_PREFIX: Final[str] = "우리는아래에기술된사항을"
_COMMUNICATION_MARK: Final[str] = "커뮤니케이션할핵심감사사항"

# 유형 E (「외부감사 실시내용」 표, 100건, finov2 BRIEF-A-kam-quality.md) —
# 감사위원회 커뮤니케이션 내역 표가 KAM 으로 통째로 들어온 것. 도입 문구가
# 아니라 아예 다른 절이라 제목만으로는 못 잡는다(제목이 정상 KAM 제목처럼
# 보이는 경우가 있다) — 그래서 이 판정만 본문(body)을 본다. 아래 어휘 중
# 2개 이상이 본문에 나오면 커뮤니케이션 표로 본다. 1개만 요구하면 "감사인
# 측:" 같은 어휘가 진짜 KAM 본문 인용에 오탐할 수 있어 2개로 고정한다(공백
# 정규화 없이, 원문 그대로 부분 문자열 매칭 — 실측으로 100건 확인, task
# 지시서 참조).
_AUDIT_COMMUNICATION_MARKERS: Final[tuple[str, ...]] = (
    "회사측:",
    "감사인측:",
    "업무수행이사",
    "대면회의",
    "서면회의",
    "구두회의",
)
_AUDIT_COMMUNICATION_MIN_MARKERS: Final[int] = 2


def _strip_all_whitespace(text: str) -> str:
    return "".join(text.split())


def _count_audit_communication_markers(body: str) -> int:
    return sum(1 for marker in _AUDIT_COMMUNICATION_MARKERS if marker in body)


def classify_item_kind(title: str, body: str = "") -> ItemKind:
    """항목 제목(및 본문)으로 `item_kind`를 판정한다.

    아래 순서대로 검사한다:
    1. 계속기업 관련 불확실성 단락 도입 문구 포함(제목, 공백 제거 기준) -> GOING_CONCERN_NOTICE
    2. 한정의견 근거 단락 도입 문구 포함(제목, 공백 제거 기준) -> GOING_CONCERN_NOTICE
    3. 핵심감사사항 선정 선언으로 시작(제목, 공백 제거 기준) -> PREAMBLE
    4. 커뮤니케이션 선언으로 시작하거나 그 표현을 포함(제목, 공백 제거 기준) -> PREAMBLE
    5. 유형 A 선언의 둘째 문장("해당 사항들은 ... 별도의 의견을 제공하지는
       않습니다")으로 시작하는 본문 -> PREAMBLE. 제목 40자에는 "별도의 의견"이
       들어오지 않아 제목만으로는 못 잡는다 — 도입 문구뿐인 원문(동성제약
       20260506000528)이 항목으로 둔갑하는 것을 막는다
    6. 「외부감사 실시내용」 표 어휘가 본문에 2개 이상(원문 그대로, 공백
       제거 없이) -> AUDIT_COMMUNICATION — 도입 문구(1~5)보다 나중에 본다
    7. 그 외 -> KAM

    kam-v2: 1·2번은 **도입 문장뿐인 조각**에만 적용한다. 본문에 「결정된 이유」
    ·「다루어진 방법」 소절이 있으면 그 조각은 이유와 절차를 갖춘 실제
    핵심감사사항이므로, 제목이 계속기업 도입 문구를 닮았어도 KAM 이다
    (코이즈 2025 의 두 번째 항목 「계속기업가정」이 그 예다).

    `body`를 생략하면(빈 문자열) 5·6번 검사는 항상 통과하지 못한다 — 제목만
    으로는 유형 E를 판정할 수 없기 때문이다(위 이유 참조).
    """
    normalized = _strip_all_whitespace(title)
    if not _has_subsection_phrase(body):
        if _GOING_CONCERN_UNCERTAINTY_MARK in normalized:
            return ItemKind.GOING_CONCERN_NOTICE
        if _QUALIFIED_OPINION_BASIS_MARK in normalized:
            return ItemKind.GOING_CONCERN_NOTICE
    if normalized.startswith(_KAM_DECLARATION_PREFIXES):
        return ItemKind.PREAMBLE
    if normalized.startswith(_COMMUNICATION_PREFIX) or _COMMUNICATION_MARK in normalized:
        return ItemKind.PREAMBLE
    if not _has_subsection_phrase(body) and _is_preamble_tail(body):
        return ItemKind.PREAMBLE
    if _count_audit_communication_markers(body) >= _AUDIT_COMMUNICATION_MIN_MARKERS:
        return ItemKind.AUDIT_COMMUNICATION
    return ItemKind.KAM
