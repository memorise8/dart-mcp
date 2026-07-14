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

from click.testing import CliRunner

# 레포 관례: `dart_search_mcp.tools.*`(-> Task 4의 `emit_topic_cases_from_facts`가
# 지연 import하는 `dart_search_mcp.tools.reports`)를 import하기 전에 `server`를
# 먼저 import해서 `@mcp.tool()` 등록 순서를 보존한다(`tests/test_public_surface.py`가
# 기대하는 정식 순서). `tests/test_audit_facts_adapter.py`/
# `tests/test_temis_export_surface.py`와 동일한 이유.
import server  # noqa: F401
import cli
from dart_search_mcp.tools.extract_facts import (
    ExtractFactsSourceError,
    deserialize_parsed_report,
    emit_topic_cases_from_facts,
    extract_audit_facts,
    select_xml_path,
    serialize_parsed_report,
)
from dart_search_mcp.audit_xml_parser import ParsedAuditReport, parse_audit_xml

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "audit_xml"
_FRESHNESS = "2026-07-01T00:00:00Z"


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

    def test_resume_with_different_docs_dir_raises_value_error(self) -> None:
        """run-params 가드는 corp_cls뿐 아니라 docs_dir이 바뀌어도 걸려야
        한다(다른 필드도 동등하게 보호되는지 확인)."""
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            other_docs_dir = os.path.join(tmp, "docs2")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            os.makedirs(other_docs_dir, exist_ok=True)
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO])
            output_path = os.path.join(tmp, "audit_facts.jsonl")
            checkpoint_path = Path(tmp) / "audit_facts.jsonl.checkpoint.json"

            extract_audit_facts(manifest_path, docs_dir, output_path, checkpoint=checkpoint_path)

            with self.assertRaises(ValueError):
                extract_audit_facts(
                    manifest_path,
                    other_docs_dir,
                    output_path,
                    resume=True,
                    checkpoint=checkpoint_path,
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


class CrashRecoveryTests(unittest.TestCase):
    """리뷰어가 재현한 크래시-중복 시나리오의 회귀 테스트: `_process_one`이
    JSONL에 줄을 flush했지만 호출부가 checkpoint를 저장하기 전에 크래시한
    상황(+ torn 마지막 라인)을 인위적으로 만들고, `--resume`이 이를
    자가복구해 최종 JSONL에 중복행 없이 각 rcept_no가 정확히 한 번씩만
    남는지 검증한다."""

    def test_resume_repairs_torn_line_and_prevents_duplicate_after_crash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO, _META_02_SAMSUNG_FN])
            output_path = os.path.join(tmp, "audit_facts.jsonl")
            checkpoint_path = Path(tmp) / "audit_facts.jsonl.checkpoint.json"

            # 1건(01)만 정상 처리된 실행(체크포인트에 01만 processed로 기록됨).
            extract_audit_facts(
                manifest_path, docs_dir, output_path, limit=1, checkpoint=checkpoint_path
            )
            self.assertEqual(len(_read_jsonl(output_path)), 1)

            # rcept 02를 _process_one이 JSONL에 flush까지는 했지만(물리적으로
            # 존재) 호출부가 checkpoint에 processed로 기록/저장하기 전에
            # 크래시한 상황을 인위적으로 재현한다: JSONL에 02행을 직접
            # append하고 checkpoint는 그대로 둔다(01만 processed에 있음).
            # 그 직후 torn(부분/잘린) 마지막 라인도 하나 심어, 쓰기 도중
            # 크래시한 흔적까지 함께 재현한다.
            xml_bytes = _load_fixture("02_listed_unqualified_with_kam.xml")
            parsed = parse_audit_xml(xml_bytes, meta=_META_02_SAMSUNG_FN, doc_path="dummy")
            row02 = serialize_parsed_report(parsed)
            with open(output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row02, ensure_ascii=False))
                f.write("\n")
                f.write('{"rcept_no": "20250710000299", "corp_name": "잘린')  # torn, 개행 없음

            checkpoint_before = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertNotIn(_META_02_SAMSUNG_FN["rcept_no"], checkpoint_before["processed"])

            summary = extract_audit_facts(
                manifest_path, docs_dir, output_path, resume=True, checkpoint=checkpoint_path
            )

            rows = _read_jsonl(output_path)
            rcept_nos = [r["rcept_no"] for r in rows]
            # 중복행 0: 각 rcept_no 정확히 1회.
            self.assertEqual(len(rcept_nos), len(set(rcept_nos)))
            self.assertEqual(
                set(rcept_nos), {_META_01_ITOMATO["rcept_no"], _META_02_SAMSUNG_FN["rcept_no"]}
            )
            self.assertEqual(len(rows), 2)

            self.assertEqual(summary["total_selected"], 2)
            self.assertEqual(summary["parsed_ok"], 2)
            self.assertEqual(summary["failed"], 0)

            # resume 후 재실행해도(멱등) 중복이 생기지 않아야 한다.
            extract_audit_facts(
                manifest_path, docs_dir, output_path, resume=True, checkpoint=checkpoint_path
            )
            rows_again = _read_jsonl(output_path)
            self.assertEqual(len(rows_again), 2)
            self.assertEqual(
                {r["rcept_no"] for r in rows_again},
                {_META_01_ITOMATO["rcept_no"], _META_02_SAMSUNG_FN["rcept_no"]},
            )

    def test_resume_deduplicates_preexisting_duplicate_lines(self) -> None:
        """(대체 시나리오) 이미 중복 라인이 심어진 JSONL을 --resume하면
        복구 스캔이 이를 병합해 각 rcept_no가 한 번만 남아야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO])
            output_path = os.path.join(tmp, "audit_facts.jsonl")
            checkpoint_path = Path(tmp) / "audit_facts.jsonl.checkpoint.json"

            extract_audit_facts(manifest_path, docs_dir, output_path, checkpoint=checkpoint_path)
            self.assertEqual(len(_read_jsonl(output_path)), 1)

            # 과거 크래시로 이미 중복행이 생겼던 상태를 인위적으로 심는다.
            xml_bytes = _load_fixture("01_unlisted_unqualified_no_kam.xml")
            parsed = parse_audit_xml(xml_bytes, meta=_META_01_ITOMATO, doc_path="dummy")
            row01 = serialize_parsed_report(parsed)
            with open(output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row01, ensure_ascii=False))
                f.write("\n")

            self.assertEqual(len(_read_jsonl(output_path)), 2)

            summary = extract_audit_facts(
                manifest_path, docs_dir, output_path, resume=True, checkpoint=checkpoint_path
            )

            rows = _read_jsonl(output_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["rcept_no"], _META_01_ITOMATO["rcept_no"])
            self.assertEqual(summary["parsed_ok"], 1)


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

    def test_select_xml_path_falls_back_to_embedded_report_xml(self) -> None:
        """00760/00761이 둘 다 없고 `<rcept>.xml`만 있으면(사업보고서 임베드
        케이스) 그 파일을 선택해야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            rcept_no = "RCEPT_EMBED"
            folder = os.path.join(tmp, rcept_no)
            os.makedirs(folder, exist_ok=True)
            embedded_path = os.path.join(folder, f"{rcept_no}.xml")
            with open(embedded_path, "wb") as f:
                f.write(b"<X>embedded</X>")

            chosen = select_xml_path(tmp, rcept_no)

            self.assertIsNotNone(chosen)
            assert chosen is not None
            self.assertEqual(str(chosen), embedded_path)

    def test_select_xml_path_returns_none_for_ambiguous_multiple_xml_files(self) -> None:
        """00760/00761/`<rcept>.xml` 어느 것도 없고 xml이 2개 이상이면(어느
        것을 골라야 할지 모호하면) None(-> 호출자가 no_xml로 격리)이어야
        한다."""
        with tempfile.TemporaryDirectory() as tmp:
            rcept_no = "RCEPT_AMBIGUOUS"
            folder = os.path.join(tmp, rcept_no)
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "a.xml"), "wb") as f:
                f.write(b"<X>a</X>")
            with open(os.path.join(folder, "b.xml"), "wb") as f:
                f.write(b"<X>b</X>")

            self.assertIsNone(select_xml_path(tmp, rcept_no))


