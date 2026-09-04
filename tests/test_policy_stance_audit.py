import json
import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.tools.policy_stance_audit import run_policy_stance_audit
from dart_search_mcp.tools.policy_stance_cli import run_extract_policy_stances

FIX = Path(__file__).parent / "fixtures" / "policy"


def _write_doc(docs_dir: Path, rcept_no: str, body: str) -> None:
    folder = docs_dir / rcept_no
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{rcept_no}_00760.xml").write_text(
        f"<DOCUMENT><BODY><P>{body}</P></BODY></DOCUMENT>", encoding="utf-8"
    )


def _write_stances(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


class RealExtractAuditTests(unittest.TestCase):
    """픽스처 XML 2건으로 실제 extract 를 돌린 jsonl 은 evidence_in_block·pattern_in_evidence
    위반이 없어야 한다(R1 evidence_sentence 수정과 규칙 패턴이 서로 맞는지 교차 검증)."""

    def test_픽스처로_추출한_jsonl은_evidence_in_block과_pattern_in_evidence_위반이_0이다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = t / "policy_stance.jsonl"
            run_extract_policy_stances(
                facts_path=FIX / "audit_facts.jsonl",
                docs_dir=FIX / "docs",
                output_path=stances,
                topics=("STD_EARLY_ADOPTION", "PPE_MEASUREMENT", "REVENUE_METHOD", "IP_MEASUREMENT"),
                use_llm=False,
            )
            rep = run_policy_stance_audit(
                stances_path=stances, docs_dir=FIX / "docs", report_path=t / "audit.md"
            )
            self.assertEqual(rep["totals"]["evidence_in_block"]["violations"], 0)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["violations"], 0)
            self.assertTrue((t / "audit.json").exists())


