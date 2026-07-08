"""
`dart_search_mcp.temis_export`에 대한 테스트.

Task 6: 구조화된 감사보고서 사실(`AuditReportRecord`, Task 5)을 finov2가
`DART_TOPIC_CASES_PATH`로 소비하는 TEMIS `DartTopicCase` JSON 배열로 변환한다.

핵심 계약(전역 제약 + finov2 `app/schemas/dart_topic_search.py`의 `DartTopicCase`,
frozen pydantic 모델과 정확히 필드/타입이 일치해야 함):
  case_id: str (고유, `dart-<topic>-<year>-<seq>` 패턴)
  company_identifier: str
  company_name: str
  fiscal_year: int
  report_id: str
  auditor: str
  topic_tags: list[str]
  disclosure_snippet: str
  source_url: str (`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=<rcept_no>`)
  document_id: str
  extraction_confidence: float, [0.0, 1.0]
  freshness_timestamp: str (ISO-8601)

finov2의 pydantic 스키마를 이 레포에서 직접 import하지 않는다(레포 간 결합/의존성
추가 위험 회피). 대신 위 계약을 이 테스트가 로컬에서 엄격한 타입으로 검증한다.

레포 관례에 따라 `dart_search_mcp.tools.reports`를 import하기 전에 `server`를
먼저 import한다 (`tests/test_audit_report_structured.py`와 동일한 이유 —
`dart_search_mcp.tools.*`의 `@mcp.tool()` 등록 순서가 `tests/test_public_surface.py`가
기대하는 정식 순서로 이루어지도록 하기 위함이다. 이 모듈 자체는 MCP tool을
등록하지 않지만, `AuditReportRecord`를 얻기 위해 import하는
`dart_search_mcp.tools.reports`가 `@mcp.tool()`을 등록하는 부수 효과를 갖는다).
"""

from __future__ import annotations

import json
import re
import unittest

import server  # noqa: F401
from dart_search_mcp.temis_export import (
    DartTopicCaseRecord,
    TopicCaseSkipped,
    TOPIC_KEYWORDS,
    convert_audit_reports_to_topic_cases,
    topic_cases_to_json,
)
from dart_search_mcp.tools.reports import AuditReportRecord

_ISO_8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_FRESHNESS = "2026-07-01T00:00:00Z"

_REQUIRED_SCHEMA_KEYS = {
    "case_id",
    "company_identifier",
    "company_name",
    "fiscal_year",
    "report_id",
    "auditor",
    "topic_tags",
    "disclosure_snippet",
    "source_url",
    "document_id",
    "extraction_confidence",
    "freshness_timestamp",
}


def _fact(
    *,
    corp_code: str = "00126380",
    corp_name: str = "삼성전자",
    bsns_year: str = "2024",
    rcept_no: str = "20250312001234",
    auditor: str = "삼일회계법인",
    audit_opinion: str = "적정",
    special_matter: str = "",
    emphasis_matter: str = "",
    core_audit_matter: str = "",
) -> AuditReportRecord:
    return AuditReportRecord(
        corp_code=corp_code,
        corp_name=corp_name,
        corp_cls="Y",
        bsns_year=bsns_year,
        reprt_code="11011",
        business_year_label=f"{bsns_year}년 사업보고서",
        rcept_no=rcept_no,
        auditor=auditor,
        audit_opinion=audit_opinion,
        special_matter=special_matter,
        emphasis_matter=emphasis_matter,
        core_audit_matter=core_audit_matter,
        settlement_date=f"{bsns_year}1231" if bsns_year else "",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else "",
    )


