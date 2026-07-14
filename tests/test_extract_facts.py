"""`dart_search_mcp.tools.extract_facts`(체크포인트 가능한 대량 감사 사실
추출: `dart extract-audit-facts`)에 대한 테스트.

Task 1이 이미 검증한 `parse_audit_xml`의 파싱 로직 자체는 다시 검증하지
않는다 - 여기서는 `tests/fixtures/audit_xml/*.xml`(Task 1 픽스처)을 재사용해
manifest+docs 폴더를 흉내낸 소형 임시 디렉토리를 만들고, 이 모듈의 루프
동작(XML 선택 우선순위/JSONL 직렬화/예외 격리/no_xml 격리/체크포인트-재개/
corp-cls 필터/summary 집계)만 검증한다. 실제 OpenDART로는 어떤 요청도 나가지
않는다(전부 로컬 파일 I/O)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dart_search_mcp.tools.extract_facts import (
    ExtractFactsSourceError,
    extract_audit_facts,
    select_xml_path,
    serialize_parsed_report,
)
from dart_search_mcp.audit_xml_parser import parse_audit_xml

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "audit_xml"


def _load_fixture(name: str) -> bytes:
    return (_FIXTURES_DIR / name).read_bytes()


# `tests/test_audit_xml_parser.py`의 실제 meta 값(= dart_collected/manifest.json
# records[] 항목을 그대로 옮긴 것)을 그대로 재사용한다 - 조작하지 않는다.
_META_01_ITOMATO = {  # 01_unlisted_unqualified_no_kam.xml, corp_cls=E, 적정, kam 없음
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

_META_02_SAMSUNG_FN = {  # 02_listed_unqualified_with_kam.xml, corp_cls=Y, 적정, kam 있음
    "category": "사업보고서",
    "report_name": "사업보고서 (2025.04)",
    "rcept_no": "20250710000299",
    "rcept_dt": "20250710",
    "corp_code": "01688896",
    "corp_name": "삼성FN리츠",
    "stock_code": "448730",
    "corp_cls": "Y",
    "flr_nm": "삼성FN리츠",
    "remark": "",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250710000299",
}

_META_04_BLPHARMTECH = {  # 04_going_concern_material_uncertainty.xml, corp_cls=K, going_concern=True
    "category": "사업보고서",
    "report_name": "사업보고서 (2025.12)",
    "rcept_no": "20260324000004",
    "rcept_dt": "20260324",
    "corp_code": "00384717",
    "corp_name": "비엘팜텍",
    "stock_code": "065170",
    "corp_cls": "K",
    "flr_nm": "비엘팜텍",
    "remark": "연",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260324000004",
}

_META_05_BACARDI = {  # 05_qualified_opinion.xml, corp_cls=E, 한정
    "category": "감사보고서",
    "report_name": "감사보고서 (2025.03)",
    "rcept_no": "20250701000289",
    "rcept_dt": "20250701",
    "corp_code": "00983697",
    "corp_name": "바카디코리아",
    "stock_code": "",
    "corp_cls": "E",
    "flr_nm": "대주회계법인",
    "remark": "",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250701000289",
}

_META_06_BFLABS = {  # 06_disclaimer_of_opinion.xml, corp_cls=E, 의견거절
    "category": "사업보고서",
    "report_name": "사업보고서 (2025.12)",
    "rcept_no": "20260324000003",
    "rcept_dt": "20260324",
    "corp_code": "00656021",
    "corp_name": "비에프랩스",
    "stock_code": "139050",
    "corp_cls": "E",
    "flr_nm": "비에프랩스",
    "remark": "연",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260324000003",
}

_META_07_ELAPHONISI = {  # 07_adverse_opinion.xml, corp_cls=E, 부적정
    "category": "감사보고서",
    "report_name": "[기재정정]감사보고서 (2024.12)",
    "rcept_no": "20250918000376",
    "rcept_dt": "20250918",
    "corp_code": "01668333",
    "corp_name": "엘라포니시",
    "stock_code": "",
    "corp_cls": "E",
    "flr_nm": "중정회계법인",
    "remark": "",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250918000376",
}


def _write_manifest(tmp: str, records: list[dict]) -> str:
    path = os.path.join(tmp, "manifest.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"records": records}, f, ensure_ascii=False)
    return path


def _write_doc(docs_dir: str, rcept_no: str, acode: str, xml_bytes: bytes) -> str:
    folder = os.path.join(docs_dir, rcept_no)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{rcept_no}_{acode}.xml")
    with open(path, "wb") as f:
        f.write(xml_bytes)
    return path


def _read_jsonl(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class HappyPathTests(unittest.TestCase):
    def test_normal_run_writes_one_line_per_rcept_with_correct_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
            _write_doc(docs_dir, _META_05_BACARDI["rcept_no"], "00760", _load_fixture("05_qualified_opinion.xml"))

            manifest_path = _write_manifest(
                tmp, [_META_01_ITOMATO, _META_02_SAMSUNG_FN, _META_05_BACARDI]
            )
            output_path = os.path.join(tmp, "audit_facts.jsonl")

            summary = extract_audit_facts(manifest_path, docs_dir, output_path)

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 3)
            self.assertEqual({r["rcept_no"] for r in rows}, {
                _META_01_ITOMATO["rcept_no"], _META_02_SAMSUNG_FN["rcept_no"], _META_05_BACARDI["rcept_no"]
            })

            row01 = next(r for r in rows if r["rcept_no"] == _META_01_ITOMATO["rcept_no"])
            self.assertEqual(row01["corp_name"], "이토마토")
            self.assertEqual(row01["audit_opinion"], "적정")
            self.assertIsInstance(row01["parse_flags"], list)
            self.assertIsInstance(row01["kam_tags"], list)

            self.assertEqual(summary["total_selected"], 3)
            self.assertEqual(summary["parsed_ok"], 3)
            self.assertEqual(summary["failed"], 0)

            summary_path = output_path + ".summary.json"
            self.assertTrue(os.path.isfile(summary_path))
            with open(summary_path, encoding="utf-8") as f:
                on_disk_summary = json.load(f)
            self.assertEqual(on_disk_summary, summary)


class JsonlSerializationTests(unittest.TestCase):
    def test_line_round_trips_with_list_flags_and_none_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_02_SAMSUNG_FN])
            output_path = os.path.join(tmp, "audit_facts.jsonl")

            extract_audit_facts(manifest_path, docs_dir, output_path)

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            row = rows[0]
            # parse_flags/kam_tags must be JSON arrays (lists), not sets/tuples.
            self.assertIsInstance(row["parse_flags"], list)
            self.assertIsInstance(row["kam_tags"], list)
            self.assertEqual(row["parse_flags"], sorted(row["parse_flags"]))
            # kam_present True 이므로 kam 플래그가 있어야 한다.
            self.assertIn("kam", row["parse_flags"])
            self.assertTrue(row["kam_present"])
            # fiscal_year/settlement_month는 int 또는 None이어야 한다(둘 다 JSON 유효).
            self.assertTrue(row["fiscal_year"] is None or isinstance(row["fiscal_year"], int))

    def test_serialize_parsed_report_converts_frozenset_and_tuple(self) -> None:
        xml_bytes = _load_fixture("01_unlisted_unqualified_no_kam.xml")
        parsed = parse_audit_xml(xml_bytes, meta=_META_01_ITOMATO, doc_path="fixtures/01.xml")

        row = serialize_parsed_report(parsed)

        self.assertIsInstance(row["parse_flags"], list)
        self.assertIsInstance(row["kam_tags"], list)
        # JSON 직렬화가 실제로 되는지(예외 없이) 확인한다.
        json.dumps(row, ensure_ascii=False)


class ExceptionIsolationTests(unittest.TestCase):
    def test_one_rcept_raising_does_not_stop_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO, _META_02_SAMSUNG_FN])
            output_path = os.path.join(tmp, "audit_facts.jsonl")

            real_parse = parse_audit_xml

            def flaky_parse(xml_bytes, meta, *, doc_path=""):
                if meta.get("rcept_no") == _META_01_ITOMATO["rcept_no"]:
                    raise ValueError("시뮬레이션된 파싱 오류")
                return real_parse(xml_bytes, meta, doc_path=doc_path)

            with patch("dart_search_mcp.tools.extract_facts.parse_audit_xml", flaky_parse):
                summary = extract_audit_facts(manifest_path, docs_dir, output_path)

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rcept_no"], _META_02_SAMSUNG_FN["rcept_no"])

            self.assertEqual(summary["total_selected"], 2)
            self.assertEqual(summary["parsed_ok"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["by_error_kind"], {"ValueError": 1})
            self.assertEqual(summary["error_samples"]["ValueError"], [_META_01_ITOMATO["rcept_no"]])


class NoXmlTests(unittest.TestCase):
    def test_missing_docs_folder_is_isolated_as_no_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            os.makedirs(docs_dir, exist_ok=True)
            _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
            # _META_01_ITOMATO에 대응하는 docs 폴더는 아예 만들지 않는다.
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO, _META_02_SAMSUNG_FN])
            output_path = os.path.join(tmp, "audit_facts.jsonl")

            summary = extract_audit_facts(manifest_path, docs_dir, output_path)

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rcept_no"], _META_02_SAMSUNG_FN["rcept_no"])

            self.assertEqual(summary["total_selected"], 2)
            self.assertEqual(summary["parsed_ok"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["by_error_kind"], {"no_xml": 1})
            self.assertEqual(summary["error_samples"]["no_xml"], [_META_01_ITOMATO["rcept_no"]])

    def test_select_xml_path_returns_none_for_missing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(select_xml_path(tmp, "NOSUCHRCEPT"))


class ResumeTests(unittest.TestCase):
    def test_resume_skips_already_processed_and_avoids_duplicate_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            # _META_02_SAMSUNG_FN은 no_xml(폴더 없음)로 처리된다.
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO, _META_02_SAMSUNG_FN])
            output_path = os.path.join(tmp, "audit_facts.jsonl")
            checkpoint_path = Path(tmp) / "audit_facts.jsonl.checkpoint.json"

            first_summary = extract_audit_facts(
                manifest_path, docs_dir, output_path, checkpoint=checkpoint_path
            )
            self.assertEqual(first_summary["parsed_ok"], 1)
            self.assertEqual(first_summary["failed"], 1)
            self.assertEqual(len(_read_jsonl(output_path)), 1)

            second_summary = extract_audit_facts(
                manifest_path, docs_dir, output_path, resume=True, checkpoint=checkpoint_path
            )

            # 재개 시 이미 처리된 rcept는 재기록되지 않아야 한다(중복행 0).
            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(second_summary["total_selected"], 2)
            self.assertEqual(second_summary["parsed_ok"], 1)
            self.assertEqual(second_summary["failed"], 1)
            self.assertEqual(second_summary["by_error_kind"], {"no_xml": 1})

    def test_resume_with_different_run_params_raises_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO])
            output_path = os.path.join(tmp, "audit_facts.jsonl")
            checkpoint_path = Path(tmp) / "audit_facts.jsonl.checkpoint.json"

            extract_audit_facts(manifest_path, docs_dir, output_path, checkpoint=checkpoint_path)

            with self.assertRaises(ValueError):
                extract_audit_facts(
                    manifest_path,
                    docs_dir,
                    output_path,
                    resume=True,
                    checkpoint=checkpoint_path,
                    corp_cls={"E"},
                )

    def test_no_resume_ignores_stale_checkpoint_and_starts_fresh(self) -> None:
        """`resume=False`(기본값)면 체크포인트에 이전 실행의 다른
        run-params가 남아 있어도 오류 없이 새로 시작해야 한다(과거 실행
        찌꺼기가 이번 실행을 막지 않는다)."""
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO])
            output_path = os.path.join(tmp, "audit_facts.jsonl")
            checkpoint_path = Path(tmp) / "audit_facts.jsonl.checkpoint.json"

            extract_audit_facts(
                manifest_path, docs_dir, output_path, checkpoint=checkpoint_path, corp_cls={"Y"}
            )
            # corp_cls={"Y"}이므로 E 레코드는 선택되지 않아 output은 비어 있다.
            self.assertEqual(_read_jsonl(output_path), [])

            summary = extract_audit_facts(manifest_path, docs_dir, output_path, checkpoint=checkpoint_path)

            self.assertEqual(summary["total_selected"], 1)
            self.assertEqual(summary["parsed_ok"], 1)
            self.assertEqual(len(_read_jsonl(output_path)), 1)


class CorpClsFilterTests(unittest.TestCase):
    def test_corp_cls_filter_excludes_other_classes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO, _META_02_SAMSUNG_FN])
            output_path = os.path.join(tmp, "audit_facts.jsonl")

            summary = extract_audit_facts(manifest_path, docs_dir, output_path, corp_cls={"E"})

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rcept_no"], _META_01_ITOMATO["rcept_no"])
            self.assertEqual(summary["total_selected"], 1)
            self.assertEqual(summary["by_corp_cls"], {"E": 1})


class SummaryAggregationTests(unittest.TestCase):
    def test_opinion_distribution_going_concern_kam_and_by_corp_cls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
            _write_doc(docs_dir, _META_04_BLPHARMTECH["rcept_no"], "00760", _load_fixture("04_going_concern_material_uncertainty.xml"))
            _write_doc(docs_dir, _META_05_BACARDI["rcept_no"], "00760", _load_fixture("05_qualified_opinion.xml"))
            _write_doc(docs_dir, _META_06_BFLABS["rcept_no"], "00760", _load_fixture("06_disclaimer_of_opinion.xml"))
            _write_doc(docs_dir, _META_07_ELAPHONISI["rcept_no"], "00760", _load_fixture("07_adverse_opinion.xml"))

            manifest_path = _write_manifest(
                tmp,
                [
                    _META_01_ITOMATO,
                    _META_02_SAMSUNG_FN,
                    _META_04_BLPHARMTECH,
                    _META_05_BACARDI,
                    _META_06_BFLABS,
                    _META_07_ELAPHONISI,
                ],
            )
            output_path = os.path.join(tmp, "audit_facts.jsonl")

            summary = extract_audit_facts(manifest_path, docs_dir, output_path)

            self.assertEqual(summary["total_selected"], 6)
            self.assertEqual(summary["parsed_ok"], 6)
            self.assertEqual(summary["failed"], 0)

            self.assertEqual(summary["opinion_distribution"]["적정"], 3)  # 01, 02, 04(계속기업 불확실성이지만 의견은 적정)
            self.assertEqual(summary["opinion_distribution"]["한정"], 1)  # 05
            self.assertEqual(summary["opinion_distribution"]["의견거절"], 1)  # 06
            self.assertEqual(summary["opinion_distribution"]["부적정"], 1)  # 07

            self.assertEqual(summary["going_concern_true"], 2)  # 04, 06(의견거절근거 문단 내 계속기업 불확실성)
            self.assertEqual(summary["kam_present"], 2)  # 02, 04

            self.assertEqual(
                summary["by_corp_cls"],
                {"E": 4, "Y": 1, "K": 1},  # 01,05,06,07=E / 02=Y / 04=K
            )


class XmlPriorityTests(unittest.TestCase):
    def test_prefers_consolidated_00761_over_audit_00760(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            rcept_no = _META_01_ITOMATO["rcept_no"]
            # 00760에는 kam이 없는 픽스처(01), 00761에는 kam이 있는 픽스처(02)를 넣어
            # 실제로 00761이 선택됐는지 kam_present로 구분한다.
            _write_doc(docs_dir, rcept_no, "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            _write_doc(docs_dir, rcept_no, "00761", _load_fixture("02_listed_unqualified_with_kam.xml"))

            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO])
            output_path = os.path.join(tmp, "audit_facts.jsonl")

            summary = extract_audit_facts(manifest_path, docs_dir, output_path)

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["doc_path"].endswith(f"{rcept_no}_00761.xml"))
            self.assertTrue(rows[0]["kam_present"])
            self.assertEqual(summary["kam_present"], 1)

    def test_select_xml_path_prefers_00761(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rcept_no = "RCEPT1"
            _write_doc(tmp, rcept_no, "00760", b"<X>audit</X>")
            _write_doc(tmp, rcept_no, "00761", b"<X>consolidated</X>")

            chosen = select_xml_path(tmp, rcept_no)

            self.assertIsNotNone(chosen)
            assert chosen is not None
            self.assertEqual(chosen.name, f"{rcept_no}_00761.xml")


class SourceLoadingTests(unittest.TestCase):
    def test_missing_records_key_raises_source_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"not_records": []}, f)

            with self.assertRaises(ExtractFactsSourceError):
                extract_audit_facts(path, os.path.join(tmp, "docs"), os.path.join(tmp, "out.jsonl"))


if __name__ == "__main__":
    unittest.main()
