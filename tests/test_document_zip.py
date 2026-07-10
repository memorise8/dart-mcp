"""`dart_search_mcp.document_zip`에 대한 테스트.

Step 2a: DART 문서 ZIP(bytes/path)을 열어 XML 엔트리를 감사보고서/
연결감사보고서/본문보고서로 분류하는 순수 모듈. 실제 OpenDART 호출 없이
`zipfile.ZipFile(BytesIO(), "w")`로 실제 확인된 ZIP 구조를 모방한 인메모리
fixture만 사용한다.

이 모듈은 삭제된 `document_extraction.py`(키워드 스니펫 추출, dead code로
제거됨)를 복원하는 것이 아니다 — ZIP 엔트리 분류만 담당한다.
"""

from __future__ import annotations

import unittest
import zipfile
from io import BytesIO

from dart_search_mcp.document_zip import (
    DocumentEntry,
    DocumentZipContents,
    DocumentZipError,
    inspect_document_zip,
    read_entry,
)

_RCEPT_NO = "20260323001689"


def _document_xml(acode: str, name: str, *, padding: int = 0) -> bytes:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<DOCUMENT xmlns="http://dart.fss.or.kr/dsae">'
        f'<DOCUMENT-NAME ACODE="{acode}">{name}</DOCUMENT-NAME>'
        '<FORMULA-VERSION FORMULA-VERSION="20"/>'
        '<COMPANY-NAME AREGCIK="00126380">삼성전자</COMPANY-NAME>'
        "</DOCUMENT>"
    )
    if padding:
        # 헤더는 앞부분에 남기고, 뒤쪽에 큰 패딩을 더해 head-read 경로를 검증.
        body = body.replace("</DOCUMENT>", "<PADDING>" + ("x" * padding) + "</PADDING></DOCUMENT>")
    return body.encode("utf-8")


def _document_xml_no_acode(name: str) -> bytes:
    body = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<DOCUMENT xmlns="http://dart.fss.or.kr/dsae">'
        f"<DOCUMENT-NAME>{name}</DOCUMENT-NAME>"
        "</DOCUMENT>"
    )
    return body.encode("utf-8")


