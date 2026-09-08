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
탐색을 우선 시도하되, 실패하거나 판정 근거 문구가 없으면 의견 문장을 직접
정규식으로 찾는 방식으로 폴백한다(``extract_fs_opinion``).

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

구역 나누기만으로는 부족하다 - 랜드마크 표기가 문서마다 일정하지 않기 때문이다
(실측 1,500건 중 48건은 내부회계관리제도를 언급하면서도 랜드마크에 걸리는 표기가
없었다). 그래서 **문장 가드**를 함께 둔다: 분류기에 넣는 텍스트는
``_strip_icfr_sentences``(내부회계 문장 제거) + ``_fs_verdict_text``(재무제표/
연결재무제표를 가리키면서 판정 문구를 담은 문장만 남김)를 거치고, 의견 문장
정규식 폴백도 내부회계 문장이면 건너뛴다. 구역 나누기가 통하지 않는 문서에서
이 가드가 마지막 방어선이다.

``opinion_status``/``opinion_source``는 그 값이 어떻게 나왔는지를 밝힌다 -
헤더 기반으로 분류에 성공하면 ``confirmed``/``header``, 문장 정규식 폴백은
값을 만들되 **항상** ``unconfirmed``/``sentence_fallback``이다(폴백은 확정값을
만들지 않는다). 값 자체(``audit_opinion``)는 v1과 같은 규칙으로 계산한다.

두 열을 읽을 때 주의할 점 두 가지:

- ``opinion_source == "header"``는 **1단(구역)과 2단(문서 전체 - 내부회계 구역)
  경로를 모두** 가리킨다. 둘 다 헤더 기반이지만 확신도가 같지는 않다 - 1단은
  "재무제표 감사보고서 구역 안에서 찾았다"이고, 2단은 "구역을 못 나눠 문서
  전체에서 찾았다"이다. 열 하나로 두 경로를 더 쪼개지 않은 것은 하류 계약을
  늘리지 않기 위해서다.
- ``audit_report_count``는 **1단에서만 의미가 있다**(판정 문구를 담은 구역
  개수). 2단·폴백 경로는 구역을 세지 못하므로 언제나 1이다.

## 계속기업 기재 위치·시점, 강조/기타사항 상태 (af-v2)

회계사 검수 결과 ``going_concern``(bool) 한 칸으로는 부족했다. 실제 감사보고서는
계속기업 불확실성을 셋 중 하나로 적는다 - ``계속기업 관련 중요한 불확실성``이라는
**별도 구역**(작성사례 5.1), 의견근거 단락 **안**(5.3~5.8), 그 밖의 본문. 게다가
**전기 감사보고서를 가리키는 문장**(10.6.1의 전기 타감사인 문안)은 당기 기재가
아닌데도 「있음」으로 셀 수 있었다. 그래서 ``locate_going_concern``이
``gc_material_uncertainty``(disclosed/not_disclosed/unknown)와
``gc_location``(separate_section/within_basis_for_opinion/other/""/unknown)을
함께 내고, ``going_concern``은 그 축약(``== "disclosed"``)으로 남는다.

``emphasis_status``/``other_matter_status``도 같은 이유로 덧붙였다 -
``emphasis_raw``가 비어 있는 것만으로는 「구역이 없다」와 「본문을 못 얻었다」를
구분할 수 없고, 정정신고 대비표의 자리표시자(「강조사항 (주1)」)를 강조사항으로
집계하던 결함(동성제약 20260407002635)도 상태 판정과 함께 고쳤다
(``_labelled_section``).

## 랜드마크는 표제 자리에서만 (af-v2)

구역의 **끝**을 정할 때는 랜드마크가 표제 자리(``_is_heading_position``)에 있을
때만 인정한다. 핵심감사사항 서문의 표준 문안 「우리는 한정의견근거 단락에 기술된
사항에 추가하여…」(작성사례 6.2) 안의 「한정의견근거」에서 끊으면 핵심감사사항
구역이 「우리는」에서 잘리기 때문이다(실측: 다이나믹디자인 20260323001749,
용마산건설 20260406004560, 동성제약 20260506000528). 구역의 **시작**에는 이
규칙을 걸지 않는다 - 표제가 앞 문장의 마침표 없이 이어지는 실측 형태가 있어
시작까지 좁히면 구역 자체를 잃는다.
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

    # --- af-v2: 계속기업 기재 위치·시점, 강조/기타사항 상태 ---

    # 아래 네 열은 af-v2에서 새로 생겼다. jsonl을 되읽을 때 **빈 문자열**은
    # "af-v2 이전(레거시) 행이라 이 열 자체가 없었다"는 뜻이다
    # (`deserialize_parsed_report` 참조) - "unknown"("본문을 못 얻어 판정 못 함")과
    # 구분된다.

    # 계속기업 관련 중요한 불확실성이 **당기 기재**로 있는가:
    # "disclosed" | "not_disclosed" | "unknown"(본문 자체를 못 얻음) | ""(레거시 행).
    # 기존 열 `going_concern`(bool)은 `gc_material_uncertainty == "disclosed"`다.
    gc_material_uncertainty: str = "unknown"
    # 그 기재가 어디에 있었나: "separate_section"(별도 구역 표제) |
    # "within_basis_for_opinion"(의견근거 단락 안) | "other"(그 밖의 본문) |
    # ""(기재 없음, 그리고 레거시 행) | "unknown"(본문 없음).
    gc_location: str = "unknown"
    # 강조사항/기타사항 구역: "present" | "absent" | "unknown"(본문 없음) |
    # ""(레거시 행).
    # `emphasis_raw`가 비어 있는 것만으로는 "구역이 없다"와 "본문을 못 얻었다"를
    # 구분할 수 없어 상태 열을 따로 둔다.
    emphasis_status: str = "unknown"
    other_matter_status: str = "unknown"


