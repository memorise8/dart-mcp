import json
import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.tools.policy_stance_audit import (
    PolicyStanceAuditRowError,
    _pattern_in_evidence_llm,
    run_policy_stance_audit,
)
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


    def test_목록_서문에서_온_근거도_원문_확인을_통과한다(self) -> None:
        """T4: 항목이 침묵하면 근거는 블록 **앞**의 목록 서문이다 — evidence_in_block 의 창이
        앞쪽으로도 열려 있어야 그 근거가 "원문에 없다"로 잘못 잡히지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            rcept = "20260001000009"
            _write_doc(
                docs, rcept,
                "2.2 회계정책과 공시의 변경 2.2.1 회사가 채택한 제ㆍ개정 기준서 및 해석서 회사는 "
                "2025년 1월 1일로 개시하는 회계기간부터 다음의 제ㆍ개정 기준서 및 해석서를 신규로 "
                "적용하였습니다. (1) 기업회계기준서 제1021호 '환율변동효과' 개정 교환가능성 평가에 "
                "관한 지침을 추가하였습니다.",
            )
            facts = t / "audit_facts.jsonl"
            facts.write_text(
                json.dumps({"rcept_no": rcept, "corp_code": "9", "fiscal_year": "2025"}) + "\n",
                encoding="utf-8",
            )
            stances = t / "policy_stance.jsonl"
            run_extract_policy_stances(
                facts_path=facts, docs_dir=docs, output_path=stances,
                topics=("STD_EARLY_ADOPTION",), use_llm=False,
            )
            rows = [json.loads(line) for line in stances.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual([r["value"] for r in rows], ["ADOPTED"])
            self.assertIn("신규로 적용하였습니다", rows[0]["evidence"])
            rep = run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=t / "audit.md")
            self.assertEqual(rep["totals"]["evidence_in_block"]["violations"], 0)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["violations"], 0)


class EvidenceWindowTests(unittest.TestCase):
    """Fix round 1(Important 1): 블록 **앞** 창은 목록 서문에서 온 행(sentence_kind ==
    "list_preamble")에만 열린다 — 나머지 행에서 앞쪽 창을 열면 이웃 항목 오귀속을 잡는
    유일한 자동 검사에 사각지대가 생긴다."""

    def _doc_with_lead(self, tmp: Path) -> tuple[Path, str, int]:
        from dart_search_mcp.audit_xml_parser import _strip_tags_to_text

        lead = "2.2.1 회사가 채택한 제ㆍ개정 기준서 및 해석서 회사는 다음의 기준서를 신규로 적용하였습니다."
        filler = "이 문단은 앞 항목의 본문입니다. " * 25  # 500자 남짓
        block = "(9) 기업회계기준서 제1021호 '환율변동효과' 개정 교환가능성 평가 지침입니다."
        docs = tmp / "docs"
        _write_doc(docs, "W1", lead + " " + filler + block)
        text = _strip_tags_to_text((docs / "W1" / "W1_00760.xml").read_bytes())
        offset = text.index("(9) 기업회계기준서")
        self.assertGreater(offset - text.index(lead[:10]), 500)
        return docs, lead, offset

    def _violations(self, tmp: Path, docs: Path, row: dict) -> int:
        stances = tmp / f"s{row['sentence_kind'] or 'none'}.jsonl"
        _write_stances(stances, [row])
        rep = run_policy_stance_audit(
            stances_path=stances, docs_dir=docs, report_path=tmp / "audit.md"
        )
        return rep["totals"]["evidence_in_block"]["violations"]

    def test_서문_행만_앞쪽_창이_열린다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs, lead, offset = self._doc_with_lead(t)
            base = {
                "rcept_no": "W1", "corp_code": "1", "fiscal_year": 2025,
                "topic": "STD_EARLY_ADOPTION", "subject": "1021", "value": "ADOPTED",
                "evidence": lead, "method": "rule", "confidence": 1.0,
                "block_offset": offset, "parser_version": "ps-v2",
            }
            self.assertEqual(self._violations(t, docs, {**base, "sentence_kind": "list_preamble"}), 0)
            # 같은 근거·같은 오프셋이라도 서문 행이 아니면 블록 밖 근거로 잡아야 한다.
            self.assertEqual(
                self._violations(t, docs, {**base, "value": "NOT_ADOPTED", "sentence_kind": "company_statement"}), 1
            )


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


class MissingXmlTests(unittest.TestCase):
    """Q-I4: rcept_no 의 XML 을 못 읽으면(--docs 오지정·문서 결손) evidence_in_block 을 위반으로
    잡지 않고 missing_xml 로 따로 센다 — 그래야 "근거의 100% 가 블록 밖"으로 잘못 읽히지 않는다."""

    def test_XML이_없는_행은_evidence_in_block_위반이_아니라_missing_xml로_센다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            docs.mkdir(parents=True, exist_ok=True)  # docs_dir 는 있지만 이 rcept_no 폴더는 없다
            rows = [
                {
                    "rcept_no": "NOXML", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED",
                    "evidence": "회사는 아직 채택하지 않았습니다.", "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            rep = run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=t / "audit.md")

            self.assertEqual(rep["missing_xml"], 1)
            self.assertEqual(rep["totals"]["evidence_in_block"]["checked"], 0)
            self.assertEqual(rep["totals"]["evidence_in_block"]["violations"], 0)
            self.assertEqual(rep["violations"]["evidence_in_block"], [])
            # N4: XML 결손은 "확인 못함"(unchecked) 이지 "설계상 적용 대상 아님"(not_applicable)
            # 이 아니다 — foreign_standard 등 다른 플래그의 not_applicable 과 섞이면 안 된다.
            self.assertEqual(rep["totals"]["evidence_in_block"]["unchecked"], 1)
            self.assertEqual(rep["totals"]["evidence_in_block"]["not_applicable"], 0)
            md = (t / "audit.md").read_text(encoding="utf-8")
            self.assertIn("XML 없음", md)


class CliExitCodeTests(unittest.TestCase):
    """Q-I6/Q-M4/Q-M5: policy-stance-audit CLI 의 종료 코드를 CliRunner 로 직접 고정한다 —
    지금까지 exit 2 는 리뷰어가 수동으로만 확인했고 회귀 방어가 없었다."""

    def test_evidence_in_block_위반이_있으면_exit_2(self) -> None:
        import server  # noqa: F401  — 레포 관례: server -> cli -> dart_search_mcp.tools.* 순서로 import
        import cli
        from click.testing import CliRunner

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            _write_doc(docs, "BAD1", "본문 텍스트입니다.")
            rows = [
                {
                    "rcept_no": "BAD1", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "UNDETERMINED",
                    "evidence": "판정 불가: 이 문구는 문서에 전혀 없습니다", "method": "none",
                    "confidence": 0.0, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            report = t / "audit.md"

            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                ["policy-stance-audit", "--stances", str(stances), "--docs", str(docs), "-o", str(report)],
            )
            self.assertEqual(result.exit_code, 2, result.output)

    def test_missing_xml만_있으면_exit_0(self) -> None:
        import server  # noqa: F401
        import cli
        from click.testing import CliRunner

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            docs.mkdir(parents=True, exist_ok=True)  # 이 rcept_no 의 XML 은 없다
            rows = [
                {
                    "rcept_no": "NOXML2", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED",
                    "evidence": "회사는 아직 채택하지 않았습니다.", "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            report = t / "audit.md"

            runner = CliRunner()
            result = runner.invoke(
                cli.cli,
                ["policy-stance-audit", "--stances", str(stances), "--docs", str(docs), "-o", str(report)],
            )
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("XML 없음", result.output)


class SingleDocCacheTests(unittest.TestCase):
    """Q-I5: rcept_no 별 무제한 dict 캐시 대신 직전 rcept_no 하나만 기억하는 단일 슬롯 캐시를
    쓴다 — 입력이 rcept_no 순서로 안 섞여 있어도(정렬을 그 보장에 기대지 않는다) 문서당 정확히
    1회만 파싱돼야 한다."""

    def test_같은_rcept_no_여러_행도_문서당_1회만_파싱한다(self) -> None:
        import dart_search_mcp.tools.policy_stance_audit as audit_mod

        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            _write_doc(docs, "S1", "회사는 아직 채택하지 않았습니다.")
            _write_doc(docs, "S2", "당사는 원가모형을 적용합니다.")
            # 입력을 일부러 rcept_no 순서가 아니게(S2, S1, S2) 둔다 — 정렬이 실제로 동작해야
            # 단일 슬롯 캐시로도 문서당 1회만 파싱된다.
            rows = [
                {
                    "rcept_no": "S2", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "PPE_MEASUREMENT", "subject": None, "value": "COST_MODEL",
                    "evidence": "당사는 원가모형을 적용합니다.", "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
                {
                    "rcept_no": "S1", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "STD_EARLY_ADOPTION", "subject": "1118", "value": "NOT_ADOPTED",
                    "evidence": "회사는 아직 채택하지 않았습니다.", "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
                {
                    "rcept_no": "S2", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "STD_EARLY_ADOPTION", "subject": "1109", "value": "NOT_ADOPTED",
                    "evidence": "당사는 원가모형을 적용합니다.", "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)

            calls: list[str] = []
            original = audit_mod.select_xml_path

            def _spy(docs_dir: Path, rcept_no: str):
                calls.append(rcept_no)
                return original(docs_dir, rcept_no)

            audit_mod.select_xml_path = _spy
            try:
                run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=t / "audit.md")
            finally:
                audit_mod.select_xml_path = original

            self.assertEqual(len(calls), 2)  # 문서 2개 — 행은 3개지만 파싱은 문서당 1회
            self.assertEqual(set(calls), {"S1", "S2"})


class AuditKeywordsTests(unittest.TestCase):
    """Q-I6: _AUDIT_KEYWORDS 는 브리프가 주제별 정규식을 문자까지 지정한 부분인데, 지금까지
    이 함수를 지나는 테스트가 0 이었다(합성 5행은 전부 rule/none, 픽스처 교차 테스트는
    use_llm=False). 네 주제 각각 양성 1건·음성 1건으로 회귀를 잠근다."""

    def test_STD_EARLY_ADOPTION_키워드(self) -> None:
        self.assertTrue(_pattern_in_evidence_llm("STD_EARLY_ADOPTION", "당사는 조기적용을 검토 중입니다."))
        self.assertFalse(_pattern_in_evidence_llm("STD_EARLY_ADOPTION", "회사는 이 사항과 무관합니다."))

    def test_PPE_MEASUREMENT_키워드(self) -> None:
        self.assertTrue(_pattern_in_evidence_llm("PPE_MEASUREMENT", "당사는 원가모형을 적용합니다."))
        self.assertFalse(_pattern_in_evidence_llm("PPE_MEASUREMENT", "당사는 취득가액으로 인식합니다."))

    def test_REVENUE_METHOD_키워드(self) -> None:
        self.assertTrue(_pattern_in_evidence_llm("REVENUE_METHOD", "수익은 진행률에 따라 인식합니다."))
        self.assertFalse(_pattern_in_evidence_llm("REVENUE_METHOD", "수익은 매출 발생 시 계상합니다."))

    def test_IP_MEASUREMENT_키워드(self) -> None:
        self.assertTrue(_pattern_in_evidence_llm("IP_MEASUREMENT", "투자부동산은 공정가치모형으로 측정합니다."))
        self.assertFalse(_pattern_in_evidence_llm("IP_MEASUREMENT", "투자부동산은 임대수익을 창출합니다."))

    def test_등록되지_않은_주제는_None(self) -> None:
        self.assertIsNone(_pattern_in_evidence_llm("UNKNOWN_TOPIC", "아무 문장"))


class RowValidationTests(unittest.TestCase):
    """Q-M1: 필수 키(rcept_no·topic)가 없는 행은 bare KeyError 대신 몇 번째 줄인지 담은
    명확한 오류를 낸다 — 중간에 잘린 jsonl 을 감사할 때 아무 리포트도 없이 죽는 대신."""

    def test_topic_키가_없는_행은_줄번호와_함께_에러(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = t / "policy_stance.jsonl"
            stances.write_text(
                json.dumps({"rcept_no": "A", "topic": "STD_EARLY_ADOPTION", "subject": None,
                            "value": "NOT_ADOPTED", "evidence": "x", "method": "rule", "block_offset": 0})
                + "\n"
                + json.dumps({"rcept_no": "B", "subject": None, "value": "NOT_ADOPTED",
                              "evidence": "x", "method": "rule", "block_offset": 0})
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(PolicyStanceAuditRowError) as ctx:
                run_policy_stance_audit(stances_path=stances, docs_dir=t / "docs", report_path=t / "audit.md")
            self.assertIn("2번째 줄", str(ctx.exception))
            self.assertIn("topic", str(ctx.exception))

    def test_rcept_no가_정수면_줄번호와_함께_에러_N3(self) -> None:
        """재리뷰 N3 재현: Q-M1 은 키 존재만 봐서, rcept_no 가 따옴표 없는 숫자(정수)로 들어온
        행은 그대로 통과시켰다. 그러면 rows.sort(key=lambda r: r["rcept_no"]) 가 str/int 혼재로
        bare TypeError 를 던져 Q-M1 이 없애려던 실패 양상이 되살아난다. 이제 타입까지 확인해
        줄번호가 담긴 명확한 오류를 낸다."""
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            stances = t / "policy_stance.jsonl"
            stances.write_text(
                json.dumps({"rcept_no": "A1", "topic": "STD_EARLY_ADOPTION", "subject": None,
                            "value": "NOT_ADOPTED", "evidence": "x", "method": "rule", "block_offset": 0})
                + "\n"
                + json.dumps({"rcept_no": 20260001000, "topic": "STD_EARLY_ADOPTION", "subject": None,
                              "value": "NOT_ADOPTED", "evidence": "x", "method": "rule", "block_offset": 0})
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(PolicyStanceAuditRowError) as ctx:
                run_policy_stance_audit(stances_path=stances, docs_dir=t / "docs", report_path=t / "audit.md")
            self.assertIn("2번째 줄", str(ctx.exception))
            self.assertIn("rcept_no", str(ctx.exception))


class NotApplicableVsUncheckedTests(unittest.TestCase):
    """N4: `unchecked = total_rows - checked`(Q-M2) 는 "설계상 이 플래그가 이 행에 적용되지
    않는다"(not_applicable — 예: T1 이 아닌 행의 foreign_standard, TOPICS 밖 주제의 rule 행
    pattern_in_evidence)와 "적용은 되는데 XML 을 못 읽어 확인 못했다"(unchecked)를 구분하지
    못했다. T1 이 아닌 모든 행이 foreign_standard 의 "미검사"에 잡혀, 진짜 등록 누락 신호가
    거대한 정상 수치에 묻혔다. 이제 둘을 분리한다. (llm/llm_verified 행의 _AUDIT_KEYWORDS 미등록은
    not_applicable 이 아니라 별도의 unregistered_topic 이다 — UnregisteredTopicTests 참고.)"""

    def test_TOPICS_밖_주제코드는_pattern_in_evidence가_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            _write_doc(docs, "U1", "아무 문장입니다.")
            rows = [
                {
                    "rcept_no": "U1", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "BOGUS_TOPIC", "subject": None, "value": "WHATEVER",
                    "evidence": "아무 문장입니다.", "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            rep = run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=t / "audit.md")

            self.assertEqual(rep["totals"]["pattern_in_evidence"]["checked"], 0)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["violations"], 0)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["not_applicable"], 1)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["unchecked"], 0)
            md = (t / "audit.md").read_text(encoding="utf-8")
            self.assertIn("미적용", md)
            self.assertIn("미검사", md)

    def test_T1이_아닌_행은_foreign_standard가_not_applicable(self) -> None:
        """재리뷰가 직접 지적한 예시(PPE 1행: foreign_standard checked=0)를 그대로 고정한다 —
        이제는 unchecked 가 아니라 not_applicable 이어야 한다(T1 전용 플래그를 T1 아닌 행에
        적용 안 하는 것은 설계이지, XML 을 못 읽은 게 아니다)."""
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            body = "유형자산은 원가모형을 적용합니다."
            _write_doc(docs, "P1", body)
            rows = [
                {
                    "rcept_no": "P1", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "PPE_MEASUREMENT", "subject": None, "value": "COST_MODEL",
                    "evidence": body, "method": "rule",
                    "confidence": 1.0, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            rep = run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=t / "audit.md")

            self.assertEqual(rep["totals"]["foreign_standard"]["checked"], 0)
            self.assertEqual(rep["totals"]["foreign_standard"]["not_applicable"], 1)
            self.assertEqual(rep["totals"]["foreign_standard"]["unchecked"], 0)


class UnregisteredTopicTests(unittest.TestCase):
    """N4 잔여(재리뷰 Minor #10): llm/llm_verified 행의 주제가 _AUDIT_KEYWORDS 에 없으면
    pattern_in_evidence 가 not_applicable 로 묻혔다 — method=="none" 행 전부가 이미 채우는
    거대한 정상 모집단과 같은 칸이라 "주제 추가 후 등록을 잊었다"는 신호가 안 보였다.
    policy_stance_gate.unknown_topics 처럼 별도 카운터·목록으로 낸다."""

    def test_미등록_주제의_llm_행은_unregistered_topic으로_잡힌다(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            docs = t / "docs"
            _write_doc(docs, "L1", "아무 문장입니다.")
            rows = [
                {
                    "rcept_no": "L1", "corp_code": "1", "fiscal_year": 2025,
                    "topic": "BOGUS_TOPIC", "subject": None, "value": "WHATEVER",
                    "evidence": "아무 문장입니다.", "method": "llm",
                    "confidence": 0.6, "block_offset": 0, "parser_version": "ps-v1",
                },
            ]
            stances = t / "policy_stance.jsonl"
            _write_stances(stances, rows)
            report = t / "audit.md"
            rep = run_policy_stance_audit(stances_path=stances, docs_dir=docs, report_path=report)

            self.assertEqual(rep["totals"]["pattern_in_evidence"]["checked"], 0)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["violations"], 0)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["not_applicable"], 0)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["unchecked"], 0)
            self.assertEqual(rep["totals"]["pattern_in_evidence"]["unregistered_topic"], 1)
            self.assertEqual(rep["unregistered_topics"], ["BOGUS_TOPIC"])

            md = report.read_text(encoding="utf-8")
            self.assertIn("미등록주제", md)
            self.assertIn("BOGUS_TOPIC", md)


if __name__ == "__main__":
    unittest.main()
