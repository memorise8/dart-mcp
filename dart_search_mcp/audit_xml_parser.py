"""DART 감사보고서 원문 XML(``dart_collected/docs/<rcept>/<rcept>_<acode>.xml``)에서
감사 사실(fact)을 뽑아내는 순수·결정론 파서.

이 **모듈 body**는 순수하다: ``re``/``dataclasses``만 import하고, 함수 입력은
항상 ``xml_bytes``(원문 바이트)와 ``meta``(manifest.json의 ``records[]`` 항목
하나) 뿐이며, 같은 입력에는 항상 같은 출력을 낸다(``datetime.now()``/``random``/
네트워크 등 비결정·부수효과 요소 없음). 다만 이는 **모듈 body**에 한정된 사실이다
— ``dart_search_mcp`` **패키지**를 통해 이 모듈을 import하면(예: ``import
dart_search_mcp.audit_xml_parser``), 패키지 ``__init__.py``가 top-level에서
``from dart_search_mcp.app import mcp``를 실행하고, 그 경로가 전이적으로
``config.py``의 import-time ``load_dotenv()``(디스크 ``.env`` 읽기)를 유발한다.
즉 "이 모듈을 import하면 파일 I/O가 전혀 없다"는 단정은 패키지 경유 import에는
성립하지 않는다 — 파서 함수 자체의 순수성·결정론과는 무관한, 패키지 구조에서
비롯된 부수효과다.

## 랜드마크 기반 파싱과 실측 근거

실제 DART 감사보고서 XML 48k건 중 일부(`dart_collected/docs/`)를 직접 관찰한 결과,
브리프가 가정한 "감사의견" 고정 헤더는 실제로는 **의견 유형별로 헤더 문구 자체가
달라진다**:

- 적정의견: ``감사의견`` / ``감사의견근거``
- 한정의견: ``한정의견`` / ``한정의견근거``
- 부적정의견: ``부적정의견`` / ``부적정의견근거``
- 의견거절: ``의견거절`` / ``의견거절근거``

또한 구형식(2018년 이전 회계연도 재감사·정정 보고서, "부속명세서" 감사의견 등)은
"감사의견" 헤더 자체가 없이 ``본 감사인의 의견으로는 ...`` 문장만 존재하는 경우도
실제로 발견되었다(예: 부속명세서 감사의견). 이 때문에 의견 문단 추출은 헤더
탐색을 우선 시도하되, 실패하거나 판정 근거 문구가 없으면 전체 텍스트에서 의견
문장을 직접 정규식으로 찾는 방식으로 폴백한다(``_extract_opinion_paragraph``).

부정어 표현도 실측 기준 ``표시하고 있지 않습니다`` 외에 ``표시하고 있지
아니합니다`` 변형이 실제 부적정의견 보고서에서 발견되어 함께 처리한다.

## 감사의견 축 분리 (af-v2)

한 공시(= ``audit_facts.jsonl`` 한 줄)에는 감사보고서가 **여럿** 실릴 수 있다.
특히 사업보고서/감사보고서 원문에는 재무제표 감사보고서와 **내부회계관리제도
감사(검토)보고서**가 나란히 들어 있고, 두 보고서의 의견은 서로 다를 수 있다
(실측: 동성제약 20260407002635 - 재무제표 적정, 내부회계 부적정). af-v1은 문서
전체를 한 덩어리로 보고 의견을 찾았기 때문에 내부회계 의견 문단이 재무제표
의견으로 새어 들어갔다.

af-v2는 평문을 **구역(section)**으로 먼저 나눈다:

- 재무제표 감사보고서 구역: ``독립된 감사인의 감사보고서`` 랜드마크부터 다음
  감사보고서 랜드마크 또는 내부회계관리제도 랜드마크 직전까지
  (``carve_fs_sections``). ``audit_opinion``/``opinion_snippet``은 **이 구역
  안에서만** 뽑는다.
- 내부회계관리제도 구역: ``내부회계관리제도 (감사|검토)(의견|보고서)`` 계열
  랜드마크부터 다음 랜드마크 직전까지(``carve_icfr_sections``). 여기서 뽑은
  의견은 ``icfr_opinion``/``icfr_opinion_snippet``이라는 **별도 열**에 담는다.

원문에는 표지·목차·정정신고 대비표가 같은 랜드마크를 여러 번 반복하므로
(정정 사업보고서는 「독립된 감사인의 감사보고서 강조사항」 같은 항목 줄만 4~5개)
구역이 여러 개 잡히는 것이 정상이다. 그래서 "첫 랜드마크"가 아니라 **판정 문구
(``_CLASSIFY_MARKERS``)를 실제로 담은 첫 구역**을 의견으로 채택하고, 그런 구역
개수를 ``audit_report_count``에 적는다.

``opinion_status``/``opinion_source``는 그 값이 어떻게 나왔는지를 밝힌다 -
헤더 기반으로 분류에 성공하면 ``confirmed``/``header``, 문장 정규식 폴백은
값을 만들되 **항상** ``unconfirmed``/``sentence_fallback``이다(폴백은 확정값을
만들지 않는다). 값 자체(``audit_opinion``)는 v1과 같은 규칙으로 계산한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 파서 산출 스키마/규칙 버전. 의견 축을 분리한 이 버전부터 "af-v2"다
# (af-v1 = 문서 전체를 한 덩어리로 보고 의견을 뽑던 최초 버전).
PARSER_VERSION = "af-v2"

# ---------------------------------------------------------------------------
# 결과 타입
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ParsedAuditReport:
    """감사보고서 XML 한 건을 파싱한 구조화 사실(fact) 레코드.

    필드별 실패는 그 필드만 빈 문자열/None으로 남기고 예외를 던지지 않는다.
    어떤 필드가 "성공적으로" 채워졌는지는 ``parse_flags``로 확인한다."""

    rcept_no: str
    corp_code: str
    corp_name: str
    corp_cls: str
    stock_code: str
    report_name: str
    rcept_dt: str
    category: str

    fiscal_year: int | None
    settlement_month: int | None

    auditor: str
    audit_opinion: str
    opinion_snippet: str

    going_concern: bool
    going_concern_snippet: str

    kam_present: bool
    kam_raw: str
    emphasis_raw: str

    # Phase 2(태깅)가 채운다 - 이 태스크에서는 항상 빈 tuple.
    kam_tags: tuple[str, ...]

    parse_flags: frozenset[str]

    source_url: str
    doc_path: str

    # --- af-v2: 감사의견 축 분리 (기본값이 있으므로 v1 호출부는 그대로 동작한다) ---

    # audit_opinion이 어떻게 나왔는지: 헤더 기반 분류가 성공했으면 "confirmed",
    # 폴백이거나 분류가 unknown이면 "unconfirmed".
    opinion_status: str = "unconfirmed"
    # 그 값을 만든 경로: "header" | "sentence_fallback" | ""(의견 문단 자체가 없음).
    opinion_source: str = ""
    # 내부회계관리제도 감사·검토 의견: "적정" | "부적정" | "unknown"
    # (내부회계관리제도 구역 자체가 없으면 "").
    icfr_opinion: str = ""
    icfr_opinion_snippet: str = ""
    # 판정 문구를 담은 재무제표 감사보고서 구역 개수(별도·연결, 원본·재감사 등).
    audit_report_count: int = 1
    parser_version: str = PARSER_VERSION


# ---------------------------------------------------------------------------
# 텍스트 정규화
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")


def _normalize_text(value: str) -> str:
    """개행/탭/중복 공백을 단일 공백으로 정리하고 앞뒤 공백을 제거한다.

    `dart_search_mcp.tools.reports._normalize_text`와 동일한 로직의 인라인
    복제본이다. 이 모듈은 순수·결정론 파서이므로 `tools.reports`(및 그것이
    전이 import하는 `config.py`의 import-time `load_dotenv()` 파일접근)에
    의존하지 않는다."""

    return re.sub(r"\s+", " ", value).strip()


def _strip_tags_to_text(xml_bytes: bytes) -> str:
    """XML 바이트 -> 태그 제거 -> 엔티티 제거 -> 공백 정규화된 평문.

    실제 XML 파서를 쓰지 않는다(브리프 지시: 순수 정규식 기반). 태그가
    불균형하거나 잘려 있어도(발췌 픽스처 등) 예외를 던지지 않고 최선을 다해
    평문을 만든다."""

    text = xml_bytes.decode("utf-8", errors="replace")
    text = _TAG_RE.sub(" ", text)
    text = _ENTITY_RE.sub(" ", text)
    return _normalize_text(text)


# ---------------------------------------------------------------------------
# 랜드마크 상수
# ---------------------------------------------------------------------------

# 의견 유형별 헤더 후보. 실측 결과 헤더 문구 자체가 의견 유형에 따라 다르다.
_OPINION_HEADERS: tuple[str, ...] = ("감사의견", "한정의견", "부적정의견", "의견거절")

# 문단 경계로 쓰이는 섹션 랜드마크(의견 헤더의 "다음 랜드마크"를 찾을 때,
# 그리고 강조사항/핵심감사사항 구간을 잘라낼 때 공통으로 사용).
#
# "계속기업 관련 중요한/중대한 불확실성"은 여기 포함하지 않는다 - 실측 결과
# 핵심감사사항 섹션 본문이 앞서 나온 계속기업 불확실성 단락을 문장 중간에서
# 다시 언급하는 경우가 있어("우리는 계속기업 관련 중요한 불확실성 단락에
# 기술된 사항에 추가하여, ..."), 이를 경계로 쓰면 핵심감사사항 본문이
# 문장 중간에서 잘려나간다. 계속기업 판정 자체는 `detect_going_concern`이
# 독립적으로 처리하므로 문제되지 않는다.
_SECTION_LANDMARKS: tuple[str, ...] = (
    "감사의견근거",
    "한정의견근거",
    "부적정의견근거",
    "의견거절근거",
    "강조사항",
    "핵심감사사항",
    "기타사항",
    "재무제표에 대한 경영진과 지배기구의 책임",
    "재무제표에 대한 경영진의 책임과 지배기구의 책임",
    "재무제표감사에 대한 감사인의 책임",
)

# 감사의견 분류에 쓰이는 판정 문구가 opinion_para 안에 하나라도 있어야
# "헤더 기반 추출 성공"으로 간주한다. 없으면 전체 텍스트 폴백 탐색으로 넘어간다.
_CLASSIFY_MARKERS: tuple[str, ...] = (
    "표시하고 있지 않습니다",
    "표시하고 있지 아니합니다",
    "의견을 표명하지 않습니다",
    "의견거절",
    "제외하고는",
    "공정하게",
    "적정하게",
)

# 헤더 뒤에 바로 붙어 있으면 안 되는 문자(있으면 "감사의견근거"/"감사의견을"처럼
# 같은 단어의 일부이거나 다른 랜드마크이므로 헤더로 인정하지 않는다).
_HEADER_BOUNDARY_OK = (" ", "")


def _find_landmark(text: str, landmark: str, start: int = 0) -> int:
    """`landmark`가 단어 경계(뒤에 공백 또는 문자열 끝)로 나타나는 첫 위치.

    "감사의견"이 "감사의견근거"/"감사의견을"의 접두어로만 나타나는 경우는
    제외한다(정규화된 평문에서 태그 자리는 항상 공백 하나로 치환되므로,
    실제 헤더 뒤에는 거의 항상 공백이 온다)."""

    pos = start
    while True:
        idx = text.find(landmark, pos)
        if idx == -1:
            return -1
        after = text[idx + len(landmark) : idx + len(landmark) + 1]
        if after in _HEADER_BOUNDARY_OK:
            return idx
        pos = idx + len(landmark)


def _next_landmark_pos(text: str, after: int, exclude: str) -> int:
    """`after` 위치 이후 가장 먼저 나오는 섹션 랜드마크 위치(없으면 len(text))."""

    end = len(text)
    for landmark in _SECTION_LANDMARKS:
        if landmark == exclude:
            continue
        idx = _find_landmark(text, landmark, after)
        if idx != -1 and idx < end:
            end = idx
    return end


def _section_text(text: str, header: str, *, max_len: int = 6000) -> str:
    """`header` 랜드마크부터 다음 랜드마크 직전까지의 원문(앞뒤 공백 제거).

    헤더가 없으면 빈 문자열."""

    idx = _find_landmark(text, header)
    if idx == -1:
        return ""
    start = idx + len(header)
    end = _next_landmark_pos(text, start, header)
    end = min(end, start + max_len)
    return text[start:end].strip()


# ---------------------------------------------------------------------------
# 구역(section) 나누기 - 재무제표 감사보고서 vs 내부회계관리제도 (af-v2)
# ---------------------------------------------------------------------------

# 재무제표 감사보고서가 시작하는 랜드마크. 실측 300건 표본에서 292건이
# "독립된 감사인의 감사보고서"였고 "독립된감사인의 감사보고서"(공백 없음)가
# 3건 있어 공백을 선택적으로 허용한다. "독립된 감사인의 내부회계관리제도
# 감사보고서"는 사이에 다른 단어가 있어 이 패턴에 걸리지 않는다 - 두 축을
# 가르는 핵심이다.
_FS_REPORT_LANDMARK_RE = re.compile(r"독립된\s*감사인의\s*(?:연결)?감사보고서")

# 내부회계관리제도 감사(검토)보고서/의견이 시작하는 랜드마크. 실측 표본에서
# 관찰된 표기: "내부회계관리제도 감사보고서", "내부회계관리제도 검토보고서",
# "내부회계관리제도검토보고서", "내부회계관리제도 감사 또는 검토의견",
# "내부회계관리제도에 대한 (감사|검토|부적정)의견", "내부회계관리제도에 대한
# 의견", "내부회계관리제도 (감사|검토|부적정)의견".
#
# "내부회계관리제도 운영실태보고서"(회사가 쓰는 보고서)는 일부러 제외한다 -
# 감사인의 의견 구역이 아니다. "내부회계관리제도 설계 및 운영 개념체계",
# "내부회계관리제도를 감사하였으며", "내부회계관리제도감사에 대한 감사인의
# 책임"처럼 재무제표 의견 문단·책임 문단에 흔히 나오는 표현도 걸리지 않는다
# (제도 뒤에 곧바로 의견/보고서 표기가 와야 한다).
_ICFR_LANDMARK_RE = re.compile(
    r"(?:독립된\s*감사인의\s*)?내부회계관리제도\s*(?:에\s*대한\s*)?"
    r"(?:감사\s*(?:또는|및)\s*검토\s*)?"
    r"(?:감사|검토|적정|부적정|한정)?\s*(?:의견|감사보고서|검토보고서)"
)


def _fs_landmark_positions(text: str) -> list[int]:
    """재무제표 감사보고서 랜드마크 위치들(오름차순).

    ``_find_landmark``와 같은 단어 경계 규칙을 쓴다 - 뒤에 공백이나 문자열 끝이
    와야 한다. "...감사보고서의 강조사항에서 언급한 바와 같이"처럼 조사가 붙은
    본문 내 상호참조를 구역 시작으로 오인하지 않기 위해서다."""

    return [
        m.start()
        for m in _FS_REPORT_LANDMARK_RE.finditer(text)
        if text[m.end() : m.end() + 1] in _HEADER_BOUNDARY_OK
    ]


def _icfr_landmark_positions(text: str) -> list[int]:
    """내부회계관리제도 구역 랜드마크 위치들(오름차순).

    이쪽은 단어 경계를 요구하지 않는다 - "내부회계관리제도 감사보고서는",
    "내부회계관리제도 감사의견근거"처럼 조사/접미어가 붙은 표기도 재무제표
    구역의 **끝**으로 인정해야 내부회계 문단이 재무제표 의견으로 새지 않는다."""

    return [m.start() for m in _ICFR_LANDMARK_RE.finditer(text)]


def _first_bound_after(bounds: list[int], pos: int, default: int) -> int:
    for bound in bounds:
        if bound > pos:
            return bound
    return default


def _section_spans(text: str, *, starts: list[int], others: list[int]) -> list[tuple[int, int]]:
    """`starts`의 각 랜드마크에서 시작해 다음 랜드마크(양쪽 종류 모두) 직전까지."""

    bounds = sorted(set(starts) | set(others))
    return [(start, _first_bound_after(bounds, start, len(text))) for start in starts]


def carve_fs_sections(text: str) -> list[str]:
    """재무제표 감사보고서 구역들을 원문 순서대로 잘라낸다.

    각 구역은 재무제표 감사보고서 랜드마크에서 시작해 그다음 감사보고서
    랜드마크 또는 내부회계관리제도 랜드마크 직전에서 끝난다. 랜드마크가 하나도
    없으면 빈 리스트다 - 그 경우 호출자(``extract_fs_opinion``)가 구역 대신
    ``_fs_text_excluding_icfr``(문서 전체에서 내부회계 구역만 도려낸 텍스트)로
    되돌아간다."""

    fs_starts = _fs_landmark_positions(text)
    if not fs_starts:
        return []
    spans = _section_spans(text, starts=fs_starts, others=_icfr_landmark_positions(text))
    return [text[a:b] for a, b in spans]


def carve_icfr_sections(text: str) -> list[str]:
    """내부회계관리제도 구역들을 원문 순서대로 잘라낸다(없으면 빈 리스트)."""

    icfr_starts = _icfr_landmark_positions(text)
    if not icfr_starts:
        return []
    spans = _section_spans(text, starts=icfr_starts, others=_fs_landmark_positions(text))
    return [text[a:b] for a, b in spans]


def _fs_text_excluding_icfr(text: str) -> str:
    """문서 전체에서 **판정 가능한** 내부회계관리제도 구역만 도려낸 텍스트.

    랜드마크 기반 구역 나누기가 실패하는 실측 형태가 있다 - 표제가 「외부감사인의
    감사보고서」인 보고서(랜드마크 자체가 없다), 본문 머리말이 「독립된 감사인의
    감사보고서 - 계속」뿐이라 정작 의견 문단이 첫 랜드마크보다 **앞**에 있는
    보고서 등. 이때도 af-v1처럼 문서 전체를 보되, 내부회계 의견이 실제로 실린
    구역만은 도려내 의견이 새지 않게 한다. 목차의 「내부회계관리제도 검토의견
    ....... 88」 같은 줄은 판정 문구가 없으므로 도려내지 않는다(도려내면 본문이
    통째로 사라진다)."""

    icfr_starts = _icfr_landmark_positions(text)
    if not icfr_starts:
        return text

    kept: list[str] = []
    cursor = 0
    for start, end in _section_spans(text, starts=icfr_starts, others=_fs_landmark_positions(text)):
        if classify_icfr_opinion(text[start:end]) == "unknown":
            continue
        kept.append(text[cursor:start])
        cursor = end
    kept.append(text[cursor:])
    return " ".join(part for part in kept if part)


# ---------------------------------------------------------------------------
# 감사의견 분류
# ---------------------------------------------------------------------------

_OPINION_SENTENCE_FALLBACK_RE = re.compile(
    r"(?:우리(?:의|는)|본\s*감사인(?:의|은))\s*의견[^.]{0,400}\.", re.DOTALL
)


def classify_opinion(opinion_para: str) -> str:
    """감사의견 문단 텍스트를 4분류(+unknown)한다. 순서가 중요하다:

    1. "표시하고 있지 않습니다"/"표시하고 있지 아니합니다" 포함 -> 부적정
    2. "의견을 표명하지 않습니다" 또는 "의견거절" 포함 -> 의견거절
    3. "제외하고는" 그리고 "표시하고 있습니다" 포함 -> 한정
    4. ("공정하게" 또는 "적정하게") 그리고 "표시하고 있습니다" 포함 -> 적정
    5. 그 외 -> unknown

    한정의견 문단은 "제외하고는"과 "표시하고 있습니다"뿐 아니라 "공정하게"도
    함께 포함하는 것이 실제 문구이므로(예: "...제외하고는, ... 공정하게
    표시하고 있습니다"), 반드시 3번을 4번보다 먼저 체크해야 한정의견이
    적정의견으로 오분류되지 않는다."""

    if not opinion_para:
        return "unknown"

    text = opinion_para

    if "표시하고 있지 않습니다" in text or "표시하고 있지 아니합니다" in text:
        return "부적정"

    if "의견을 표명하지 않습니다" in text or "의견거절" in text:
        return "의견거절"

    if "제외하고는" in text and "표시하고 있습니다" in text:
        return "한정"

    if ("공정하게" in text or "적정하게" in text) and "표시하고 있습니다" in text:
        return "적정"

    return "unknown"


def _header_opinion_paragraph(section: str) -> str:
    """구역 하나에서 헤더 기반 의견 문단만 뽑는다(폴백 없음).

    af-v1 ``_extract_opinion_paragraph``의 헤더 절반을 그대로 옮긴 것이고,
    달라진 것은 탐색 범위가 문서 전체가 아니라 **한 구역**이라는 점뿐이다."""

    header_pos = -1
    matched_header = ""
    for header in _OPINION_HEADERS:
        idx = _find_landmark(section, header)
        if idx != -1 and (header_pos == -1 or idx < header_pos):
            header_pos = idx
            matched_header = header

    if header_pos == -1:
        return ""

    start = header_pos
    end = _next_landmark_pos(section, header_pos + len(matched_header), matched_header)
    end = min(end, start + 1200)
    return section[start:end].strip()


def extract_fs_opinion(full_text: str) -> tuple[str, str, str, str, int]:
    """재무제표 감사보고서 구역들에서 감사의견을 뽑는다.

    ``(audit_opinion, opinion_para, opinion_status, opinion_source,
    audit_report_count)``를 돌려준다.

    1. 각 구역에서 헤더 기반 의견 문단을 뽑고, 판정 문구(``_CLASSIFY_MARKERS``)를
       담은 구역만 "실제 감사보고서"로 인정한다 - 표지·목차·정정 대비표가 같은
       랜드마크를 반복하기 때문이다. 그런 구역의 **첫 번째**가 의견이 되고,
       개수가 ``audit_report_count``다.
    2. 그런 구역이 하나도 없으면 ``_fs_text_excluding_icfr``(문서 전체에서 내부회계
       의견 구역만 도려낸 텍스트)에서 af-v1과 같은 순서로 - 헤더 우선, 실패하면
       의견 문장 정규식 - 다시 찾는다. 랜드마크가 문서마다 일정하지 않아
       (「외부감사인의 감사보고서」, 「... - 계속」 머리말 등) 구역 나누기가
       빗나갈 수 있는데, 그때 v1이 찾던 의견까지 잃지는 않게 하는 안전망이다.

    폴백(의견 문장 정규식) 경로는 값은 만들되 확정하지 않는다 - 항상
    ``unconfirmed``다.
    """

    header_paras = [_header_opinion_paragraph(section) for section in carve_fs_sections(full_text)]
    confirmed = [para for para in header_paras if any(m in para for m in _CLASSIFY_MARKERS)]

    if confirmed:
        para = confirmed[0]
        opinion = classify_opinion(para)
        status = "confirmed" if opinion != "unknown" else "unconfirmed"
        return opinion, para, status, "header", len(confirmed)

    fs_text = _fs_text_excluding_icfr(full_text)
    para = _header_opinion_paragraph(fs_text)
    if any(marker in para for marker in _CLASSIFY_MARKERS):
        opinion = classify_opinion(para)
        status = "confirmed" if opinion != "unknown" else "unconfirmed"
        return opinion, para, status, "header", 1

    match = _OPINION_SENTENCE_FALLBACK_RE.search(fs_text)
    if match:
        return classify_opinion(match.group(0)), match.group(0), "unconfirmed", "sentence_fallback", 1

    return classify_opinion(para), para, "unconfirmed", "header" if para else "", 1


# ---------------------------------------------------------------------------
# 내부회계관리제도 의견 분류 (af-v2, 재무제표 의견과 별개 축)
# ---------------------------------------------------------------------------

# 감사의견: "...내부회계관리제도는 ... 효과적으로 설계 및 운영되고 있(지 않)습니다".
# 실측에서 "설계 및 운영" 자리에 "설계·운영"/"설계ㆍ운영" 표기가 함께 쓰인다.
_ICFR_INEFFECTIVE_RE = re.compile(r"효과적으로\s*설계\s*(?:및|ㆍ|·|,)?\s*운영되고\s*있지\s*(?:않|아니)")
_ICFR_EFFECTIVE_RE = re.compile(r"효과적으로\s*설계\s*(?:및|ㆍ|·|,)?\s*운영되고\s*있(?:습니다|음)")

# 부적정의견 문단은 근거로 중요한 취약점을 든다. 감사인의 책임 보일러플레이트
# ("중요한 취약점이 존재하는지에 대한 감사증거를 입수하기 위한 절차")와 섞이지
# 않도록 "식별/존재한다고 판단/영향"이 뒤따르는 표현으로 좁힌다.
_ICFR_WEAKNESS_RE = re.compile(r"중요한\s*취약점(?:이|의|을)?\s*(?:식별|존재한다고|영향)")

# 검토의견(비상장·소규모): "...개념체계에 따라 작성되지 않았다고 판단하게 하는
# 점이 발견되지 아니하였습니다" = 적정.
_ICFR_REVIEW_CLEAN_RE = re.compile(r"작성되지\s*(?:않았|아니하였)다고[^.]{0,160}발견되지\s*(?:않았|아니하였)")


def classify_icfr_opinion(section: str) -> str:
    """내부회계관리제도 구역 텍스트를 적정/부적정(+unknown)으로 분류한다.

    부적정 판정을 먼저 본다 - 정정 대비표처럼 정정 전(적정)·정정 후(부적정)
    문구가 한 구역에 같이 실리는 경우가 실재하고, 그럴 때 최종 의견은
    부적정이기 때문이다."""

    if not section:
        return "unknown"

    if (
        _ICFR_INEFFECTIVE_RE.search(section)
        or "부적정의견" in section
        or _ICFR_WEAKNESS_RE.search(section)
    ):
        return "부적정"

    if _ICFR_EFFECTIVE_RE.search(section) or _ICFR_REVIEW_CLEAN_RE.search(section):
        return "적정"

    return "unknown"


def extract_icfr_opinion(full_text: str) -> tuple[str, str]:
    """``(icfr_opinion, icfr_opinion_snippet)``.

    내부회계관리제도 구역이 아예 없으면 ``("", "")``다 - "구역이 없다"와
    "구역은 있는데 판정을 못 했다(unknown)"를 구분한다."""

    sections = carve_icfr_sections(full_text)
    if not sections:
        return "", ""

    for section in sections:
        opinion = classify_icfr_opinion(section)
        if opinion != "unknown":
            return opinion, section[:400].strip()

    return "unknown", sections[0][:400].strip()


# ---------------------------------------------------------------------------
# 계속기업(going concern) - 오탐 방지
# ---------------------------------------------------------------------------

# 강조/불확실성 섹션 랜드마크(명시적 헤더)로만 true 판정한다. "계속기업전제",
# "계속기업으로서의 존속능력을 평가/초래할 수 있는" 같은 경영진/감사인 책임
# 표준 보일러플레이트 문구는 실측 결과 거의 모든 보고서(계속기업 문제가 전혀
# 없는 보고서 포함)에 등장하므로 오탐의 주범이다 - 절대 트리거로 쓰지 않는다.
_GOING_CONCERN_HEADERS: tuple[str, ...] = (
    "계속기업 관련 중요한 불확실성",
    "계속기업 관련 중대한 불확실성",
)

# 브리프가 예시로 든 "...존속능력에 유의적인 의문을 제기(할 수 있는 중요한
# 불확실성이 존재)" 변형. "제기"(실제로 의문이 제기됨을 서술)로 한정하고,
# 보일러플레이트가 쓰는 "초래할 수 있는"(가정법) 표현과는 구분한다.
_GOING_CONCERN_RAISE_RE = re.compile(
    r"계속기업으로서의\s*존속능력에?\s*(?:대하여\s*)?유의적(?:인|\s)*\s*의문을?\s*제기"
)


def detect_going_concern(full_text: str) -> tuple[bool, str]:
    """계속기업 관련 중요/중대한 불확실성 섹션이 실제로 있는지 판정한다.

    True인 경우에만 근거 문장 일부를 함께 반환한다(없으면 두번째 값은 "")."""

    for header in _GOING_CONCERN_HEADERS:
        idx = _find_landmark(full_text, header)
        if idx != -1:
            start = idx + len(header)
            end = min(len(full_text), start + 400)
            snippet = full_text[start:end].strip()
            return True, snippet

    m = _GOING_CONCERN_RAISE_RE.search(full_text)
    if m:
        start = max(0, m.start() - 80)
        end = min(len(full_text), m.end() + 250)
        return True, full_text[start:end].strip()

    return False, ""


# ---------------------------------------------------------------------------
# 핵심감사사항 / 강조사항
# ---------------------------------------------------------------------------


def extract_kam(text: str) -> str:
    """핵심감사사항 섹션 원문(랜드마크~다음 랜드마크). 없으면 ""."""

    return _section_text(text, "핵심감사사항")


def extract_emphasis(text: str) -> str:
    """강조사항 섹션 원문(랜드마크~다음 랜드마크). 없으면 ""."""

    return _section_text(text, "강조사항")


# ---------------------------------------------------------------------------
# 감사인(auditor) - meta 우선, XML 서명과 대조
# ---------------------------------------------------------------------------

# 실제 XML은 서명란이 표(TABLE) 셀 단위로 한 글자씩 배치되는 경우가 많아,
# 태그 제거 후 평문에서는 "대 주 회 계 법 인"처럼 글자 사이에 공백이 낀다.
# "회계법인"/"감사반" 자체도 글자 사이가 벌어질 수 있으므로 각 글자 사이에
# 선택적 공백을 허용해 매칭한다.
_AUDITOR_SIGNATURE_RE = re.compile(r"(?:[가-힣]\s*){2,20}?(?:회\s*계\s*법\s*인|감\s*사\s*반)")


def _extract_auditor_signature(full_text: str) -> str:
    """문서 말미(보통 마지막 매칭)의 감사인 서명 문자열(공백 제거)."""

    matches = list(_AUDITOR_SIGNATURE_RE.finditer(full_text))
    if not matches:
        return ""
    return re.sub(r"\s+", "", matches[-1].group(0))


# ---------------------------------------------------------------------------
# fiscal_year / settlement_month
# ---------------------------------------------------------------------------

_FY_REPORT_NAME_RE = re.compile(r"\((\d{4})\.(\d{1,2})\)")
_FY_XML_PERIOD_RE = re.compile(r"(\d{4})년\s*(\d{1,2})월\s*\d{1,2}일\s*현재")
_RCEPT_DT_RE = re.compile(r"^\d{8}$")


def derive_fiscal_year(meta: dict, full_text: str) -> tuple[int | None, int | None]:
    """회계연도/결산월을 우선순위대로 폴백하며 결정한다:

    1. ``meta["report_name"]`` 말미 ``(YYYY.MM)`` 패턴
    2. XML 본문의 보고기간 종료일 ``"YYYY년 MM월 DD일 현재"``
    3. ``meta["rcept_dt"]``(YYYYMMDD)
    4. 모두 실패하면 (None, None)
    """

    report_name = str(meta.get("report_name", "") or "")
    m = _FY_REPORT_NAME_RE.search(report_name)
    if m:
        return int(m.group(1)), int(m.group(2))

    m = _FY_XML_PERIOD_RE.search(full_text)
    if m:
        return int(m.group(1)), int(m.group(2))

    rcept_dt = str(meta.get("rcept_dt", "") or "")
    if _RCEPT_DT_RE.match(rcept_dt):
        return int(rcept_dt[:4]), int(rcept_dt[4:6])

    return None, None


# ---------------------------------------------------------------------------
# 메인 진입점
# ---------------------------------------------------------------------------


def parse_audit_xml(xml_bytes: bytes, meta: dict, *, doc_path: str = "") -> ParsedAuditReport:
    """감사보고서 XML 바이트 + manifest meta -> 구조화된 감사 사실(fact).

    순수·결정론 함수다: 네트워크/파일시스템 접근이 전혀 없고
    ``datetime.now()``/``random`` 등을 쓰지 않는다. 필드별 파싱 실패는 예외를
    던지지 않고 해당 필드만 빈 값/None으로 남긴 채 최대한 채운
    ``ParsedAuditReport``를 반환한다.

    태그 제거만 하고 실제 파싱은 ``parse_audit_text``에 위임한다."""

    return parse_audit_text(_strip_tags_to_text(xml_bytes), meta, doc_path=doc_path)


def parse_audit_text(text: str, meta: dict, *, doc_path: str = "") -> ParsedAuditReport:
    """평문(태그 제거·공백 정규화된 감사보고서 본문) + manifest meta -> 감사 사실.

    ``parse_audit_xml``의 본체다. 원문 XML 없이 평문 발췌만으로 파싱 규칙을
    시험할 수 있도록 분리했다(테스트 픽스처가 이 진입점으로 들어온다)."""

    full_text = text

    flags: set[str] = set()

    rcept_no = str(meta.get("rcept_no", "") or "")
    corp_code = str(meta.get("corp_code", "") or "")
    corp_name = str(meta.get("corp_name", "") or "")
    corp_cls = str(meta.get("corp_cls", "") or "")
    stock_code = str(meta.get("stock_code", "") or "")
    report_name = str(meta.get("report_name", "") or "")
    rcept_dt = str(meta.get("rcept_dt", "") or "")
    category = str(meta.get("category", "") or "")
    source_url = str(meta.get("source_url", "") or "")

    # --- auditor: meta["flr_nm"] 우선, XML 서명과 대조 ---
    auditor = str(meta.get("flr_nm", "") or "")
    if auditor:
        flags.add("auditor")
        signature = _extract_auditor_signature(full_text)
        if signature and re.sub(r"\s+", "", auditor) != signature:
            flags.add("auditor_mismatch")

    # --- audit_opinion (재무제표 감사보고서 구역 한정) ---
    (
        audit_opinion,
        opinion_para,
        opinion_status,
        opinion_source,
        audit_report_count,
    ) = extract_fs_opinion(full_text)
    opinion_snippet = opinion_para[:400] if opinion_para else ""
    if audit_opinion != "unknown":
        flags.add("opinion")

    # --- icfr_opinion (내부회계관리제도 구역, 재무제표 의견과 별개 축) ---
    # `parse_flags`에는 새 플래그를 넣지 않는다 - 기존 열의 값을 바꾸지 않고
    # 열만 덧붙이는 것이 이 변경의 계약이다. 내부회계 의견의 유무는
    # `icfr_opinion` 자체가("" / "unknown" / 값) 이미 구분한다.
    icfr_opinion, icfr_opinion_snippet = extract_icfr_opinion(full_text)

    # --- going_concern ---
    going_concern, going_concern_snippet = detect_going_concern(full_text)
    if going_concern:
        flags.add("going_concern")

    # --- KAM / 강조사항 ---
    kam_raw = extract_kam(full_text)
    kam_present = bool(kam_raw)
    if kam_present:
        flags.add("kam")
    emphasis_raw = extract_emphasis(full_text)

    # --- fiscal_year / settlement_month ---
    fiscal_year, settlement_month = derive_fiscal_year(meta, full_text)
    if fiscal_year is not None:
        flags.add("fiscal_year")

    return ParsedAuditReport(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name=corp_name,
        corp_cls=corp_cls,
        stock_code=stock_code,
        report_name=report_name,
        rcept_dt=rcept_dt,
        category=category,
        fiscal_year=fiscal_year,
        settlement_month=settlement_month,
        auditor=auditor,
        audit_opinion=audit_opinion,
        opinion_snippet=opinion_snippet,
        going_concern=going_concern,
        going_concern_snippet=going_concern_snippet,
        kam_present=kam_present,
        kam_raw=kam_raw,
        emphasis_raw=emphasis_raw,
        kam_tags=(),
        parse_flags=frozenset(flags),
        source_url=source_url,
        doc_path=doc_path,
        opinion_status=opinion_status,
        opinion_source=opinion_source,
        icfr_opinion=icfr_opinion,
        icfr_opinion_snippet=icfr_opinion_snippet,
        audit_report_count=audit_report_count,
        parser_version=PARSER_VERSION,
    )
