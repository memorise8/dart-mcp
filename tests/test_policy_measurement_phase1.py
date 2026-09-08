"""Phase 1 T5: 측정모형 조건절 배제·자산군별 행·「일부 수익」 병행(회계사 확인 표본).

픽스처는 `dart_collected/docs/<rcept>/` 의 실제 XML 을
`audit_xml_parser._strip_tags_to_text` 로 평문화한 뒤 해당 주석 항목 구간만 잘라
저장한 것이다 — 아래 표의 [start, end) 는 그 평문 기준 char 범위다.

| 픽스처                                | 출처 XML                 | 범위(char)      |
|---------------------------------------|--------------------------|-----------------|
| 20260430001895-dyd-policy.txt         | 20260430001895_00761.xml | [24962, 28052)  |
| 20260319000739-sungkwang-policy.txt   | 20260319000739_00761.xml | [25648, 26571)  |
| 20260319001171-ontide-policy.txt      | 20260319001171_00761.xml | [23244, 24294)  |
| 20260318000717-lanix-policy.txt       | 20260318000717_00761.xml | [29364, 30813)  |
| 20260318000447-mncsolution-policy.txt | 20260318000447_00760.xml | [37276, 37373)  |
| 20260401000853-xcena-policy.txt       | 20260401000853_00760.xml | [15645, 16262)  |
| 20260323001053-hcbokwang-policy.txt   | 20260323001053_00761.xml | [35382, 35824)  |
| 20260318000745-heungah-policy.txt     | 20260318000745_00761.xml | [59101, 60957)  |
| 20260319000921-redcap-policy.txt      | 20260319000921_00761.xml | [39170, 40478)  |

값의 권위는 회계사 확인(2026-09-08, P3~P5)이다.
"""
import json
import tempfile
import unittest
from pathlib import Path

from dart_search_mcp.policy_blocks import PolicyBlock, split_policy_blocks
from dart_search_mcp.policy_rules import TOPICS, Judgement, judge, judge_all
from dart_search_mcp.policy_rules_measurement import asset_class_rows, block_heading
from dart_search_mcp.tools.policy_stance_cli import run_extract_policy_stances

FX = Path(__file__).parent / "fixtures" / "phase1"
_SENTENCE_ENDS = ("다.", "음.", "함.")


def _judge_all(name: str, topic: str) -> list[Judgement]:
    """픽스처 평문을 블록으로 자른 뒤 `spec.applies` 블록 전부에 `judge_all` 을 적용해 합친다."""
    text = (FX / name).read_text(encoding="utf-8")
    spec = TOPICS[topic]
    blocks = [b for b in split_policy_blocks(text) if spec.applies(b)]
    assert blocks, f"{name}: {topic} 대상 블록 없음"
    return [j for b in blocks for j in judge_all(spec, b)]


def _blk(text: str, topic: str) -> PolicyBlock:
    return PolicyBlock(heading=text[:60], standard_no=None, topic_heading=topic, start=0, text=text, capped=False)