class ConvertAuditReportsToTopicCasesTests(unittest.TestCase):
    def test_put_option_case_maps_all_fields_with_strict_types(self) -> None:
        fact = _fact(
            special_matter="풋옵션 약정으로 발생한 금융부채와 비지배지분 관련 사항을 검토하였다.",
        )

        records, skipped = convert_audit_reports_to_topic_cases([fact], freshness_timestamp=_FRESHNESS)

        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertIsInstance(record, DartTopicCaseRecord)

        self.assertEqual(record.case_id, "dart-put-option-2024-001")
        self.assertEqual(record.company_identifier, "00126380")
        self.assertEqual(record.company_name, "삼성전자")
        self.assertIs(type(record.fiscal_year), int)
        self.assertEqual(record.fiscal_year, 2024)
        self.assertEqual(record.report_id, "20250312001234")
        self.assertEqual(record.auditor, "삼일회계법인")
        self.assertIsInstance(record.topic_tags, list)
        self.assertTrue(all(type(tag) is str for tag in record.topic_tags))
        self.assertEqual(record.topic_tags, ["풋옵션", "금융부채", "비지배지분"])
        self.assertIn("풋옵션", record.disclosure_snippet)
        self.assertEqual(
            record.source_url,
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250312001234",
        )
        self.assertEqual(record.document_id, "20250312001234")
        self.assertIs(type(record.extraction_confidence), float)
        self.assertGreaterEqual(record.extraction_confidence, 0.0)
        self.assertLessEqual(record.extraction_confidence, 1.0)
        self.assertEqual(record.freshness_timestamp, _FRESHNESS)
        self.assertRegex(record.freshness_timestamp, _ISO_8601_RE)

    def test_revenue_case_matches_fixture_style_ordering(self) -> None:
        fact = _fact(
            corp_code="00164779",
            corp_name="현대자동차",
            bsns_year="2023",
            rcept_no="20240315004567",
            auditor="한영회계법인",
            core_audit_matter="보증형 용역과 별도 수행의무를 구분하여 수익인식 시점을 판단하였다.",
            emphasis_matter="계약부채 인식 관련 강조사항.",
        )

        records, skipped = convert_audit_reports_to_topic_cases([fact], freshness_timestamp=_FRESHNESS)

        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.case_id, "dart-revenue-2023-001")
        self.assertEqual(record.topic_tags, ["수익인식", "보증", "계약부채"])

    def test_multiple_facts_same_topic_and_year_get_unique_sequential_case_ids(self) -> None:
        fact_a = _fact(rcept_no="20250312000001", special_matter="풋옵션 관련 사항.")
        fact_b = _fact(rcept_no="20250312000002", special_matter="풋옵션 관련 별도 사항.")

        records, skipped = convert_audit_reports_to_topic_cases(
            [fact_a, fact_b], freshness_timestamp=_FRESHNESS
        )

        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 2)
        case_ids = [r.case_id for r in records]
        self.assertEqual(case_ids, ["dart-put-option-2024-001", "dart-put-option-2024-002"])
        self.assertEqual(len(set(case_ids)), len(case_ids))

    def test_no_keyword_match_uses_general_slug_and_empty_tags(self) -> None:
        fact = _fact(audit_opinion="적정", special_matter="특이사항 없음.")

        records, skipped = convert_audit_reports_to_topic_cases([fact], freshness_timestamp=_FRESHNESS)

        self.assertEqual(skipped, [])
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.topic_tags, [])
        self.assertTrue(record.case_id.startswith("dart-general-2024-"))

    def test_empty_rcept_no_is_skipped_and_cannot_produce_fake_url(self) -> None:
        fact = _fact(rcept_no="")

        records, skipped = convert_audit_reports_to_topic_cases([fact], freshness_timestamp=_FRESHNESS)

        self.assertEqual(records, [])
        self.assertEqual(len(skipped), 1)
        self.assertIsInstance(skipped[0], TopicCaseSkipped)
        self.assertIn("접수번호", skipped[0].reason)

    def test_whitespace_only_rcept_no_is_skipped(self) -> None:
        fact = _fact(rcept_no="   ")

        records, skipped = convert_audit_reports_to_topic_cases([fact], freshness_timestamp=_FRESHNESS)

        self.assertEqual(records, [])
        self.assertEqual(len(skipped), 1)

    def test_missing_corp_code_is_skipped(self) -> None:
        fact = _fact(corp_code="")

        records, skipped = convert_audit_reports_to_topic_cases([fact], freshness_timestamp=_FRESHNESS)

        self.assertEqual(records, [])
        self.assertEqual(len(skipped), 1)

    def test_non_numeric_bsns_year_is_skipped(self) -> None:
        fact = _fact(bsns_year="N/A")

        records, skipped = convert_audit_reports_to_topic_cases([fact], freshness_timestamp=_FRESHNESS)

        self.assertEqual(records, [])
        self.assertEqual(len(skipped), 1)

    def test_mixed_valid_and_invalid_facts_preserve_order_for_valid_ones(self) -> None:
        valid_a = _fact(rcept_no="20250312000001", special_matter="풋옵션.")
        invalid = _fact(rcept_no="")
        valid_b = _fact(rcept_no="20250312000002", special_matter="풋옵션.")

        records, skipped = convert_audit_reports_to_topic_cases(
            [valid_a, invalid, valid_b], freshness_timestamp=_FRESHNESS
        )

        self.assertEqual(len(records), 2)
        self.assertEqual(len(skipped), 1)
        self.assertEqual(records[0].report_id, "20250312000001")
        self.assertEqual(records[1].report_id, "20250312000002")

    def test_topic_keywords_dictionary_is_a_small_deterministic_tuple(self) -> None:
        self.assertIsInstance(TOPIC_KEYWORDS, tuple)
        self.assertGreater(len(TOPIC_KEYWORDS), 0)
        for slug, term in TOPIC_KEYWORDS:
            self.assertIsInstance(slug, str)
            self.assertIsInstance(term, str)
            self.assertEqual(term, term.strip())
            self.assertEqual(term, term.strip(), "keywords must be pre-stripped for substring matching")