# ---------------------------------------------------------------------------
# 텍스트 정규화
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_ENTITY_RE = re.compile(r"&[a-zA-Z#0-9]+;")


# 어미 한가운데에 낀 공백을 닫는 규칙(af-v2). 원문 XML은 줄 폭에 맞춰 낱말
# 중간에서 줄을 바꾸고, 공백 정규화는 그 줄바꿈을 공백 하나로 남긴다. 그래서
# 「…공정하게 표시하고 있습니 다.」처럼 어미가 갈라진 평문이 나오고, 판정
# 문구(`_CLASSIFY_MARKERS`)를 문자열로 찾는 분류기가 그 문장을 놓친다.
#
# 실측(읽기 전용 `audit_facts.jsonl`): `audit_opinion=unknown` 721건 중 323건의
# 원문에 「…니 다.」가 있었고, 604건의 등장 중 604건이 「습/합/됩…」+「니」 뒤였다.
# 이 규칙 하나로 721건 중 105건이 「적정」(confirmed)으로 확정된다.
#
# 「…니」로 끝나는 낱말 뒤에 「다음/다만/다른」이 오는 정상 문장을 붙여 버리지
# 않도록, 뒤따르는 「다」가 문장을 끝낼 때(마침표·쉼표·닫는 괄호·문서 끝)만
# 공백을 지운다. 숫자·영문 사이 공백은 건드리지 않는다.
_SPLIT_ENDING_RE = re.compile(r"(?<=[가-힣]니)\s+(?=다(?:[.,)\]]|$))")


def _join_split_endings(value: str) -> str:
    """「…있습니 다.」처럼 어미 안에 낀 공백을 닫는다(그 외 공백은 그대로)."""

    return _SPLIT_ENDING_RE.sub("", value)


def _normalize_text(value: str) -> str:
    """개행/탭/중복 공백을 단일 공백으로 정리하고, 갈라진 어미를 닫는다.

    앞부분(공백 정리)은 `dart_search_mcp.tools.reports._normalize_text`와 같은
    로직의 인라인 복제본이다. 이 모듈은 순수·결정론 파서이므로 `tools.reports`
    (및 그것이 전이 import하는 `config.py`의 import-time `load_dotenv()`
    파일접근)에 의존하지 않는다. 뒷부분(`_join_split_endings`)은 이 파서에만
    있는 규칙이다 - 감사의견 판정이 문자열 일치에 기대기 때문이다.

    멱등이다: 이미 정규화된 평문을 다시 넣어도 결과가 같다(`parse_audit_text`가
    평문 픽스처에도 같은 규칙을 적용할 수 있는 근거)."""

    return _join_split_endings(re.sub(r"\s+", " ", value).strip())


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
    # 연결감사보고서는 같은 표제에 "연결"이 붙는다. 구역 끝 랜드마크를 표제
    # 자리에서만 인정하게 되면서(`_next_landmark_pos`) 접두어가 붙은 표기를
    # 따로 실어야 한다 - "연결재무제표에 대한..."의 "재무제표에 대한..."은 앞
    # 글자가 한글("결")이라 문장 중간으로 판정되기 때문이다. 실측 400건 표본에서
    # "연결재무제표감사에 대한 감사인의 책임" 78회, "연결재무제표에 대한 경영진..."
    # 43회 등장한다.
    "연결재무제표에 대한 경영진과 지배기구의 책임",
    "연결재무제표에 대한 경영진의 책임과 지배기구의 책임",
    "연결재무제표감사에 대한 감사인의 책임",
)

# 계속기업 불확실성을 **별도 구역**으로 세울 때 쓰는 표제(계속기업 판정의 상수이자
# 구역 경계이기도 해서 랜드마크 곁에 둔다). "계속기업전제", "계속기업으로서의
# 존속능력을 평가/초래할 수 있는" 같은 경영진/감사인 책임 표준 보일러플레이트
# 문구는 실측 결과 거의 모든 보고서(계속기업 문제가 전혀 없는 보고서 포함)에
# 등장하므로 오탐의 주범이다 - 절대 트리거로 쓰지 않는다.
_GOING_CONCERN_HEADERS: tuple[str, ...] = (
    "계속기업 관련 중요한 불확실성",
    "계속기업 관련 중대한 불확실성",
)

