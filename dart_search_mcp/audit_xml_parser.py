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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


def _extract_opinion_paragraph(full_text: str) -> str:
    """의견 문단을 헤더 기반으로 우선 추출하고, 판정 문구가 없으면 전체
    텍스트에서 의견 문장을 직접 정규식으로 찾아 폴백한다."""

    para = ""
    header_pos = -1
    matched_header = ""
    for header in _OPINION_HEADERS:
        idx = _find_landmark(full_text, header)
        if idx != -1 and (header_pos == -1 or idx < header_pos):
            header_pos = idx
            matched_header = header

    if header_pos != -1:
        start = header_pos
        end = _next_landmark_pos(full_text, header_pos + len(matched_header), matched_header)
        end = min(end, start + 1200)
        para = full_text[start:end].strip()

    if not any(marker in para for marker in _CLASSIFY_MARKERS):
        m = _OPINION_SENTENCE_FALLBACK_RE.search(full_text)
        if m:
            para = m.group(0)

    return para


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
    ``ParsedAuditReport``를 반환한다."""

    full_text = _strip_tags_to_text(xml_bytes)

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

    # --- audit_opinion ---
    opinion_para = _extract_opinion_paragraph(full_text)
    audit_opinion = classify_opinion(opinion_para)
    opinion_snippet = opinion_para[:400] if opinion_para else ""
    if audit_opinion != "unknown":
        flags.add("opinion")

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
    )