class SourceLoadingTests(unittest.TestCase):
    def test_missing_records_key_raises_source_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"not_records": []}, f)

            with self.assertRaises(ExtractFactsSourceError):
                extract_audit_facts(path, os.path.join(tmp, "docs"), os.path.join(tmp, "out.jsonl"))


# ---------------------------------------------------------------------------
# Task 4: deserialize_parsed_report / emit_topic_cases_from_facts
# ---------------------------------------------------------------------------


def _null_fiscal_year_parsed(rcept_no: str) -> ParsedAuditReport:
    """fiscal_year를 유도할 수 없는 사실(fact)을 인위적으로 만든다 - 실제
    fixture는 report_name/XML 본문/rcept_dt 중 하나로 항상 fiscal_year를
    유도할 수 있으므로, null 케이스는 직접 구성해야 한다."""
    return ParsedAuditReport(
        rcept_no=rcept_no,
        corp_code="00999999",
        corp_name="연도미상",
        corp_cls="E",
        stock_code="",
        report_name="감사보고서",
        rcept_dt="",
        category="감사보고서",
        fiscal_year=None,
        settlement_month=None,
        auditor="어느회계법인",
        audit_opinion="적정",
        opinion_snippet="",
        going_concern=False,
        going_concern_snippet="",
        kam_present=False,
        kam_raw="",
        emphasis_raw="",
        kam_tags=(),
        parse_flags=frozenset(),
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        doc_path="",
    )


