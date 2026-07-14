"""`dart_search_mcp.audit_facts_adapter`에 대한 테스트.

Task 2: Task 1의 순수 파서 산출물(`ParsedAuditReport`)을 기존
`dart_search_mcp.temis_export.convert_audit_reports_to_topic_cases`가 이미
소비하는 `AuditReportRecord`(Task 5, OpenDART API 형태)로 변환하는 어댑터
(`to_audit_report_record`)를 검증한다.

핵심 관심사는 corp_cls "E"(비상장) 감사보고서의 다수(실측 92%)가 KAM/강조사항
섹션이 아예 없는 "단순 적정의견" 사실이라는 점이다 — 이 어댑터가 그런 사실을
`AuditReportRecord`로 바르게 변환하고, 기존 `convert_audit_reports_to_topic_cases`가
topic 키워드 매칭 없이도(빈 core_audit_matter/emphasis_matter) 여전히
`DartTopicCaseRecord` 1건(topic_slug="general", topic_tags=[])을 생성하는지가
이 92% 커버리지의 관건이다. 이게 안 되면 대다수 비상장 감사보고서 사실이
TEMIS 파이프라인에서 조용히 유실된다.

레포 관례에 따라 `dart_search_mcp.tools.*`(→ `AuditReportRecord`)의
`@mcp.tool()` 등록 순서를 `tests/test_public_surface.py`가 기대하는 정식
순서로 유지하기 위해 `server`를 먼저 import한다(`tests/test_temis_topic_case_export.py`와
동일한 이유)."""

from __future__ import annotations

import os
import unittest

import server  # noqa: F401
from dart_search_mcp.audit_facts_adapter import (
    parsed_reports_to_topic_cases,
    to_audit_report_record,
)
from dart_search_mcp.audit_xml_parser import ParsedAuditReport, parse_audit_xml
from dart_search_mcp.temis_export import (
    DartTopicCaseRecord,
    TopicCaseSkipped,
    convert_audit_reports_to_topic_cases,
)
from dart_search_mcp.tools.reports import AuditReportRecord

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "audit_xml")
_FRESHNESS = "2026-07-01T00:00:00Z"


def _load_fixture(name: str) -> bytes:
    with open(os.path.join(_FIXTURES_DIR, name), "rb") as f:
        return f.read()


def _parsed(
    *,
    rcept_no: str = "20250701000004",
    corp_code: str = "00363255",
    corp_name: str = "이토마토",
    corp_cls: str = "E",
    category: str = "감사보고서",
    fiscal_year: int | None = 2025,
    settlement_month: int | None = 3,
    auditor: str = "대주회계법인",
    audit_opinion: str = "적정",
    kam_raw: str = "",
    emphasis_raw: str = "",
    source_url: str = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250701000004",
) -> ParsedAuditReport:
    return ParsedAuditReport(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name=corp_name,
        corp_cls=corp_cls,
        stock_code="",
        report_name=(
            f"{category} ({fiscal_year}.{settlement_month:02d})"
            if fiscal_year is not None and settlement_month is not None
            else category
        ),
        rcept_dt="20250701",
        category=category,
        fiscal_year=fiscal_year,
        settlement_month=settlement_month,
        auditor=auditor,
        audit_opinion=audit_opinion,
        opinion_snippet="",
        going_concern=False,
        going_concern_snippet="",
        kam_present=bool(kam_raw),
        kam_raw=kam_raw,
        emphasis_raw=emphasis_raw,
        kam_tags=(),
        parse_flags=frozenset(),
        source_url=source_url,
        doc_path="",
    )


