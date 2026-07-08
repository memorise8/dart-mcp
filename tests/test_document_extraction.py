import os
import tempfile
import unittest
import zipfile
from io import BytesIO

from dart_search_mcp.document_extraction import (
    DocumentExtractionError,
    DocumentExtractionResult,
    extract_snippets,
)


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class ExtractSnippetsXmlTests(unittest.TestCase):
    def test_zip_with_xml_text_returns_snippet_around_keyword(self) -> None:
        xml = (
            "<DOCUMENT><TITLE>감사보고서</TITLE>"
            "<BODY>당사는 외부감사인으로서 회계감사인 의견거절을 통보하였습니다.</BODY>"
            "</DOCUMENT>"
        ).encode("utf-8")
        data = _zip_bytes({"0001.xml": xml})

        result = extract_snippets(data, ["의견거절"])

        self.assertIsInstance(result, DocumentExtractionResult)
        assert isinstance(result, DocumentExtractionResult)
        self.assertEqual(result.file_names, ["0001.xml"])
        self.assertEqual(len(result.snippets), 1)
        snippet = result.snippets[0]
        self.assertEqual(snippet.filename, "0001.xml")
        self.assertEqual(snippet.keyword, "의견거절")
        self.assertIn("의견거절", snippet.snippet)
        # tags must be stripped from the snippet text
        self.assertNotIn("<", snippet.snippet)
        self.assertNotIn(">", snippet.snippet)

    def test_zip_with_cp949_encoded_xml_decodes_korean_text(self) -> None:
        xml = "<BODY>감사인 교체 사유가 발생하였습니다.</BODY>".encode("cp949")
        data = _zip_bytes({"0001.xml": xml})

        result = extract_snippets(data, ["교체"])

        self.assertIsInstance(result, DocumentExtractionResult)
        assert isinstance(result, DocumentExtractionResult)
        self.assertEqual(len(result.snippets), 1)
        self.assertIn("교체", result.snippets[0].snippet)


class ExtractSnippetsMultipleFilesTests(unittest.TestCase):
    def test_zip_with_multiple_files_lists_all_and_scopes_snippets_per_file(self) -> None:
        data = _zip_bytes(
            {
                "0001.xml": "<BODY>주요사항보고서 본문입니다.</BODY>".encode("utf-8"),
                "0002.xml": "<BODY>내부회계관리제도 비적정 의견입니다.</BODY>".encode("utf-8"),
            }
        )

        result = extract_snippets(data, ["비적정"])

        self.assertIsInstance(result, DocumentExtractionResult)
        assert isinstance(result, DocumentExtractionResult)
        self.assertEqual(sorted(result.file_names), ["0001.xml", "0002.xml"])
        self.assertEqual(len(result.snippets), 1)
        self.assertEqual(result.snippets[0].filename, "0002.xml")


class ExtractSnippetsNoMatchTests(unittest.TestCase):
    def test_no_matching_keyword_returns_success_with_empty_snippets(self) -> None:
        data = _zip_bytes({"0001.xml": "<BODY>정상 의견입니다.</BODY>".encode("utf-8")})

        result = extract_snippets(data, ["존재하지않는키워드"])

        self.assertIsInstance(result, DocumentExtractionResult)
        assert isinstance(result, DocumentExtractionResult)
        self.assertEqual(result.file_names, ["0001.xml"])
        self.assertEqual(result.snippets, [])

    def test_empty_keyword_list_returns_file_names_without_scanning(self) -> None:
        data = _zip_bytes({"0001.xml": "<BODY>내용</BODY>".encode("utf-8")})

        result = extract_snippets(data, [])

        self.assertIsInstance(result, DocumentExtractionResult)
        assert isinstance(result, DocumentExtractionResult)
        self.assertEqual(result.file_names, ["0001.xml"])
        self.assertEqual(result.snippets, [])


class ExtractSnippetsInvalidZipTests(unittest.TestCase):
    def test_invalid_zip_bytes_return_typed_error_not_crash(self) -> None:
        result = extract_snippets(b"this is not a zip file", ["keyword"])

        self.assertIsInstance(result, DocumentExtractionError)
        assert isinstance(result, DocumentExtractionError)
        self.assertIn("오류", result.message)

    def test_empty_bytes_return_typed_error(self) -> None:
        result = extract_snippets(b"", ["keyword"])

        self.assertIsInstance(result, DocumentExtractionError)

    def test_zip_with_no_files_returns_typed_error(self) -> None:
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w"):
            pass

        result = extract_snippets(buffer.getvalue(), ["keyword"])

        self.assertIsInstance(result, DocumentExtractionError)

    def test_invalid_zip_does_not_write_any_file_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            before = set(os.listdir(tmpdir))
            cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                extract_snippets(b"not a zip", ["keyword"])
            finally:
                os.chdir(cwd)
            after = set(os.listdir(tmpdir))
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