class DeserializeRoundTripTests(unittest.TestCase):
    def test_serialize_then_deserialize_round_trips_for_representative_report(self) -> None:
        xml_bytes = _load_fixture("02_listed_unqualified_with_kam.xml")
        parsed = parse_audit_xml(xml_bytes, meta=_META_02_SAMSUNG_FN, doc_path="fixtures/02.xml")

        row = serialize_parsed_report(parsed)
        # 실제 JSONL 파이프라인과 동일하게 JSON 왕복(dumps/loads)까지 거친다.
        row_via_json = json.loads(json.dumps(row, ensure_ascii=False))
        restored = deserialize_parsed_report(row_via_json)

        self.assertEqual(restored, parsed)
        self.assertIsInstance(restored.parse_flags, frozenset)
        self.assertIsInstance(restored.kam_tags, tuple)

    def test_round_trip_preserves_none_fiscal_year_and_settlement_month(self) -> None:
        parsed = _null_fiscal_year_parsed("R_NULL_FY")

        row = json.loads(json.dumps(serialize_parsed_report(parsed), ensure_ascii=False))
        restored = deserialize_parsed_report(row)

        self.assertEqual(restored, parsed)
        self.assertIsNone(restored.fiscal_year)
        self.assertIsNone(restored.settlement_month)

    def test_deserialize_is_lenient_with_missing_keys(self) -> None:
        restored = deserialize_parsed_report({"rcept_no": "R1"})

        self.assertEqual(restored.rcept_no, "R1")
        self.assertEqual(restored.corp_code, "")
        self.assertIsNone(restored.fiscal_year)
        self.assertIsNone(restored.settlement_month)
        self.assertEqual(restored.parse_flags, frozenset())
        self.assertEqual(restored.kam_tags, ())
        self.assertFalse(restored.going_concern)
        self.assertFalse(restored.kam_present)