class MappingContractTests(unittest.TestCase):
    """대표 사실(적정+KAM 있음, 적정+KAM 없음, 의견거절) 각각에 대해
    필드가 브리프의 매핑 규칙대로 채워지는지 검증한다."""

    def test_unqualified_with_kam_maps_all_fields(self) -> None:
        parsed = _parsed(
            corp_cls="Y",
            fiscal_year=2025,
            settlement_month=3,
            category="감사보고서",
            kam_raw="핵심감사사항 임대료수익의 발생사실에 대한 설명입니다.",
            emphasis_raw="강조사항 주석 23의 재작성 관련 설명입니다.",
        )

        record = to_audit_report_record(parsed)

        self.assertIsInstance(record, AuditReportRecord)
        self.assertEqual(record.corp_code, parsed.corp_code)
        self.assertEqual(record.corp_name, parsed.corp_name)
        self.assertEqual(record.corp_cls, "Y")
        self.assertEqual(record.rcept_no, parsed.rcept_no)
        self.assertEqual(record.source_url, parsed.source_url)
        self.assertEqual(record.bsns_year, "2025")
        self.assertEqual(record.auditor, parsed.auditor)
        self.assertEqual(record.audit_opinion, "적정")
        self.assertEqual(record.core_audit_matter, parsed.kam_raw)
        self.assertEqual(record.emphasis_matter, parsed.emphasis_raw)
        self.assertEqual(record.special_matter, "")
        self.assertEqual(record.reprt_code, "")
        self.assertEqual(record.business_year_label, "2025년 감사보고서")
        self.assertEqual(record.settlement_date, "2025.03")

    def test_unqualified_without_kam_maps_empty_matters(self) -> None:
        parsed = _parsed(corp_cls="E", kam_raw="", emphasis_raw="")

        record = to_audit_report_record(parsed)

        self.assertEqual(record.audit_opinion, "적정")
        self.assertEqual(record.core_audit_matter, "")
        self.assertEqual(record.emphasis_matter, "")
        self.assertEqual(record.special_matter, "")
        self.assertEqual(record.bsns_year, "2025")
        self.assertEqual(record.business_year_label, "2025년 감사보고서")
        self.assertEqual(record.settlement_date, "2025.03")

    def test_disclaimer_of_opinion_maps_opinion_verbatim(self) -> None:
        parsed = _parsed(
            corp_cls="E",
            audit_opinion="의견거절",
            fiscal_year=2025,
            settlement_month=12,
            category="사업보고서",
        )

        record = to_audit_report_record(parsed)

        self.assertEqual(record.audit_opinion, "의견거절")
        self.assertEqual(record.business_year_label, "2025년 사업보고서")
        self.assertEqual(record.settlement_date, "2025.12")


class FiscalYearNoneTests(unittest.TestCase):
    """fiscal_year가 None이면 bsns_year=""가 되고, 하류
    `convert_audit_reports_to_topic_cases`가 그 사실을 정직하게 skip하는지
    검증한다(조작된 연도 값을 채워넣지 않는 것이 의도된 동작)."""

    def test_fiscal_year_none_yields_empty_bsns_year_and_settlement_date(self) -> None:
        parsed = _parsed(fiscal_year=None, settlement_month=None, category="감사보고서")

        record = to_audit_report_record(parsed)

        self.assertEqual(record.bsns_year, "")
        self.assertEqual(record.settlement_date, "")
        self.assertEqual(record.business_year_label, "감사보고서")

    def test_fiscal_year_none_record_is_skipped_by_convert(self) -> None:
        parsed = _parsed(fiscal_year=None, settlement_month=None)
        record = to_audit_report_record(parsed)

        records, skipped = convert_audit_reports_to_topic_cases([record], freshness_timestamp=_FRESHNESS)

        self.assertEqual(records, [])
        self.assertEqual(len(skipped), 1)
        self.assertIsInstance(skipped[0], TopicCaseSkipped)

    def test_settlement_date_empty_when_only_settlement_month_missing(self) -> None:
        parsed = _parsed(fiscal_year=2025, settlement_month=None)

        record = to_audit_report_record(parsed)

        self.assertEqual(record.settlement_date, "")


