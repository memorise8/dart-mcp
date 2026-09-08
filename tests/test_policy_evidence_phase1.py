"""Phase 1 T4: 정책 근거 문장 선택·판단 보류 축소·목록 서문 귀속(회계사 확인 표본).

픽스처는 `dart_collected/docs/<rcept>/` 의 실제 XML 을
`audit_xml_parser._strip_tags_to_text` 로 평문화한 뒤 회계정책 주석(주석 2)
구간만 잘라 저장한 것이다 — 아래 표의 [start, end) 는 그 평문 기준 char 범위다.

| 픽스처                              | 출처 XML                        | 범위(char)      |
|-------------------------------------|---------------------------------|-----------------|
| 20260316001522-neopharm-policy.txt  | 20260316001522_00761.xml        | [12287, 15200)  |
| 20260320001388-nibec-policy.txt     | 20260320001388_00760.xml        | [10263, 13350)  |
| 20260318001024-emart-policy.txt     | 20260318001024_00761.xml        | [25952, 28500)  |
| 20260316001663-estec-policy.txt     | 20260316001663_00761.xml        | [11885, 13400)  |
| 20260310002820-samsung-policy.txt   | 20260310002820_00761.xml        | [36873, 39000)  |
| 20260327000721-olladio-policy.txt   | 20260327000721_00760.xml        | [7694, 10500)   |
| 20260316000929-koreazinc-policy.txt | 20260316000929_00761.xml        | [33788, 36800)  |
| 20260313001168-unisem-policy.txt    | 20260313001168_00761.xml        | [11709, 13700)  |

값의 권위는 회계사 확인(P1·P2, 경계4)과 원문 확인(올래디오)이다.
"""
import unittest
from pathlib import Path

from dart_search_mcp.policy_blocks import split_policy_blocks
from dart_search_mcp.policy_rules import TOPICS, judge, positive_pattern_matches

FX = Path(__file__).parent / "fixtures" / "phase1"


def _judge_std(name: str, std_no: str):
    text = (FX / name).read_text(encoding="utf-8")
    spec = TOPICS["STD_EARLY_ADOPTION"]
    blocks = [b for b in split_policy_blocks(text) if b.standard_no == std_no and spec.applies(b)]
    assert blocks, f"{name}: 제{std_no}호 블록 없음"
    return [judge(spec, b) for b in blocks]


class PolicyEvidencePhase1(unittest.TestCase):
    def test_company_review_sentence_is_the_evidence(self) -> None:
        """근거는 기준서 설명문이 아니라 회사가 주어인 문장이어야 한다(회계사 확인 P1·P2).

        나이벡의 `must` 는 브리프가 인용한 「경영진은 현재 … 검토 중에 있으며」 대신
        같은 블록의 「회사는 제1118호를 아직 채택하지 않았으며 …」 로 둔다 — 둘 다 회사
        문장이고, 뒤엣것은 검토 중이라는 정황이지만 앞엣것은 미적용을 직접 단정한다.
        회계사가 지적한 축(기준서 설명문 → 회사 문장)은 그대로 지키면서 더 강한 근거를
        고른 것이라, 규칙을 약한 문장 쪽으로 되돌리지 않고 기대값을 맞춘다."""
        for name, must in (("20260316001522-neopharm-policy.txt", "검토 중"),
                           ("20260320001388-nibec-policy.txt", "아직 채택하지 않았으며")):
            with self.subTest(name=name):
                js = [j for j in _judge_std(name, "1118") if j.value == "NOT_ADOPTED"]
                self.assertTrue(js, name)
                self.assertIn(must, js[0].evidence)
                self.assertEqual(js[0].sentence_kind, "company_statement")
                # 기준서 설명문(시행일·조기적용 허용)은 더 이상 근거가 아니다.
                self.assertNotIn("조기적용이 허용됩니다", js[0].evidence)
                self.assertNotIn("조기도입이 가능합니다", js[0].evidence)

    def test_under_review_sentences_resolve_held_to_not_adopted(self) -> None:
        for name in ("20260318001024-emart-policy.txt", "20260316001663-estec-policy.txt",
                     "20260310002820-samsung-policy.txt"):
            with self.subTest(name=name):
                values = {j.value for j in _judge_std(name, "1118")}
                self.assertIn("NOT_ADOPTED", values, name)
                self.assertNotIn("UNDETERMINED", values, name)

    def test_future_effective_date_only_is_permission_only(self) -> None:
        block_text = ("(4) 기업회계기준서 제1118호 '재무제표 표시와 공시' 제정 동 기준서는 2027년 1월 1일 이후 "
                      "최초로 시작하는 회계연도부터 적용되며, 조기적용이 허용됩니다.")
        j = judge(TOPICS["STD_EARLY_ADOPTION"], split_policy_blocks(block_text)[0])
        self.assertEqual(j.value, "PERMITTED_ONLY")
        self.assertEqual(j.sentence_kind, "standard_description")

    def test_list_preamble_attaches_forward_only(self) -> None:
        js = _judge_std("20260327000721-olladio-policy.txt", "1117")
        self.assertEqual({j.value for j in js}, {"ADOPTED"})
        self.assertNotIn("조기 적용하지 아니한", js[0].evidence)

    def test_new_evidence_columns_are_filled(self) -> None:
        """근거 메타 5열 — 값이 어디서 왔는지(문장 종류·주어·절 경로)와 왜 보류인지를 남긴다."""
        spec = TOPICS["STD_EARLY_ADOPTION"]
        j = _judge_std("20260327000721-olladio-policy.txt", "1117")[0]
        self.assertEqual((j.value, j.sentence_kind, j.sentence_subject), ("ADOPTED", "list_preamble", "회사"))
        self.assertTrue(j.evidence_heading_path.startswith("2.2.1 › "), j.evidence_heading_path)
        self.assertIn("제1117호", j.evidence_heading_path)
        self.assertEqual((j.held_reason, j.is_conditional), ("", False))
        self.assertTrue(positive_pattern_matches(spec, "ADOPTED", j.evidence))
        silent = judge(spec, split_policy_blocks(
            "(4) 기업회계기준서 제1118호 제정 손익계산서에 새로운 중간합계를 도입합니다.")[0])
        self.assertEqual((silent.value, silent.held_reason), ("UNDETERMINED", "EVIDENCE_INSUFFICIENT"))

    def test_regressions(self) -> None:
        self.assertIn("EARLY_ADOPTED", {j.value for j in _judge_std("20260316000929-koreazinc-policy.txt", "1109")})
        self.assertIn("PERMITTED_ONLY", {j.value for j in _judge_std("20260313001168-unisem-policy.txt", "1109")})


if __name__ == "__main__":
    unittest.main()