_EXPECTED_TOPIC_CASE_FIELDS = {
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


class EmitTopicCasesFromFactsTests(unittest.TestCase):
    def _build_mixed_facts_jsonl(self, tmp: str) -> str:
        """적정+KAM 있음(02) / 적정+KAM 없음·E(01) / 의견거절(06)의 실제
        facts + fiscal_year가 null인 인위적 행 1건 + 손상된 라인 1건을 섞은
        facts.jsonl을 만든다."""
        docs_dir = os.path.join(tmp, "docs")
        _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
        _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
        _write_doc(docs_dir, _META_06_BFLABS["rcept_no"], "00760", _load_fixture("06_disclaimer_of_opinion.xml"))
        manifest_path = _write_manifest(tmp, [_META_01_ITOMATO, _META_02_SAMSUNG_FN, _META_06_BFLABS])
        facts_path = os.path.join(tmp, "audit_facts.jsonl")
        extract_audit_facts(manifest_path, docs_dir, facts_path)
        self.assertEqual(len(_read_jsonl(facts_path)), 3)

        null_fy_row = serialize_parsed_report(_null_fiscal_year_parsed("20260101999999"))
        with open(facts_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(null_fy_row, ensure_ascii=False))
            f.write("\n")
            f.write('{"rcept_no": "손상됨", "corp_code": ')  # 손상 라인(파싱 불가), 개행 없음
            f.write("\n")

        return facts_path

    def test_emit_builds_valid_topic_cases_with_mixed_scenarios(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = self._build_mixed_facts_jsonl(tmp)
            output_path = os.path.join(tmp, "dart_topic_cases.json")

            summary = emit_topic_cases_from_facts(facts_path, output_path, freshness_timestamp=_FRESHNESS)

            # 3건(실제) + 1건(fiscal_year null) = 4행이 유효하게 역직렬화됨.
            self.assertEqual(summary["facts_rows"], 4)
            # 손상 라인 1건은 별도 집계되고 emit을 멈추지 않는다.
            self.assertEqual(summary["corrupt_lines"], 1)
            # fiscal_year null 행은 어댑터가 skip으로 집계한다.
            self.assertEqual(summary["skipped"], 1)
            self.assertTrue(summary["skipped_reasons_count"])
            # 나머지 3건(01,02,06)은 모두 유효한 topic case가 된다.
            self.assertEqual(summary["topic_cases"], 3)

            self.assertTrue(os.path.isfile(output_path))
            with open(output_path, encoding="utf-8") as f:
                records = json.load(f)

            self.assertEqual(len(records), 3)
            report_ids = {r["report_id"] for r in records}
            self.assertEqual(
                report_ids,
                {_META_01_ITOMATO["rcept_no"], _META_02_SAMSUNG_FN["rcept_no"], _META_06_BFLABS["rcept_no"]},
            )

            for record in records:
                # finov2 DartTopicCase 스키마와 필드 1:1 (temis_export.DartTopicCaseRecord 참고).
                self.assertEqual(set(record.keys()), _EXPECTED_TOPIC_CASE_FIELDS)
                self.assertIsInstance(record["company_identifier"], str)
                self.assertIsInstance(record["fiscal_year"], int)
                self.assertIsInstance(record["topic_tags"], list)
                self.assertIsInstance(record["extraction_confidence"], float)
                self.assertEqual(record["freshness_timestamp"], _FRESHNESS)

            # KAM 없는 E 케이스(01)도 topic 키워드 매칭 없이 general 케이스
            # 1건으로 정상 생성돼야 한다(어댑터/변환기가 이미 보장하는 동작).
            itomato_case = next(r for r in records if r["report_id"] == _META_01_ITOMATO["rcept_no"])
            self.assertEqual(itomato_case["topic_tags"], [])
            self.assertIn("-general-", itomato_case["case_id"])

    def test_emit_is_atomic_write_no_partial_file_left_behind_by_tmp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = self._build_mixed_facts_jsonl(tmp)
            output_path = os.path.join(tmp, "dart_topic_cases.json")

            emit_topic_cases_from_facts(facts_path, output_path, freshness_timestamp=_FRESHNESS)

            self.assertTrue(os.path.isfile(output_path))
            self.assertFalse(os.path.isfile(output_path + ".tmp"))


class FreshnessInjectionTests(unittest.TestCase):
    def test_freshness_timestamp_is_injected_not_read_from_clock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO])
            facts_path = os.path.join(tmp, "audit_facts.jsonl")
            extract_audit_facts(manifest_path, docs_dir, facts_path)

            output_path = os.path.join(tmp, "topic_cases.json")
            fixed_ts = "2020-01-01T00:00:00Z"

            summary = emit_topic_cases_from_facts(facts_path, output_path, freshness_timestamp=fixed_ts)
            self.assertEqual(summary["topic_cases"], 1)

            with open(output_path, encoding="utf-8") as f:
                records = json.load(f)
            self.assertTrue(records)
            for record in records:
                self.assertEqual(record["freshness_timestamp"], fixed_ts)