class CoreCoverageTests(unittest.TestCase):
    """★ 핵심 커버리지 테스트: KAM/강조사항이 빈(corp_cls E 92% 케이스) 적정
    사실을 어댑팅 -> convert_audit_reports_to_topic_cases에 넣으면 topic 매칭이
    없어도 DartTopicCaseRecord가 1건 생성되고(topic_slug "general", topic_tags
    []), audit_opinion이 보존되는지 검증한다. 이게 안 되면 92%가 유실된다."""

    def test_empty_kam_and_emphasis_unqualified_fact_still_produces_one_general_case(self) -> None:
        parsed = _parsed(
            corp_cls="E",
            audit_opinion="적정",
            kam_raw="",
            emphasis_raw="",
        )
        record = to_audit_report_record(parsed)

        records, skipped = convert_audit_reports_to_topic_cases([record], freshness_timestamp=_FRESHNESS)

        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        case = records[0]
        self.assertIsInstance(case, DartTopicCaseRecord)
        self.assertEqual(case.topic_tags, [])
        self.assertEqual(case.case_id.split("-")[-2], "general")
        self.assertEqual(case.company_identifier, parsed.corp_code)
        self.assertEqual(case.fiscal_year, 2025)
        self.assertEqual(case.report_id, parsed.rcept_no)
        self.assertEqual(case.auditor, parsed.auditor)

    def test_audit_opinion_is_preserved_through_adaptation_and_conversion(self) -> None:
        for opinion in ("적정", "한정", "부적정", "의견거절", "unknown"):
            with self.subTest(opinion=opinion):
                parsed = _parsed(audit_opinion=opinion, kam_raw="", emphasis_raw="")
                record = to_audit_report_record(parsed)
                self.assertEqual(record.audit_opinion, opinion)

                records, skipped = convert_audit_reports_to_topic_cases(
                    [record], freshness_timestamp=_FRESHNESS
                )
                self.assertEqual(skipped, [])
                self.assertEqual(len(records), 1)


class ConvenienceFunctionTests(unittest.TestCase):
    def test_parsed_reports_to_topic_cases_batches_adaptation_and_conversion(self) -> None:
        parsed_list = [
            _parsed(rcept_no="20250701000004", audit_opinion="적정", kam_raw="", emphasis_raw=""),
            _parsed(
                rcept_no="20250701000005",
                corp_code="00983697",
                corp_name="바카디코리아",
                audit_opinion="한정",
            ),
        ]

        records, skipped = parsed_reports_to_topic_cases(parsed_list, freshness_timestamp=_FRESHNESS)

        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 2)
        report_ids = {r.report_id for r in records}
        self.assertEqual(report_ids, {"20250701000004", "20250701000005"})

    def test_parsed_reports_to_topic_cases_skips_invalid_facts(self) -> None:
        parsed_list = [
            _parsed(fiscal_year=None, settlement_month=None),
        ]

        records, skipped = parsed_reports_to_topic_cases(parsed_list, freshness_timestamp=_FRESHNESS)

        self.assertEqual(records, [])
        self.assertEqual(len(skipped), 1)


class EndToEndFixtureTests(unittest.TestCase):
    """실제 파서 출력으로 end-to-end: Task 1 픽스처(01_unlisted_unqualified_no_kam.xml,
    corp_cls "E", 적정, KAM/강조사항 없음 - 실측 92% 케이스의 실제 표본)를
    `parse_audit_xml`로 파싱 -> 어댑팅 -> convert까지 통과하는지 검증한다."""

    _META = {
        "category": "감사보고서",
        "report_name": "감사보고서 (2025.03)",
        "rcept_no": "20250701000004",
        "rcept_dt": "20250701",
        "corp_code": "00363255",
        "corp_name": "이토마토",
        "stock_code": "",
        "corp_cls": "E",
        "flr_nm": "대주회계법인",
        "remark": "",
        "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250701000004",
    }

    def test_real_fixture_parses_adapts_and_converts_to_one_general_case(self) -> None:
        xml_bytes = _load_fixture("01_unlisted_unqualified_no_kam.xml")
        parsed = parse_audit_xml(xml_bytes, self._META, doc_path="fixtures/01.xml")

        # 픽스처가 실제로 이 테스트가 검증하려는 92% 케이스의 모양인지 확인
        # (KAM/강조사항 없음, 적정의견) - 전제가 깨지면 이 테스트는 무의미해진다.
        self.assertEqual(parsed.audit_opinion, "적정")
        self.assertEqual(parsed.kam_raw, "")
        self.assertEqual(parsed.emphasis_raw, "")
        self.assertEqual(parsed.corp_cls, "E")

        record = to_audit_report_record(parsed)
        self.assertEqual(record.bsns_year, "2025")
        self.assertEqual(record.audit_opinion, "적정")

        records, skipped = convert_audit_reports_to_topic_cases([record], freshness_timestamp=_FRESHNESS)

        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        case = records[0]
        self.assertEqual(case.topic_tags, [])
        self.assertIn("general", case.case_id)
        self.assertEqual(case.company_identifier, "00363255")
        self.assertEqual(case.fiscal_year, 2025)
        self.assertEqual(case.report_id, "20250701000004")
        self.assertEqual(
            case.source_url,
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250701000004",
        )


if __name__ == "__main__":
    unittest.main()