class TopicCasesToJsonTests(unittest.TestCase):
    def test_serializes_to_json_array_matching_finov2_schema_shape(self) -> None:
        fact_1 = _fact(
            corp_code="00126380",
            corp_name="삼성전자",
            bsns_year="2024",
            rcept_no="20250312001234",
            auditor="삼일회계법인",
            special_matter="풋옵션 약정으로 발생한 금융부채와 비지배지분 관련 사항을 검토하였다.",
        )
        fact_2 = _fact(
            corp_code="00164779",
            corp_name="현대자동차",
            bsns_year="2023",
            rcept_no="20240315004567",
            auditor="한영회계법인",
            core_audit_matter="보증형 용역과 별도 수행의무를 구분하여 수익인식 시점을 판단하였다.",
        )

        records, skipped = convert_audit_reports_to_topic_cases(
            [fact_1, fact_2], freshness_timestamp=_FRESHNESS
        )
        self.assertEqual(skipped, [])

        raw_json = topic_cases_to_json(records)
        parsed = json.loads(raw_json)

        self.assertIsInstance(parsed, list)
        self.assertEqual(len(parsed), 2)

        case_ids = set()
        for item in parsed:
            self.assertIsInstance(item, dict)
            self.assertEqual(set(item.keys()), _REQUIRED_SCHEMA_KEYS)

            self.assertIs(type(item["case_id"]), str)
            self.assertIs(type(item["company_identifier"]), str)
            self.assertIs(type(item["company_name"]), str)
            self.assertIs(type(item["fiscal_year"]), int)
            self.assertIs(type(item["report_id"]), str)
            self.assertIs(type(item["auditor"]), str)
            self.assertIsInstance(item["topic_tags"], list)
            self.assertTrue(all(type(t) is str for t in item["topic_tags"]))
            self.assertIs(type(item["disclosure_snippet"]), str)
            self.assertIs(type(item["source_url"]), str)
            self.assertTrue(item["source_url"].startswith("https://dart.fss.or.kr/dsaf001/main.do?rcpNo="))
            self.assertIs(type(item["document_id"]), str)
            self.assertIs(type(item["extraction_confidence"]), float)
            self.assertGreaterEqual(item["extraction_confidence"], 0.0)
            self.assertLessEqual(item["extraction_confidence"], 1.0)
            self.assertIs(type(item["freshness_timestamp"]), str)
            self.assertRegex(item["freshness_timestamp"], _ISO_8601_RE)

            case_ids.add(item["case_id"])

        self.assertEqual(len(case_ids), len(parsed), "case_id must be unique per record")

    def test_serializes_empty_list_to_empty_json_array(self) -> None:
        self.assertEqual(topic_cases_to_json([]), "[]")

    def test_json_is_deterministic_for_same_input(self) -> None:
        fact = _fact(special_matter="풋옵션 관련 사항.")
        records, _ = convert_audit_reports_to_topic_cases([fact], freshness_timestamp=_FRESHNESS)

        first = topic_cases_to_json(records)
        second = topic_cases_to_json(records)

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