class MeasurementPhase1(unittest.TestCase):
    def test_impairment_conditional_clause_is_not_revaluation(self) -> None:
        """디와이디 20260430001895 연결: 2.2.8 유형자산은 원가모형이고, 2.2.11 「비금융자산의
        손상」의 「다만, 재평가모형을 적용하고 있는 자산의 경우에는 …」은 조건절이라 근거가 될
        수 없다(회계사 확인 P3). 픽스처는 2.2.8~2.2.11 을 통째로 담고 있으므로, 손상 절의
        재평가 문구가 유형자산 블록으로 새면 이 테스트가 깨진다."""
        rows = _judge_all("20260430001895-dyd-policy.txt", "PPE_MEASUREMENT")
        self.assertNotIn("REVALUATION_MODEL", {r.value for r in rows})
        self.assertIn("COST_MODEL", {r.value for r in rows})
        held = [r for r in rows if r.value == "UNDETERMINED"]
        # Fix round 1(리뷰 minor): 이 픽스처에는 보류 행이 **하나도 없어야** 한다 — 2.2.8 절이
        # 통째로 한 블록이 되고 값이 나오기 때문이다. 빈 목록을 명시해, 아래 루프가 공허하게
        # 통과하는 것(보류 행이 생겼는데도 눈치채지 못하는 것)을 막는다.
        self.assertEqual(held, [])
        for r in held:
            self.assertEqual(r.held_reason, "CONDITIONAL_ONLY")

    def test_conditional_clause_only_block_is_held_as_conditional_only(self) -> None:
        """조건절뿐인 블록의 보류 사유는 CONDITIONAL_ONLY 다 — 「근거가 아예 없다」와
        「조건절뿐이라 못 정한다」를 구분해야 다음 라운드에 무엇을 고칠지 정할 수 있다.
        문장은 디와이디 20260430001895 2.2.11 원문 그대로다(위 픽스처에 있는 문장)."""
        spec = TOPICS["PPE_MEASUREMENT"]
        text = (
            "(9) 유형자산 다만, 재평가모형을 적용하고 있는 자산의 경우에는 동 환입액을 "
            "재평가잉여금의 증가로 처리하고 있습니다."
        )
        j = judge(spec, _blk(text, "유형자산"))
        self.assertEqual((j.value, j.held_reason), ("UNDETERMINED", "CONDITIONAL_ONLY"))
        silent = judge(spec, _blk("(9) 유형자산 유형자산의 내용연수는 매 보고기간말에 재검토합니다.", "유형자산"))
        self.assertEqual((silent.value, silent.held_reason), ("UNDETERMINED", ""))

    def test_conditional_clause_does_not_swallow_the_preceding_clause(self) -> None:
        """Fix round 1(리뷰 minor): 조건절의 시작은 명시적 경계(구두점·연결어미 「며」·문자열 처음)다.
        구두점 없이 절이 이어지는 문장에서 60자 walk-back 을 하면 앞 절의 확정 진술까지 지운다."""
        spec = TOPICS["PPE_MEASUREMENT"]
        kept = judge(spec, _blk(
            "연결회사는 유형자산에 대하여 원가모형을 적용하고 있으며 자산의 장부금액이 "
            "회수가능액을 초과하는 경우 손상차손을 인식합니다.", "유형자산"))
        self.assertEqual(kept.value, "COST_MODEL")
        comma = judge(spec, _blk(
            "(9) 유형자산 토지는 재평가모형을 적용하고 있으며, 손상징후가 있는 경우 "
            "회수가능액을 추정합니다.", "유형자산"))
        self.assertEqual(comma.value, "REVALUATION_MODEL")

    def test_impairment_heading_block_does_not_apply(self) -> None:
        """「(1) 유형자산, 사용권자산 손상차손 및 환입」(흥아해운 20260318000745 실측 표제)은
        손상 회계정책 블록이지 측정모형 블록이 아니다 — 판정 대상에서 뺀다. 표제에 손상이
        없는 통상 블록(본문에 「손상차손누계액」이 나올 뿐)은 그대로 대상이다."""
        spec = TOPICS["PPE_MEASUREMENT"]
        impairment = _blk(
            "(1) 유형자산, 사용권자산 손상차손 및 환입 유형자산, 사용권자산의 손상차손을 "
            "인식하고 있습니다.", "유형자산",
        )
        self.assertFalse(spec.applies(impairment))
        self.assertIn("손상", block_heading(impairment))
        ordinary = _blk(
            "(4) 유형자산 유형자산은 원가에서 감가상각누계액과 손상차손누계액을 차감하여 표시됩니다.",
            "유형자산",
        )
        self.assertNotIn("손상", block_heading(ordinary))
        self.assertTrue(spec.applies(ordinary))

    def test_asset_class_rows(self) -> None:
        rows = {(r.subject, r.value) for r in _judge_all("20260319001171-ontide-policy.txt", "PPE_MEASUREMENT")}
        self.assertIn(("토지", "REVALUATION_MODEL"), rows)
        self.assertIn(("건물", "REVALUATION_MODEL"), rows)
        self.assertTrue(any(s and "제외한 유형자산" in s and v == "COST_MODEL" for s, v in rows), rows)
        self.assertIn((None, "MIXED"), rows)
        rows2 = {(r.subject, r.value) for r in _judge_all("20260318000717-lanix-policy.txt", "PPE_MEASUREMENT")}
        self.assertIn(("토지", "REVALUATION_MODEL"), rows2)
        self.assertIn(("토지를 제외한 유형자산", "COST_MODEL"), rows2)

    def test_asset_class_rows_are_extractable_alone(self) -> None:
        """자산군 추출은 판정 순서와 독립이다 — 함수 하나로 검증할 수 있어야 한다.
        자산군이 특정되지 않으면 행을 내지 않고(회사 수준 행 하나만 남는다), 자산군 언급이
        측정 서술 없이 나오면(「토지는 감가상각을 하지 않으며」) 역시 행이 아니다."""
        self.assertEqual(
            asset_class_rows(_blk("(5) 유형자산 유형자산은 원가모형을 적용하고 있습니다.", "유형자산")), []
        )
        self.assertEqual(
            asset_class_rows(_blk("(5) 유형자산 유형자산 중 토지는 감가상각을 하지 않습니다.", "유형자산")), []
        )
        rows = asset_class_rows(_blk(
            "(5) 유형자산 토지와 건물은 공정가치로 측정하고 있습니다. "
            "토지와 건물을 제외한 유형자산은 취득원가에서 감가상각누계액을 차감하여 표시합니다.",
            "유형자산",
        ))
        self.assertEqual(
            [(s, v) for s, v, _e in rows],
            [("토지", "REVALUATION_MODEL"), ("건물", "REVALUATION_MODEL"), ("토지와 건물을 제외한 유형자산", "COST_MODEL")],
        )

    def test_partial_over_time_is_both(self) -> None:
        self.assertEqual(
            {r.value for r in _judge_all("20260318000447-mncsolution-policy.txt", "REVENUE_METHOD")}, {"BOTH"}
        )

    def test_both_evidence_covers_both_methods(self) -> None:
        r = _judge_all("20260323001053-hcbokwang-policy.txt", "REVENUE_METHOD")[0]
        self.assertEqual(r.value, "BOTH")
        self.assertIn("기간에 걸쳐", r.evidence)
        self.assertIn("시점에", r.evidence)
        self.assertLessEqual(len(r.evidence), 400)
        self.assertTrue(r.evidence.endswith(_SENTENCE_ENDS), r.evidence[-40:])
        self.assertIn(r.evidence, (FX / "20260323001053-hcbokwang-policy.txt").read_text(encoding="utf-8"))

    def test_both_evidence_never_ends_mid_sentence(self) -> None:
        """Fix round 1(리뷰 I2): 계획은 「400자 상한 안에서 **앞뒤 문장 경계까지만**」이다.
        원시 400자 창을 그대로 쓰면 근거가 낱말 한가운데서 끝난다(엑시나 20260401000853:
        「…진행률에 따라 용」). 두 트리거를 다 담은 채로 문장 경계에서 끝낼 수 없으면 넓히지
        않고 결정 패턴이 든 문장 하나를 쓴다."""
        for name in ("20260401000853-xcena-policy.txt", "20260323001053-hcbokwang-policy.txt"):
            with self.subTest(name=name):
                r = _judge_all(name, "REVENUE_METHOD")[0]
                self.assertEqual(r.value, "BOTH")
                self.assertLessEqual(len(r.evidence), 400)
                self.assertTrue(r.evidence.endswith(_SENTENCE_ENDS), r.evidence[-40:])
                self.assertIn(r.evidence, (FX / name).read_text(encoding="utf-8"))

    def test_regressions(self) -> None:
        self.assertEqual(_judge_all("20260319000739-sungkwang-policy.txt", "PPE_MEASUREMENT")[0].value, "COST_MODEL")
        self.assertEqual(_judge_all("20260401000853-xcena-policy.txt", "REVENUE_METHOD")[0].value, "BOTH")
        self.assertEqual(_judge_all("20260318000745-heungah-policy.txt", "IP_MEASUREMENT")[0].value, "FAIR_VALUE_MODEL")
        self.assertEqual(_judge_all("20260319000921-redcap-policy.txt", "IP_MEASUREMENT")[0].value, "COST_MODEL")

    _TWO_BLOCK_XML = (
        "<DOCUMENT><BODY>"
        "<P>2. 중요한 회계정책</P>"
        "<P>(4) 유형자산 그 외의 모든 유형자산은 취득원가에서 감가상각누계액을 차감하여 "
        "표시하고 있습니다.</P>"
        "<P>(5) 유형자산 재평가당사는 유형자산 중 토지에 대하여 재평가모형을 적용하고 "
        "있으며 보고기간말에 주기적으로 재평가를 수행하고 있습니다.</P>"
        "</BODY></DOCUMENT>"
    )

    def _run_cli(self, tmp: Path, xml: str) -> dict[tuple[str, str | None], dict]:
        rcept = "20260001000009"
        docs = tmp / "docs" / rcept
        docs.mkdir(parents=True)
        (docs / f"{rcept}_00760.xml").write_text(xml, encoding="utf-8")
        facts = tmp / "audit_facts.jsonl"
        facts.write_text(
            json.dumps({"rcept_no": rcept, "corp_code": "00000009", "fiscal_year": "2025"},
                       ensure_ascii=False) + "\n", encoding="utf-8")
        out = tmp / "policy_stance.jsonl"
        run_extract_policy_stances(
            facts_path=facts, docs_dir=tmp / "docs", output_path=out,
            topics=("PPE_MEASUREMENT",), use_llm=False)
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
        return {(r["topic"], r["subject"]): r for r in rows}

    def test_cross_block_disagreement_becomes_mixed(self) -> None:
        """Fix round 1(리뷰 I1): 한 보고서의 블록 둘이 원가·재평가로 갈리면 회사 수준 행은
        MIXED 다 — 예전에는 dedup 이 먼저 나온 블록(원가)을 남겨, 같은 파일 안에서 회사 수준
        행이 자기 자산군 행(토지=재평가)과 모순됐다(실측 20260403000370·20260413001027)."""
        with tempfile.TemporaryDirectory() as tmp:
            by = self._run_cli(Path(tmp), self._TWO_BLOCK_XML)
            self.assertEqual(by[("PPE_MEASUREMENT", None)]["value"], "MIXED")
            self.assertEqual(by[("PPE_MEASUREMENT", "토지")]["value"], "REVALUATION_MODEL")
            # 회사 수준 행은 어느 자산군 행과도 모순되지 않는다(조합값이거나 같은 값이다).
            company = by[("PPE_MEASUREMENT", None)]["value"]
            asset_values = {r["value"] for (topic, subj), r in by.items()
                            if topic == "PPE_MEASUREMENT" and subj is not None}
            self.assertTrue(all(company in ("MIXED", v) for v in asset_values), (company, asset_values))
            # 근거·근거 메타는 먼저 나온 블록의 것을 그대로 둔다(어디서 시작된 판단인지 남긴다).
            self.assertIn("취득원가에서 감가상각누계액", by[("PPE_MEASUREMENT", None)]["evidence"])

    def test_single_value_report_is_not_promoted(self) -> None:
        """값이 하나뿐이면 승격하지 않는다 — 승격 조건은 "확정값이 둘 이상"이다."""
        xml = (
            "<DOCUMENT><BODY><P>2. 중요한 회계정책</P>"
            "<P>(4) 유형자산 그 외의 모든 유형자산은 취득원가에서 감가상각누계액을 차감하여 "
            "표시하고 있습니다.</P></BODY></DOCUMENT>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            by = self._run_cli(Path(tmp), xml)
            self.assertEqual(by[("PPE_MEASUREMENT", None)]["value"], "COST_MODEL")

    def test_judge_is_the_first_row_of_judge_all(self) -> None:
        """`judge` 계약 유지 — 회사 수준 행 하나(subject 는 spec.subject_of)."""
        spec = TOPICS["PPE_MEASUREMENT"]
        text = (FX / "20260319001171-ontide-policy.txt").read_text(encoding="utf-8")
        block = next(b for b in split_policy_blocks(text) if spec.applies(b))
        rows = judge_all(spec, block)
        self.assertEqual(judge(spec, block), rows[0])
        self.assertIsNone(rows[0].subject)
        self.assertTrue(len(rows) > 1)


if __name__ == "__main__":
    unittest.main()