class SyntheticFlagTests(unittest.TestCase):
    """손으로 만든 jsonl + 손으로 만든 XML 로 다섯 플래그를 각각 정확히 1건씩만 잡는지 확인한다.
    각 행은 자신이 겨냥한 플래그 하나만 위반하고 나머지 네 플래그는 통과하도록 설계했다."""

    def test_다섯_플래그가_각각_1건씩_잡힌다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"

            # R1 — evidence_in_block 위반: "판정 불가: " 뒤 텍스트가 원문에 없다.
            _write_doc(docs, "R1", "본문 텍스트입니다.")
            # R2 — table_fragment 위반: 나머지(evidence_in_block·pattern_in_evidence)는 통과.
            r2_evidence = "유형자산은 원가모형을 적용합니다. 장부금액은 1,234,567 원 미만이며 2,345,678 원 이상입니다."
            _write_doc(docs, "R2", r2_evidence)
            # R3 — foreign_standard 위반: subject(1109) 와 다른 기준서 번호(1118) 가 evidence 에 있다.
            r3_evidence = "회사는 제1118호를 아직 채택하지 않았으며 영향을 평가 중입니다."
            _write_doc(docs, "R3", r3_evidence)
            # R4 — pattern_in_evidence 위반: value(OVER_TIME) 를 내는 패턴이 evidence 에 없다.
            r4_evidence = "당사는 계약을 검토하고 있습니다."
            _write_doc(docs, "R4", r4_evidence)
            # R5 — capped 위반: evidence 길이가 400자 이상.
            r5_evidence = "투자부동산에 대하여 원가모형을 적용합니다. " + "추가 설명 문장입니다. " * 30
            self.assertGreaterEqual(len(r5_evidence), 400)
            _write_doc(docs, "R5", r5_evidence)

            rows = [
                {
                    "rcept_no": "R1", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "UNDETERMINED",
                    "evidence": "판정 불가: 이 문구는 문서에 전혀 없습니다", "method": "none",
                    "confidence": 0.0, "block_offset": 0, "parser_version": "ps-v1",
                },
                {
                    "rcept_no": "R2", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "PPE_MEASUREMENT", "subject": None, "value": "COST_MODEL",
                    "evidence": r2_evidence, "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
                {
                    "rcept_no": "R3", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "STD_EARLY_ADOPTION", "subject": "1109", "value": "NOT_ADOPTED",
                    "evidence": r3_evidence, "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
                {
                    "rcept_no": "R4", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "REVENUE_METHOD", "subject": None, "value": "OVER_TIME",
                    "evidence": r4_evidence, "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
                {
                    "rcept_no": "R5", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "IP_MEASUREMENT", "subject": None, "value": "COST_MODEL",
                    "evidence": r5_evidence, "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            report = t / "audit.md"
            rep = run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=report)

            self.assertEqual(rep["totals"]["evidence_in_block"]["violations"], 1)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["violations"], 1)
            self.assertEqual(rep["totals"]["foreign_standard"]["violations"], 1)
            self.assertEqual(rep["totals"]["table_fragment"]["violations"], 1)
            self.assertEqual(rep["totals"]["capped"]["violations"], 1)

            self.assertEqual(rep["violations"]["evidence_in_block"], [["R1", "STD_EARLY_ADOPTION", "1118"]])
            self.assertEqual(rep["violations"]["table_fragment"], [["R2", "PPE_MEASUREMENT", None]])
            self.assertEqual(rep["violations"]["foreign_standard"], [["R3", "STD_EARLY_ADOPTION", "1109"]])
            self.assertEqual(rep["violations"]["pattern_in_evidence"], [["R4", "REVENUE_METHOD", None]])
            self.assertEqual(rep["violations"]["capped"], [["R5", "IP_MEASUREMENT", None]])

            self.assertTrue(report.exists())
            md = report.read_text(encoding="utf-8")
            for flag in ("evidence_in_block", "pattern_in_evidence", "foreign_standard", "table_fragment", "capped"):
                self.assertIn(flag, md)
            self.assertIn("R1", md)
            self.assertIn("R4", md)

            json_path = report.with_suffix(".json")
            self.assertTrue(json_path.exists())
            data = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["violations"]["capped"], [["R5", "IP_MEASUREMENT", None]])


class ComboAuditTests(unittest.TestCase):
    """Q-I3: 콤보값(MIXED/BOTH) 행은 evidence(한 문장)가 아니라 블록 창 전체에서 서로 다른 값
    2개를 찾아야 하고, 리포트에서 (topic, "combo") 로 따로 묶여야 한다."""

    def test_두_모형_언급이_400자_넘게_떨어져도_블록_창_전체로_보면_위반이_아니다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            filler = "추가 설명 문장입니다. " * 40  # 400자 넘게 벌려 둔다
            body = (
                "유형자산 당사는 토지 및 건물에 대하여 재평가모형을 적용하고 있습니다. "
                + filler
                + "그 밖의 유형자산에 대하여는 원가모형을 적용하고 있습니다."
            )
            self.assertGreater(len(filler), 400)
            _write_doc(docs, "C1", body)

            rows = [
                {
                    "rcept_no": "C1", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "PPE_MEASUREMENT", "subject": None, "value": "MIXED",
                    # evidence 는 judge() 가 실제로 저장하는 것처럼 승격 근거가 된 문장 하나뿐이다 —
                    # 여기엔 재평가모형만 있고 원가모형은 400자 밖에 있다.
                    "evidence": "유형자산 당사는 토지 및 건물에 대하여 재평가모형을 적용하고 있습니다.",
                    "method": "rule", "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            rep = run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=t / "audit.md")

            self.assertEqual(rep["totals"]["pattern_in_evidence"]["violations"], 0)
            self.assertIn("PPE_MEASUREMENT|combo", rep["by_group"])
            self.assertNotIn("PPE_MEASUREMENT|rule", rep["by_group"])
            combo_group = rep["by_group"]["PPE_MEASUREMENT|combo"]
            self.assertEqual(combo_group["method"], "combo")
            self.assertEqual(combo_group["flags"]["pattern_in_evidence"]["violations"], 0)
            self.assertEqual(combo_group["flags"]["pattern_in_evidence"]["checked"], 1)

    def test_한_모형만_블록에_있으면_콤보_위반이다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            body = "유형자산 당사는 토지 및 건물에 대하여 재평가모형을 적용하고 있습니다."
            _write_doc(docs, "C2", body)
            rows = [
                {
                    "rcept_no": "C2", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "PPE_MEASUREMENT", "subject": None, "value": "MIXED",
                    "evidence": body, "method": "rule", "confidence": 1.0,
                    "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            rep = run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=t / "audit.md")
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["violations"], 1)
            self.assertEqual(rep["violations"]["pattern_in_evidence"], [["C2", "PPE_MEASUREMENT", None]])


if __name__ == "__main__":
    unittest.main()