def _build_zip(entries: dict[str, bytes]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for filename, content in entries.items():
            zf.writestr(filename, content)
    return buf.getvalue()


class StandaloneAuditZipTests(unittest.TestCase):
    def test_standalone_audit_zip_has_one_audit_entry(self) -> None:
        data = _build_zip(
            {f"{_RCEPT_NO}_00760.xml": _document_xml("00760", "감사보고서")}
        )

        result = inspect_document_zip(data)

        self.assertIsInstance(result, DocumentZipContents)
        assert isinstance(result, DocumentZipContents)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(len(result.audit_entries), 1)
        self.assertEqual(len(result.consolidated_audit_entries), 0)
        self.assertIsNone(result.main_report_entry)

        entry = result.audit_entries[0]
        self.assertIsInstance(entry, DocumentEntry)
        self.assertEqual(entry.filename, f"{_RCEPT_NO}_00760.xml")
        self.assertEqual(entry.document_name, "감사보고서")
        self.assertEqual(entry.acode, "00760")
        self.assertTrue(entry.is_audit)
        self.assertFalse(entry.is_consolidated_audit)
        self.assertFalse(entry.is_main_report)
        self.assertGreater(entry.size, 0)


class StandaloneConsolidatedAuditZipTests(unittest.TestCase):
    def test_standalone_consolidated_audit_zip_not_flagged_as_plain_audit(self) -> None:
        data = _build_zip(
            {f"{_RCEPT_NO}_00761.xml": _document_xml("00761", "연결감사보고서")}
        )

        result = inspect_document_zip(data)

        self.assertIsInstance(result, DocumentZipContents)
        assert isinstance(result, DocumentZipContents)
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(len(result.consolidated_audit_entries), 1)
        # 회귀 방지: 연결감사보고서는 plain 감사보고서로 이중 분류되면 안 된다.
        self.assertEqual(len(result.audit_entries), 0)

        entry = result.consolidated_audit_entries[0]
        self.assertEqual(entry.document_name, "연결감사보고서")
        self.assertEqual(entry.acode, "00761")
        self.assertTrue(entry.is_consolidated_audit)
        self.assertFalse(entry.is_audit)


class BusinessReportWithSeparateAuditEntriesTests(unittest.TestCase):
    def test_business_report_with_separate_audit_entries_classifies_all_three(self) -> None:
        data = _build_zip(
            {
                f"{_RCEPT_NO}.xml": _document_xml("11011", "사업보고서"),
                f"{_RCEPT_NO}_00760.xml": _document_xml("00760", "감사보고서"),
                f"{_RCEPT_NO}_00761.xml": _document_xml("00761", "연결감사보고서"),
            }
        )

        result = inspect_document_zip(data)

        self.assertIsInstance(result, DocumentZipContents)
        assert isinstance(result, DocumentZipContents)
        self.assertEqual(len(result.entries), 3)

        main_entry = result.main_report_entry
        self.assertIsNotNone(main_entry)
        assert main_entry is not None
        self.assertEqual(main_entry.filename, f"{_RCEPT_NO}.xml")
        self.assertEqual(main_entry.document_name, "사업보고서")
        self.assertEqual(main_entry.acode, "11011")
        self.assertTrue(main_entry.is_main_report)
        self.assertFalse(main_entry.is_audit)
        self.assertFalse(main_entry.is_consolidated_audit)

        self.assertEqual(len(result.audit_entries), 1)
        self.assertEqual(result.audit_entries[0].filename, f"{_RCEPT_NO}_00760.xml")

        self.assertEqual(len(result.consolidated_audit_entries), 1)
        self.assertEqual(result.consolidated_audit_entries[0].filename, f"{_RCEPT_NO}_00761.xml")


class BusinessReportWithInlineAuditOnlyTests(unittest.TestCase):
    def test_business_report_with_only_main_xml_has_no_audit_entries(self) -> None:
        """감사보고서가 본문 XML에 인라인으로 포함되어 별도 엔트리가 없는 경우.

        이는 정상적인 "없음" 상태이며 오류가 아니다 (범위 밖: 인라인 내용
        추출은 이 모듈이 하지 않는다)."""
        data = _build_zip(
            {f"{_RCEPT_NO}.xml": _document_xml("11011", "사업보고서")}
        )

        result = inspect_document_zip(data)

        self.assertIsInstance(result, DocumentZipContents)
        assert isinstance(result, DocumentZipContents)
        self.assertEqual(len(result.entries), 1)

        main_entry = result.main_report_entry
        self.assertIsNotNone(main_entry)
        assert main_entry is not None
        self.assertTrue(main_entry.is_main_report)

        self.assertEqual(result.audit_entries, [])
        self.assertEqual(result.consolidated_audit_entries, [])


class AcodeAbsentTests(unittest.TestCase):
    def test_document_name_present_without_acode_classifies_by_name_text(self) -> None:
        data = _build_zip(
            {f"{_RCEPT_NO}_00760.xml": _document_xml_no_acode("감사보고서")}
        )

        result = inspect_document_zip(data)

        self.assertIsInstance(result, DocumentZipContents)
        assert isinstance(result, DocumentZipContents)
        self.assertEqual(len(result.entries), 1)

        entry = result.entries[0]
        self.assertEqual(entry.document_name, "감사보고서")
        self.assertEqual(entry.acode, "")
        self.assertTrue(entry.is_audit)
        self.assertFalse(entry.is_consolidated_audit)


class CorruptAndEmptyZipTests(unittest.TestCase):
    def test_bytes_that_are_not_a_zip_return_typed_error(self) -> None:
        result = inspect_document_zip(b"this is definitely not a zip file")

        self.assertIsInstance(result, DocumentZipError)
        assert isinstance(result, DocumentZipError)
        self.assertTrue(result.message)

    def test_empty_bytes_return_typed_error(self) -> None:
        result = inspect_document_zip(b"")

        self.assertIsInstance(result, DocumentZipError)
        assert isinstance(result, DocumentZipError)
        self.assertTrue(result.message)


class LargeHeadCaseTests(unittest.TestCase):
    def test_document_name_within_head_chunk_is_parsed_even_when_file_is_padded_large(self) -> None:
        padded = _document_xml("00760", "감사보고서", padding=20_000)
        self.assertGreater(len(padded), 8192)

        data = _build_zip({f"{_RCEPT_NO}_00760.xml": padded})

        result = inspect_document_zip(data)

        self.assertIsInstance(result, DocumentZipContents)
        assert isinstance(result, DocumentZipContents)
        self.assertEqual(len(result.entries), 1)

        entry = result.entries[0]
        self.assertEqual(entry.document_name, "감사보고서")
        self.assertEqual(entry.acode, "00760")
        self.assertTrue(entry.is_audit)
        self.assertEqual(entry.size, len(padded))


class ReadEntryTests(unittest.TestCase):
    def test_read_entry_returns_bytes_for_existing_entry(self) -> None:
        content = _document_xml("00760", "감사보고서")
        data = _build_zip({f"{_RCEPT_NO}_00760.xml": content})

        result = read_entry(data, f"{_RCEPT_NO}_00760.xml")

        self.assertEqual(result, content)

    def test_read_entry_returns_typed_error_for_missing_entry(self) -> None:
        data = _build_zip({f"{_RCEPT_NO}_00760.xml": _document_xml("00760", "감사보고서")})

        result = read_entry(data, "does-not-exist.xml")

        self.assertIsInstance(result, DocumentZipError)


if __name__ == "__main__":
    unittest.main()
