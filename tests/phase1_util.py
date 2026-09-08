"""Phase 1 회귀 테스트가 공유하는 픽스처 로더.

`tests/fixtures/phase1/*.txt`는 실제 DART 원문 XML(`dart_collected/docs/`)을
`dart_search_mcp.audit_xml_parser._strip_tags_to_text`로 평문화한 뒤 감사보고서
본문 구간만 잘라 저장한 것이다(각 파일의 출처 XML과 char 범위는 그 픽스처를
쓰는 테스트 모듈 상단 주석에 표로 적어둔다).

`meta`는 `parse_audit_xml`/`parse_audit_text`가 실제로 받는 manifest
`records[]` 항목과 같은 계약을 따른다 - 파서가 읽는 키는 모두 선택적이며
(`dict.get`), 여기서는 rcept_no/corp_code/corp_name/corp_cls/stock_code/
report_name/rcept_dt/flr_nm를 채운다. 네트워크·LLM·디스크 쓰기는 하지 않는다."""

from __future__ import annotations

from pathlib import Path

from dart_search_mcp.audit_xml_parser import ParsedAuditReport, parse_audit_text

FIXTURES = Path(__file__).parent / "fixtures" / "phase1"

_DEFAULT_REPORT_NAME = "사업보고서 (2025.12)"


def fixture_text(name: str) -> str:
    """픽스처 평문을 그대로 읽어 반환한다."""

    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_meta(name: str, report_name: str = _DEFAULT_REPORT_NAME) -> dict[str, str]:
    """픽스처 파일명 앞의 rcept_no를 쓴 최소 meta(파서 계약과 동일한 키)."""

    return {
        "rcept_no": name.split("-")[0],
        "corp_code": "00000000",
        "corp_name": "x",
        "corp_cls": "Y",
        "stock_code": "",
        "report_name": report_name,
        "rcept_dt": "20260401",
        "flr_nm": "",
    }


def parse_fixture(name: str, report_name: str = _DEFAULT_REPORT_NAME) -> ParsedAuditReport:
    """픽스처 평문을 `parse_audit_text`로 파싱한 결과."""

    return parse_audit_text(fixture_text(name), fixture_meta(name, report_name))
