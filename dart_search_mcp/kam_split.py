"""KAM(핵심감사사항) 원문(`kam_raw` 통짜 텍스트)을 항목 단위로 쪼개고,
항목마다 `item_kind`(실제 핵심감사사항인지 도입 문구인지)를 판정하는
순수 계산 모듈. 네트워크·시계·파일 I/O를 전혀 쓰지 않는다.

`temis-backend/app/services/dart_kam_split.py`(92줄)를 그대로 옮긴 것이다
(finov2 `docs/ops/37_dart_temis_boundary_20260902.md`: "DART 원문을 해석하는
일은 수집기가, 해석된 결과를 질의하는 일은 TEMIS 가 한다" — 통짜 텍스트를
항목으로 쪼개는 것과, 그 항목이 도입 문구인지 판정하는 것 둘 다 원문 해석
이라 수집기 쪽 일이다). **분할 동작(마커 우선순위·최소 길이·제목 규칙)은
옮긴 원본과 완전히 동일하다** — temis-backend 쪽에 이미 3,543행이 이 로직
으로 적재되어 있고(finov2 `.superpowers/sdd/2026-09-02-dart-chat-ui/
task-A-kam-split-collector.md` 게이트 ①), 바꾸면 그 백필이 깨진다.

이 모듈이 원본에 더하는 것은 `ItemKind` 판정뿐이다 — 항목 제목이 실제
핵심감사사항인지, 아니면 감사보고서의 정형 도입 문구인지를 가른다. 회계사가
지적한 「도입 문구가 핵심감사사항으로 둔갑」 문제가 여기서 해결된다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

# ---------------------------------------------------------------------------
# 항목 분할 (원본 `dart_kam_split.split_kam_items`과 동일 — 값도 순서도
# 바꾸지 않는다)
# ---------------------------------------------------------------------------

# 감사보고서 정형 문구. 마커가 없어 단일 항목으로 처리할 때만 제거한다.
# 길이 상한({0,400})이 없으면 본문 뒤쪽의 "않습니다"까지 삼켜 항목이 사라진다.
_PREAMBLE_RE: Final[re.Pattern[str]] = re.compile(
    r"^.{0,400}?핵심감사사항은.{0,400}?않습니다\s*[.]?\s*", re.S
)
_LEADING_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[-–—•]\s*|\(?\d{1,2}[.)]\s*|[가-힣][.)]\s*|[①-⑳]\s*)+"
)
_CIRCLED: Final[str] = "①②③④⑤⑥⑦⑧⑨⑩"
_PAREN_RE: Final[re.Pattern[str]] = re.compile(r"\((\d{1,2})\)\s*")
_DOT_RE: Final[re.Pattern[str]] = re.compile(r"(?:(?<=\s)|^)(\d{1,2})\.\s*(?=[가-힣A-Za-z])")
_CIRCLED_RE: Final[re.Pattern[str]] = re.compile(f"([{_CIRCLED}])\\s*")

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


@dataclass(frozen=True, slots=True)
class KamItem:
    item_no: int
    title: str
    body: str
    kind: ItemKind


def _collapse(text: str) -> str:
    return " ".join(text.split())


def _extract_title(body: str) -> str:
    cleaned = _LEADING_MARKER_RE.sub("", body).strip()
    head = cleaned[:_TITLE_SEPARATOR_WINDOW]
    separator_index = head.find(_TITLE_SEPARATOR)
    if separator_index > 0:
        return cleaned[:separator_index].strip()[:_TITLE_MAX_LEN]
    return cleaned[:_TITLE_FALLBACK_LEN].strip()[:_TITLE_MAX_LEN]


def _split_by(
    regex: re.Pattern[str], text: str, to_no: Callable[[str], int]
) -> list[KamItem] | None:
    """마커가 2개 이상일 때만 분할한다. 첫 마커 앞 내용은 버린다."""
    matches = list(regex.finditer(text))
    if len(matches) < 2:
        return None
    items: list[KamItem] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = _collapse(text[match.end() : end])
        if not body:
            continue
        title = _extract_title(body)
        items.append(
            KamItem(item_no=to_no(match.group(1)), title=title, body=body, kind=classify_item_kind(title))
        )
    return items or None


def split_kam_items(kam_raw: str) -> list[KamItem]:
    """핵심감사사항 원문을 개별 항목으로 나누고 각 항목에 `item_kind`를 붙인다.

    마커 표기가 보고서마다 달라 (N) -> N. -> 원문자 순으로 시도하고,
    어느 것도 2개 이상 없으면 전체를 단일 항목으로 반환한다.
    같은 입력은 항상 같은 결과를 낸다.
    """
    if not kam_raw or not kam_raw.strip():
        return []

    text = _collapse(kam_raw)
    if len(text) < _MIN_BODY_LEN:
        return []

    strategies: tuple[tuple[re.Pattern[str], Callable[[str], int]], ...] = (
        (_PAREN_RE, int),
        (_DOT_RE, int),
        (_CIRCLED_RE, lambda char: _CIRCLED.index(char) + 1),
    )
    for regex, to_no in strategies:
        items = _split_by(regex, text, to_no)
        if items:
            return items

    single = _LEADING_MARKER_RE.sub("", _PREAMBLE_RE.sub("", text, count=1)).strip()
    if len(single) < _MIN_BODY_LEN:
        return []
    title = _extract_title(single)
    return [KamItem(item_no=1, title=title, body=single, kind=classify_item_kind(title))]


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


def _strip_all_whitespace(text: str) -> str:
    return "".join(text.split())


def classify_item_kind(title: str) -> ItemKind:
    """항목 제목으로 `item_kind`를 판정한다.

    아래 순서대로 검사한다(공백을 모두 제거한 제목 기준):
    1. 계속기업 관련 불확실성 단락 도입 문구 포함 -> GOING_CONCERN_NOTICE
    2. 한정의견 근거 단락 도입 문구 포함 -> GOING_CONCERN_NOTICE
    3. 핵심감사사항 선정 선언으로 시작 -> PREAMBLE
    4. 커뮤니케이션 선언으로 시작하거나 그 표현을 포함 -> PREAMBLE
    5. 그 외 -> KAM
    """
    normalized = _strip_all_whitespace(title)
    if _GOING_CONCERN_UNCERTAINTY_MARK in normalized:
        return ItemKind.GOING_CONCERN_NOTICE
    if _QUALIFIED_OPINION_BASIS_MARK in normalized:
        return ItemKind.GOING_CONCERN_NOTICE
    if normalized.startswith(_KAM_DECLARATION_PREFIXES):
        return ItemKind.PREAMBLE
    if normalized.startswith(_COMMUNICATION_PREFIX) or _COMMUNICATION_MARK in normalized:
        return ItemKind.PREAMBLE
    return ItemKind.KAM