# 이 중 **표제 자리에서만** 경계로 인정할 랜드마크(`_is_heading_position`).
#
# 문장 한가운데에 **낱말 그대로**(뒤에 조사 없이 공백이 오는 꼴) 나타나 상호참조로
# 읽히는 것들만 넣는다 - 「우리는 한정의견근거 단락에 기술된 사항에 추가하여」,
# 「우리는 계속기업 관련 중요한 불확실성 단락에 기술된 사항에 추가하여」(한국공인
# 회계사회 「감사보고서 작성사례」 5.1·6.2)가 그 형태다. 조사가 붙은 상호참조
# (「강조사항에서 언급한」, 「핵심감사사항으로 결정하였습니다」)는 랜드마크 뒤에 공백이
# 와야 한다는 기존 규칙(`_HEADER_BOUNDARY_OK`)이 이미 걸러 낸다.
#
# 나머지는 **무조건** 경계다. 표제 규칙을 넓게 걸면 부작용이 훨씬 크기 때문이다 -
# 구역 본문은 마침표 없이 글머리표나 명사로 끝나는 일이 흔한데(「…적정성 검토」),
# 그러면 바로 뒤의 표제가 앞 글자(한글) 때문에 거부되어 구역이 다음 구역들을 삼킨다.
# 실측(kam_present 400건, 같은 평문):
#
# | 표제 규칙 적용 범위 | 책임문구 오염 | 6,000자 상한 | 기타/강조 구역 삼킴 | 평균 길이 |
# |---|---|---|---|---|
# | cd3bdd0(규칙 없음) | 1 | 0 | 0 | 755 |
# | 모든 랜드마크 | 246 | 15 | - | 1,182 |
# | + 긴 표제 제외 | 1 | 1 | 86 | 807 |
# | 여기(+강조사항·기타사항 제외) | 1 | 0 | 0 | 756 |
#
# 「우리는」에서 잘리던 구역 절단은 네 변이 중 마지막에서도 0건이다.
_HEADING_ONLY_LANDMARKS: frozenset[str] = frozenset(
    {
        "감사의견근거",
        "한정의견근거",
        "부적정의견근거",
        "의견거절근거",
        "핵심감사사항",
    }
    | set(_GOING_CONCERN_HEADERS)  # "…불확실성 단락에 기술된 사항에 추가하여"
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


# 랜드마크 **앞**에 오면 그 자리가 표제가 아니라 문장 한가운데임을 뜻하는 문자.
# 한글/영문/숫자/쉼표 뒤에 붙은 랜드마크는 표제가 아니라 상호참조다 -
# "우리는 한정의견근거 단락에 기술된 사항에 추가하여"(핵심감사사항 서문 표준
# 문안, 한국공인회계사회 「감사보고서 작성사례」 6.2)가 대표적이다. 표제는 앞
# 문장이 끝난 자리(".") 또는 번호매김(")", "]", "·" 등) 뒤에 온다.
_MID_SENTENCE_PREV_RE = re.compile(r"[0-9A-Za-z가-힣,、]")


def _prev_nonspace_index(text: str, idx: int) -> int:
    """`idx` 바로 앞의 공백 아닌 문자의 위치(없으면 -1)."""

    i = idx - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    return i


def _prev_nonspace(text: str, idx: int) -> str:
    """`idx` 바로 앞의 공백 아닌 문자(없으면 "")."""

    i = _prev_nonspace_index(text, idx)
    return text[i] if i >= 0 else ""


def _is_heading_position(text: str, idx: int) -> bool:
    """`idx`가 **표제 자리**인가 - 문서 첫머리이거나 앞 문장이 끝난 자리인가.

    정규화된 평문에는 줄바꿈이 남아 있지 않으므로("줄 시작"을 볼 수 없다) 앞
    문자로 판정한다. 앞 문자가 한글·영문·숫자·쉼표면 문장 한가운데다."""

    prev = _prev_nonspace(text, idx)
    return not prev or not _MID_SENTENCE_PREV_RE.match(prev)


# 상호참조 문장의 꼬리·머리. 랜드마크 자체는 표제와 똑같이 생겼는데(뒤에 공백이
# 오는 낱말 그대로) 문장 한가운데의 참조인 형태를 잡는다:
#
#   "우리는 **강조사항** 단락에 기술된 사항에 추가하여, 아래에 기술된 사항을 …
#    커뮤니케이션할 핵심감사사항으로 결정하였습니다."
#
# 한국공인회계사회 「감사보고서 작성사례」 5.1·6.2의 핵심감사사항 서문 표준 문안이고
# (자료에는 계속기업·한정의견근거 자리에 들어간 형태로 실려 있다), 그 자리에는 강조사항·
# 기타사항도 똑같이 들어간다. 앞 글자가 한글이라 `_is_heading_position`으로도 걸리지만,
# 강조사항·기타사항은 표제가 마침표 없이 끝난 항목 뒤에 오는 일이 잦아 표제 규칙을 걸 수
# 없다(Fix round 1 참조). 그래서 이 두 랜드마크는 **상호참조 형태만** 골라 건너뛴다.
_CROSS_REFERENCE_TAIL_RE = re.compile(r"\s*(?:단락|문단|에\s*기술된|에서\s*기술|에\s*기재된)")
_CROSS_REFERENCE_HEAD_TERMS: tuple[str, ...] = ("우리는", "상기", "위의", "해당")

# 상호참조 검사를 적용할 랜드마크(무조건 경계로 쓰는 짧은 표제 둘). 긴 표제에는 걸지
# 않는다 - 「이 감사보고서의 재무제표감사에 대한 감사인의 책임 **단락**에 기술되어
# 있습니다」가 감사의견근거 문단의 표준 문안이라, 거기까지 상호참조로 보면 의견근거
# 구역이 그 문장을 넘어 늘어나 의견 문단 경계가 달라진다(실측 확인, Fix round 2 참조).
_CROSS_REFERENCE_CHECKED_LANDMARKS: frozenset[str] = frozenset({"강조사항", "기타사항"})


def _is_cross_reference(text: str, idx: int, landmark: str) -> bool:
    """`idx`의 랜드마크가 표제가 아니라 **다른 단락을 가리키는 참조**인가."""

    if _CROSS_REFERENCE_TAIL_RE.match(text, idx + len(landmark)):
        return True
    head = text[max(0, idx - 12) : idx].rstrip()
    return head.endswith(_CROSS_REFERENCE_HEAD_TERMS)


def _find_landmark(
    text: str,
    landmark: str,
    start: int = 0,
    *,
    heading_only: bool = False,
    skip_cross_reference: bool = False,
) -> int:
    """`landmark`가 단어 경계(뒤에 공백 또는 문자열 끝)로 나타나는 첫 위치.

    "감사의견"이 "감사의견근거"/"감사의견을"의 접두어로만 나타나는 경우는
    제외한다(정규화된 평문에서 태그 자리는 항상 공백 하나로 치환되므로,
    실제 헤더 뒤에는 거의 항상 공백이 온다).

    ``heading_only=True``면 표제 자리(``_is_heading_position``)에 있는 등장만
    인정하고 문장 한가운데의 상호참조는 건너뛴다. ``skip_cross_reference=True``면
    표제 자리 여부와 무관하게 **상호참조 형태**(``_is_cross_reference``)만
    건너뛴다."""

    pos = start
    while True:
        idx = text.find(landmark, pos)
        if idx == -1:
            return -1
        after = text[idx + len(landmark) : idx + len(landmark) + 1]
        if (
            after in _HEADER_BOUNDARY_OK
            and (not heading_only or _is_heading_position(text, idx))
            and (not skip_cross_reference or not _is_cross_reference(text, idx, landmark))
        ):
            return idx
        pos = idx + len(landmark)


def _next_landmark_pos(
    text: str, after: int, exclude: str, *, landmarks: tuple[str, ...] = _SECTION_LANDMARKS
) -> int:
    """`after` 위치 이후 가장 먼저 나오는 섹션 랜드마크 위치(없으면 ``len(text)``).

    랜드마크마다 인정 조건이 다르다:

    - ``_HEADING_ONLY_LANDMARKS``(의견근거 4종·핵심감사사항·계속기업 표제): **표제
      자리**에서만 경계다 - 문장 안에 나오는 같은 문자열("…한정의견근거 단락에
      기술된 사항에 추가하여…")에서 끊으면 구역 본문이 통째로 날아간다(실측:
      20260323001749 다이나믹디자인·20260406004560 용마산건설의 핵심감사사항이
      「우리는」에서 잘렸다).
    - ``_CROSS_REFERENCE_CHECKED_LANDMARKS``(강조사항·기타사항): 무조건 경계이되
      **상호참조 형태만** 건너뛴다. 이 둘은 마침표 없이 끝난 항목 바로 뒤에 표제로
      오는 일이 잦아 표제 규칙을 걸 수 없고(그러면 구역이 다음 구역을 삼킨다),
      그렇다고 「우리는 강조사항 단락에 기술된 사항에 추가하여」 같은 표준 문안에서
      끊어서도 안 된다.
    - 나머지 긴 표제(…경영진과 지배기구의 책임, …감사인의 책임): 문장 안에 그
      형태로 나타나는 일이 없어 무조건 경계다."""

    end = len(text)
    for landmark in landmarks:
        if landmark == exclude:
            continue
        idx = _find_landmark(
            text,
            landmark,
            after,
            heading_only=landmark in _HEADING_ONLY_LANDMARKS,
            skip_cross_reference=landmark in _CROSS_REFERENCE_CHECKED_LANDMARKS,
        )
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
# 의견", "내부회계관리제도 (감사|검토|부적정)의견", "내부회계관리제도에 대한
# 감사인의 (감사)보고서".
#
# "내부회계관리제도 운영실태보고서"(회사가 쓰는 보고서)는 일부러 제외한다 -
# 감사인의 의견 구역이 아니다. "내부회계관리제도 설계 및 운영 개념체계",
# "내부회계관리제도를 감사하였으며", "내부회계관리제도감사에 대한 감사인의
# 책임"처럼 재무제표 의견 문단·책임 문단에 흔히 나오는 표현도 걸리지 않는다
# (제도 뒤에 곧바로 의견/보고서 표기가 와야 한다).
_ICFR_LANDMARK_RE = re.compile(
    r"(?:독립된\s*감사인의\s*)?내부회계관리제도\s*(?:에\s*대한\s*)?"
    r"(?:감사\s*(?:또는|및)\s*검토\s*)?"
    r"(?:감사인의\s*(?:감사|검토)?보고서|(?:감사|검토|적정|부적정|한정)?\s*(?:의견|감사보고서|검토보고서))"
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

# --- 문장 가드 (af-v2): 분류기에 넣는 텍스트를 재무제표 의견 문장으로 한정한다 ---
#
# 구역 나누기는 랜드마크에 기대는데 랜드마크 표기가 문서마다 일정하지 않다.
# 실측 1,500건 중 48건(3.2%)은 내부회계관리제도를 언급하면서도 랜드마크에 걸리는
# 표기가 없었고, 2단 경로(`_fs_text_excluding_icfr`)는 애초에 문서 전체를 본다.
# 그런 문서에서 마지막 방어선이 이 가드다 - **내부회계 판정 문장은 어떤 경로로도
# 재무제표 의견 분류기에 들어가지 않는다.**

# 한국어 공시문은 사실상 모든 문장이 "...다."로 끝난다. 원문에는 "구성되어
# 있습니다.우리의 의견으로는"처럼 마침표 뒤 공백이 없는 경우가 흔해 공백을
# 요구하지 않는다.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=다\.)\s*")

# 재무제표 의견 문장은 반드시 그 대상(재무제표/연결재무제표)을 가리킨다.
_FS_SUBJECT_RE = re.compile(r"(?:연결)?재무제표")


def _split_sentences(text: str) -> list[str]:
    return [s for s in (part.strip() for part in _SENTENCE_SPLIT_RE.split(text)) if s]


def _is_icfr_sentence(sentence: str) -> bool:
    """내부회계관리제도를 말하면서 재무제표를 가리키지 않는 문장.

    재무제표 감사보고서 의견 문단 끝에 흔히 붙는 상호참조 문장("우리는 또한 ...
    내부회계관리제도를 감사하였으며, ...자 감사보고서에서 부적정의견을
    표명하였습니다")과 내부회계 판정 문장("우리의 의견으로는 ... 내부회계관리제도는
    ... 효과적으로 설계 및 운영되고 있(지 않)습니다")이 여기 해당한다."""

    return "내부회계관리제도" in sentence and not _FS_SUBJECT_RE.search(sentence)


def _strip_icfr_sentences(para: str) -> str:
    """문단에서 내부회계 문장만 걷어낸다(재무제표 문장 순서는 그대로)."""

    if "내부회계관리제도" not in para:
        return para
    return " ".join(s for s in _split_sentences(para) if not _is_icfr_sentence(s))


def _fs_verdict_text(para: str) -> str:
    """`classify_opinion`에 넣을 텍스트: 재무제표 의견을 말하는 문장(무리)만.

    「우리의 의견으로는 별첨된 회사의 재무제표는 ... 공정하게 표시하고 있습니다」
    처럼 **재무제표/연결재무제표를 가리키면서 판정 문구를 담은 문장**을 찾아,
    거기서부터 이어지는 판정 문장들을 함께 남긴다("문장 무리"). 의견 문단이 한
    문장으로 끝나지 않는 실측 형태가 있기 때문이다 - 신창모터스
    20260413003086은 당기(적정) 문장 다음에 비교표시 전기에 대한 한정 문장
    (「...수정사항의 영향을 제외하고는, 2024년 12월 31일 현재의 재무상태...」)이
    이어지고, 그 문장은 「재무제표」라는 낱말을 쓰지 않는다. 앞 문장만 봤다면
    한정이 적정으로 바뀌었을 것이다.

    내부회계 문장은 무리에 들어오지 못한다. 판정 문장을 하나도 못 찾으면(문단이
    문장 중간에서 잘렸거나 구형식 문구인 경우) 문단을 그대로 돌려준다 - 가드가
    판정을 없애 버리지는 않는다."""

    sentences = _split_sentences(para)
    start = next(
        (
            i
            for i, s in enumerate(sentences)
            if _FS_SUBJECT_RE.search(s) and any(marker in s for marker in _CLASSIFY_MARKERS)
        ),
        None,
    )
    if start is None:
        return para

    group = [
        s
        for s in sentences[start:]
        if not _is_icfr_sentence(s) and any(marker in s for marker in _CLASSIFY_MARKERS)
    ]
    return " ".join(group)


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

    어느 경로든 분류기에 들어가는 텍스트는 ``_strip_icfr_sentences`` +
    ``_fs_verdict_text``를 거친다 - 내부회계 판정 문장은 분류기에 닿지 않는다.
    의견 문장 정규식 폴백도 내부회계 문장에 걸리면 건너뛰고 다음 문장을 본다.

    폴백(의견 문장 정규식) 경로는 값은 만들되 확정하지 않는다 - 항상
    ``unconfirmed``다.
    """

    header_paras = [
        _strip_icfr_sentences(_header_opinion_paragraph(section))
        for section in carve_fs_sections(full_text)
    ]
    confirmed = [
        para for para in header_paras if any(m in _fs_verdict_text(para) for m in _CLASSIFY_MARKERS)
    ]

    if confirmed:
        para = confirmed[0]
        opinion = classify_opinion(_fs_verdict_text(para))
        status = "confirmed" if opinion != "unknown" else "unconfirmed"
        return opinion, para, status, "header", len(confirmed)

    fs_text = _fs_text_excluding_icfr(full_text)
    para = _strip_icfr_sentences(_header_opinion_paragraph(fs_text))
    if any(marker in _fs_verdict_text(para) for marker in _CLASSIFY_MARKERS):
        opinion = classify_opinion(_fs_verdict_text(para))
        status = "confirmed" if opinion != "unknown" else "unconfirmed"
        return opinion, para, status, "header", 1

    for match in _OPINION_SENTENCE_FALLBACK_RE.finditer(fs_text):
        sentence = match.group(0)
        if _is_icfr_sentence(sentence):
            continue
        return classify_opinion(sentence), sentence, "unconfirmed", "sentence_fallback", 1

    return classify_opinion(_fs_verdict_text(para)), para, "unconfirmed", "header" if para else "", 1


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
# 계속기업(going concern) - 오탐 방지 + 기재 위치·시점 (af-v2)
# ---------------------------------------------------------------------------

# 계속기업 불확실성을 **서술하는** 문장. 표제 없이 의견근거 단락 안에만 있는
# 실측 형태(팡스카이 20260323001719, 삼보산업 20260407003361)를 잡기 위한
# 것이다. 판정의 갈림길은 "제기/초래합니다"(실제로 의문이 제기됨을 서술) 대
# "초래할 수 있는"(감사인의 책임 보일러플레이트의 가정법)이다 - 후자는 계속기업
# 문제가 전혀 없는 보고서에도 항상 있으므로 절대 트리거가 되면 안 된다.
#
# 문안 근거: 한국공인회계사회 「감사보고서 작성사례」(2018.10) 5.1
# ("...존속능력에 유의적 의문을 제기할 만한 중요한 불확실성이 존재함을
# 나타냅니다"), 5.7("...계속기업으로의 존속능력에 대하여 유의적인 의문을
# 초래합니다"), 5.8("...계속기업전제에 유의적 의문을 제기하는 상황에도
# 불구하고").
#
# 실측 1,200건 표본의 「의문을 (제기|초래)」 뒤 문구 분포가 이 경계를 그대로
# 보여준다: 초래+"할 (수 있는) 사건이나 상황"이 1,141회(전부 감사인의 책임
# 보일러플레이트)인 반면, 초래+"합니다/하며/하게 되었으며"는 14회이고 전부
# 실제 기재다. 제기 쪽은 "할 만한/할 수 있는/하는/하고 있습니다" 어느 형태든
# 실제 기재였다. 그래서 **초래 뒤에 "할"이 오는 형태만** 제외한다.
_GOING_CONCERN_RAISE_RE = re.compile(
    r"계속기업(?:으로서의|으로의|으로서|으로)?\s*"
    r"(?:가정에?\s*(?:대하여\s*)?)?"
    r"(?:존속능력에?\s*(?:대하여\s*)?)?"
    r"유의적(?:인|\s)*\s*의문을?\s*(?:제기|초래(?!할))"
)

# "계속기업가정(의|에 대한) (중요한) 불확실성" - 소제목·본문에서 함께 쓰이는
# 표기(팡스카이의 「(1) 계속기업가정에 대한 불확실성」, 삼보산업의 「주석 36은
# 연결실체의 계속기업가정의 불확실성에 대하여 기술하고 있습니다」). "계속기업
# 전제"(보일러플레이트)와 달리 "가정"과 "불확실성"이 붙어 나오는 형태만 잡는다.
_GOING_CONCERN_UNCERTAINTY_RE = re.compile(
    r"계속기업\s*가정(?:의|에\s*대한)?\s*(?:중요한|중대한)?\s*불확실성"
)

# 의견근거 단락의 표제. 실측 표기가 "의견거절근거"(붙임)와 "의견거절의 근거"
# (띄어씀) 둘 다이고, 뒤에 곧바로 번호매김이 붙는 형태("의견거절의 근거(1)")도
# 있어 정규식으로 찾는다.
_BASIS_HEADER_RE = re.compile(r"(?:감사의견|한정의견|부적정의견|의견거절)(?:의)?\s*근거")

# 의견근거 단락이 끝나는 자리를 찾을 때는 계속기업 표제도 경계로 본다 -
# 「감사의견근거 ... 믿습니다. 계속기업 관련 중요한 불확실성 ...」처럼 계속기업
# 구역이 의견근거 **다음**에 오는 형태(작성사례 5.1)를 의견근거 안으로
# 빨아들이면 위치 판정이 뒤집힌다.
_GOING_CONCERN_BOUNDARY_LANDMARKS: tuple[str, ...] = _SECTION_LANDMARKS + _GOING_CONCERN_HEADERS

# 핵심감사사항 서문은 계속기업 구역을 **상호참조**한다("우리는 계속기업 관련
# 중요한 불확실성 단락에 기술된 사항에 추가하여, ... 핵심감사사항으로
# 결정하였습니다" - 작성사례 5.1·6.2). 그 문장을 근거로 삼으면 계속기업 근거
# 문단이 핵심감사사항 서문이 된다(실측: 삼보산업 20260407003361).
_KAM_PREAMBLE_MARKERS: tuple[str, ...] = (
    "핵심감사사항으로 결정하였습니다",
    "커뮤니케이션할 핵심감사사항",
)

# 전기(前期) 기재임을 드러내는 표지. "타감사인"/"전임 감사인"은 작성사례
# 10.6.1의 전기 타감사인 문안에 나온다.
_PRIOR_PERIOD_TERM_RE = re.compile(r"전기|직전\s*사업연도|전임\s*감사인|타\s*감사인")

# "20X0년 12월 31일로 종료되는 보고기간" - 문장이 가리키는 보고기간 종료 연도.
_PERIOD_END_YEAR_RE = re.compile(r"(\d{4})년\s*\d{1,2}월\s*\d{1,2}일로?\s*종료")


def _is_section_break_position(text: str, idx: int) -> bool:
    """`idx`가 **새 구역이 시작하는** 자리인가(앞 문장이 끝난 자리인가).

    ``_is_heading_position``보다 좁다 - 번호매김 뒤("의견거절근거 (1) 계속기업
    관련 중요한 불확실성")는 표제 자리이긴 해도 **같은 구역 안의 소제목**이므로
    새 구역으로 보지 않는다. 이 구분이 실측 두 형태를 가른다:

    - 코이즈 20260323001579: 「...믿습니다. 계속기업 관련 중요한 불확실성」 →
      의견근거 다음의 별도 구역(`separate_section`).
    - STX마린서비스 20251219000526: 「의견거절근거 (1) 계속기업 관련 중요한
      불확실성」 → 의견근거 **안**의 소제목(`within_basis_for_opinion`).
    """

    prev_idx = _prev_nonspace_index(text, idx)
    if prev_idx == -1:
        return True
    if text[prev_idx] not in ".。":
        return False
    # "1." 같은 번호매김의 마침표는 문장 끝이 아니다.
    before_dot = _prev_nonspace_index(text, prev_idx)
    return before_dot == -1 or not text[before_dot].isdigit()


def _basis_for_opinion_spans(text: str, *, max_len: int = 6000) -> list[tuple[int, int]]:
    """의견근거 단락들의 [시작, 끝) 범위(표제 자리에 있는 것만).

    다음 표제를 못 찾으면 ``_section_text``와 같은 상한(6,000자)에서 끊는다 -
    표제가 빠진 문서에서 「의견근거 단락」이 별첨 재무제표까지 뻗어 그 안의 주석
    문단을 `within_basis_for_opinion`으로 만들지 않게 하는 안전장치다."""

    spans: list[tuple[int, int]] = []
    for m in _BASIS_HEADER_RE.finditer(text):
        if not _is_heading_position(text, m.start()):
            continue
        end = min(len(text), m.end() + max_len)
        for landmark in _GOING_CONCERN_BOUNDARY_LANDMARKS:
            pos = m.end()
            while True:
                idx = _find_landmark(text, landmark, pos, heading_only=True)
                if idx == -1:
                    break
                if _is_section_break_position(text, idx):
                    end = min(end, idx)
                    break
                pos = idx + len(landmark)
        spans.append((m.start(), end))
    return spans


# 문장 창의 최대 폭. 별첨 재무제표의 표는 마침표 없이 수백 자가 이어지므로
# "...다."만 믿고 뒤로 거슬러 올라가면 표 전체가 한 문장이 돼 버린다(그 안의
# 「전기」 같은 낱말 하나가 시점 필터를 오작동시킨다). 감사보고서의 실제 문장은
# 300자를 넘지 않는다.
_SENTENCE_WINDOW = 300


def _sentence_bounds(text: str, pos: int) -> tuple[int, int]:
    """`pos`를 품은 문장의 [시작, 끝). 한국어 공시문은 모두 "...다."로 끝난다."""

    floor = max(0, pos - _SENTENCE_WINDOW)
    start = text.rfind("다.", floor, pos)
    start = floor if start == -1 else start + 2
    ceiling = min(len(text), pos + 2 * _SENTENCE_WINDOW)
    end = text.find("다.", pos, ceiling)
    end = ceiling if end == -1 else end + 2
    return start, end


def _refers_to_another_report(sentence: str) -> bool:
    """이 문장이 **다른 감사보고서의 의견**을 옮겨 적고 있는가.

    전기 기재를 가리키는 문장은 예외 없이 그 보고서를 함께 말한다("...자
    감사보고서에는 ... 의견이 표명되었습니다" - 작성사례 10.6.1). 시점 필터를
    이 형태로 한정하는 이유는 오작동을 막기 위해서다 - 계속기업 문단은 비교
    표시 때문에 「전기」라는 낱말을 자연스럽게 품는데("전기말 결손금"), 낱말만
    보고 걸러 내면 당기 기재가 통째로 사라진다."""

    return "감사보고서" in sentence and ("표명" in sentence or "의견이" in sentence)


def _is_prior_period_sentence(sentence: str, fiscal_year: int | None) -> bool:
    """이 문장이 **전기** 기재를 가리키는가(=당기 기재로 세면 안 되는가).

    다른 보고서의 의견을 옮겨 적은 문장(``_refers_to_another_report``) 중
    둘 중 하나에 해당하면 전기 기재다:

    1. 「전기」·「직전 사업연도」·「전임 감사인」·「타 감사인」 같은 표지가 있다.
    2. 표지가 없어도, 보고서 대상 회계연도와 **다른 연도**의 보고기간을 가리킨다.
       실측: STX마린서비스 20260413000920의 기타사항 「회사의 2024년 12월 31일로
       종료되는 보고기간의 재무제표는 삼일회계법인에서 감사하였으며, 2025년 12월
       19일자 감사보고서에서 ... 계속기업가정 불확실성으로 의견거절을
       표명하였습니다」 - 표지 낱말이 하나도 없는 당기(2025) 아닌 기재다.
    """

    if not _refers_to_another_report(sentence):
        return False
    if _PRIOR_PERIOD_TERM_RE.search(sentence):
        return True
    if fiscal_year is None:
        return False
    years = {int(y) for y in _PERIOD_END_YEAR_RE.findall(sentence)}
    return bool(years) and fiscal_year not in years


def _going_concern_candidates(text: str) -> list[tuple[int, bool]]:
    """계속기업 근거 후보 위치들 - ``(위치, 표제인가)``, 원문 순서."""

    candidates: list[tuple[int, bool]] = []
    for header in _GOING_CONCERN_HEADERS:
        pos = 0
        while True:
            idx = _find_landmark(text, header, pos)
            if idx == -1:
                break
            candidates.append((idx, True))
            pos = idx + len(header)
    for pattern in (_GOING_CONCERN_RAISE_RE, _GOING_CONCERN_UNCERTAINTY_RE):
        candidates.extend((m.start(), False) for m in pattern.finditer(text))
    return sorted(set(candidates))


def locate_going_concern(
    full_text: str, *, fiscal_year: int | None = None
) -> tuple[str, str, str]:
    """``(gc_material_uncertainty, gc_location, snippet)``.

    - ``gc_material_uncertainty``: ``"disclosed"`` | ``"not_disclosed"`` |
      ``"unknown"``(본문 자체를 못 얻음).
    - ``gc_location``: ``"separate_section"``(별도 구역 표제) |
      ``"within_basis_for_opinion"``(의견근거 단락 안) | ``"other"``(그 밖의
      본문) | ``""``(기재 없음) | ``"unknown"``(본문 없음).
    - ``snippet``: 선택된 근거 문단 앞 400자. 계속기업 문장이 **실제로 있는**
      자리에서만 딴다.

    후보를 원문 순서로 모은 뒤, 핵심감사사항 서문의 상호참조와 전기 기재를
    걸러내고, 남은 것 중 **구조가 확인되는 것**(별도 구역/의견근거 안)을 먼저
    고른다. 아무 후보도 남지 않으면 ``not_disclosed``다."""

    if not full_text.strip():
        return "unknown", "unknown", ""

    basis_spans = _basis_for_opinion_spans(full_text)
    best: tuple[int, int, bool, str, int] | None = None
    # (rank, 위치, 표제인가, 위치이름, 문단 시작). rank 0 = 구조가 확인된 후보.

    for pos, is_header in _going_concern_candidates(full_text):
        sentence_start, sentence_end = _sentence_bounds(full_text, pos)
        sentence = full_text[sentence_start:sentence_end]
        if any(marker in sentence for marker in _KAM_PREAMBLE_MARKERS):
            continue
        if _is_prior_period_sentence(sentence, fiscal_year):
            continue

        if any(start <= pos < end for start, end in basis_spans):
            location = "within_basis_for_opinion"
        elif is_header and _is_section_break_position(full_text, pos):
            location = "separate_section"
        else:
            location = "other"

        candidate = (1 if location == "other" else 0, pos, is_header, location, sentence_start)
        if best is None or candidate[:2] < best[:2]:
            best = candidate

    if best is None:
        return "not_disclosed", "", ""

    _rank, pos, is_header, location, sentence_start = best
    if is_header:
        # 표제 뒤 본문에서 딴다(af-v1과 같은 자리).
        header = next(h for h in _GOING_CONCERN_HEADERS if full_text.startswith(h, pos))
        snippet_start = pos + len(header)
    else:
        snippet_start = sentence_start
    return "disclosed", location, full_text[snippet_start : snippet_start + 400].strip()


def detect_going_concern(full_text: str, *, fiscal_year: int | None = None) -> tuple[bool, str]:
    """계속기업 관련 중요/중대한 불확실성이 **당기 기재**로 있는지 판정한다.

    ``locate_going_concern``의 bool 축약이다(기존 호출부 호환). True인 경우에만
    근거 문단 일부를 함께 반환한다(없으면 두번째 값은 "")."""

    status, _location, snippet = locate_going_concern(full_text, fiscal_year=fiscal_year)
    return status == "disclosed", snippet


# ---------------------------------------------------------------------------
# 핵심감사사항 / 강조사항
# ---------------------------------------------------------------------------


def extract_kam(text: str) -> str:
    """핵심감사사항 섹션 원문(랜드마크~다음 랜드마크). 없으면 ""."""

    return _section_text(text, "핵심감사사항")


def _labelled_section(text: str, header: str, *, max_len: int = 6000) -> tuple[str, str]:
    """``(status, raw)`` - 표제 구역의 유무를 **상태**로 낸다.

    ``status``는 ``"present"``(구역을 찾음) | ``"absent"``(본문은 있는데 구역이
    없음) | ``"unknown"``(본문 자체를 못 얻음)이다.

    같은 표제가 여러 번 나오는 실측 형태가 있다 - 정정신고서의 대비표가
    「강조사항 (주1)」처럼 **자리표시자만** 먼저 싣고, 실제 강조사항 문단은 한참
    뒤 감사보고서 본문에 온다(동성제약 20260407002635: af-v1은 대비표의
    「(주1)」을 강조사항으로 뽑아 회계사가 「없음」으로 읽었다). 그래서 등장을
    모두 훑고 **표제 자리**(``_is_heading_position``)에 있는 첫 구역을 고른다.
    표제 자리인 등장이 하나도 없으면(대비표의 「정정후」 칸처럼 앞 칸에 붙어
    나오는 경우) 문장이 들어 있는 첫 구역으로 물러선다.

    구역의 끝은 ``_SECTION_LANDMARKS``와 계속기업 표제, 그리고 **같은 표제의 다음
    등장**이다 - 대비표에서 「강조사항 정정전 정정후 ... 강조사항 <본문>」처럼
    같은 표제가 잇달아 나오면 그 자리에서 끊어야 칸이 섞이지 않는다."""

    if not text.strip():
        return "unknown", ""

    landmarks = _GOING_CONCERN_BOUNDARY_LANDMARKS + (header,)
    heading_hit = ""
    prose_hit = ""
    pos = 0
    while True:
        # 상호참조(「우리는 강조사항 단락에 기술된 …」)는 구역의 시작도 아니다.
        idx = _find_landmark(
            text, header, pos, skip_cross_reference=header in _CROSS_REFERENCE_CHECKED_LANDMARKS
        )
        if idx == -1:
            break
        start = idx + len(header)
        end = min(_next_landmark_pos(text, start, "", landmarks=landmarks), start + max_len)
        body = text[start:end].strip()
        if body and _is_heading_position(text, idx):
            heading_hit = heading_hit or body
        elif "니다" in body:
            prose_hit = prose_hit or body
        pos = start

    body = heading_hit or prose_hit
    return ("present", body) if body else ("absent", "")


def extract_emphasis(text: str) -> str:
    """강조사항 섹션 원문(``_labelled_section``). 없으면 ""."""

    return _labelled_section(text, "강조사항")[1]


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
    시험할 수 있도록 분리했다(테스트 픽스처가 이 진입점으로 들어온다).

    입력에 ``_normalize_text``를 한 번 더 적용한다 - 멱등이므로
    ``_strip_tags_to_text``를 거쳐 온 평문에는 아무 영향이 없고, 이미 저장돼 있는
    평문 픽스처(예전 정규화 규칙으로 만든 것)에도 새 규칙(갈라진 어미 닫기)이
    똑같이 적용된다."""

    full_text = _normalize_text(text)

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

    # --- fiscal_year / settlement_month ---
    # 계속기업 시점 필터가 "이 보고서가 어느 회계연도의 것인지"를 알아야 하므로
    # going_concern보다 먼저 구한다.
    fiscal_year, settlement_month = derive_fiscal_year(meta, full_text)
    if fiscal_year is not None:
        flags.add("fiscal_year")

    # --- going_concern (기재 위치·시점까지) ---
    (
        gc_material_uncertainty,
        gc_location,
        going_concern_snippet,
    ) = locate_going_concern(full_text, fiscal_year=fiscal_year)
    going_concern = gc_material_uncertainty == "disclosed"
    if going_concern:
        flags.add("going_concern")

    # --- KAM / 강조사항 / 기타사항 ---
    kam_raw = extract_kam(full_text)
    kam_present = bool(kam_raw)
    if kam_present:
        flags.add("kam")
    # `extract_emphasis`(공개 헬퍼)를 다시 부르지 않는다 - 상태와 원문을 한 번에
    # 받아 구역 탐색이 두 번 돌지 않게 한다. 기타사항은 상태만 열로 낸다.
    emphasis_status, emphasis_raw = _labelled_section(full_text, "강조사항")
    other_matter_status, _other_matter_raw = _labelled_section(full_text, "기타사항")

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
        gc_material_uncertainty=gc_material_uncertainty,
        gc_location=gc_location,
        emphasis_status=emphasis_status,
        other_matter_status=other_matter_status,
    )