class CorruptLineDefenseTests(unittest.TestCase):
    def test_corrupt_line_is_skipped_and_counted_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO])
            facts_path = os.path.join(tmp, "audit_facts.jsonl")
            extract_audit_facts(manifest_path, docs_dir, facts_path)

            with open(facts_path, "a", encoding="utf-8") as f:
                f.write('{"rcept_no": "잘린 라인", "corp_code":')  # torn/무효 JSON, 개행 없음
                f.write("\n")
                f.write("이것도 JSON이 아니다\n")

            output_path = os.path.join(tmp, "topic_cases.json")
            summary = emit_topic_cases_from_facts(facts_path, output_path, freshness_timestamp=_FRESHNESS)

            self.assertEqual(summary["facts_rows"], 1)
            self.assertEqual(summary["corrupt_lines"], 2)
            self.assertEqual(summary["topic_cases"], 1)
            self.assertTrue(os.path.isfile(output_path))


class CliEmitTopicCasesTests(unittest.TestCase):
    def test_cli_extract_audit_facts_with_emit_topic_cases_writes_both_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            _write_doc(docs_dir, _META_02_SAMSUNG_FN["rcept_no"], "00760", _load_fixture("02_listed_unqualified_with_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO, _META_02_SAMSUNG_FN])
            output_path = os.path.join(tmp, "audit_facts.jsonl")
            topic_cases_path = os.path.join(tmp, "dart_topic_cases.json")

            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                [
                    "extract-audit-facts",
                    "--manifest", manifest_path,
                    "--docs-dir", docs_dir,
                    "-o", output_path,
                    "--emit-topic-cases", topic_cases_path,
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(os.path.isfile(output_path))
            self.assertTrue(os.path.isfile(topic_cases_path))
            self.assertIn("topic_cases 생성 완료", result.output)

            with open(topic_cases_path, encoding="utf-8") as f:
                records = json.load(f)
            self.assertEqual(len(records), 2)

    def test_cli_extract_audit_facts_without_emit_topic_cases_only_writes_jsonl(self) -> None:
        """`--emit-topic-cases` 없이는 기존 동작 그대로(팩트만) 유지돼야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = os.path.join(tmp, "docs")
            _write_doc(docs_dir, _META_01_ITOMATO["rcept_no"], "00760", _load_fixture("01_unlisted_unqualified_no_kam.xml"))
            manifest_path = _write_manifest(tmp, [_META_01_ITOMATO])
            output_path = os.path.join(tmp, "audit_facts.jsonl")

            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                ["extract-audit-facts", "--manifest", manifest_path, "--docs-dir", docs_dir, "-o", output_path],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertTrue(os.path.isfile(output_path))
            self.assertNotIn("topic_cases", result.output)


if __name__ == "__main__":
    unittest.main()
