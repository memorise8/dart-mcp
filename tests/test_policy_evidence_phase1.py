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
from dart_search_mcp.policy_sentences import find_section

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
        같은 블록의 「회사는 제1118호를 아직 채택하지 않았으며 …」 로 둔다. 규칙이 그 문장을
        고르는 이유는 "더 강해서"가 아니라 **위치**다 — 두 문장 다 company_statement 이고 같은
        패턴 묶음(A)에 걸려 순위가 같으므로, 블록에서 먼저 나오는 문장이 이긴다. 회계사가 지적한
        축(기준서 설명문 → 회사 문장)은 그대로 지켜지므로 기대값을 이 문장으로 맞춘다."""
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

    _TWO_PREAMBLE_TEXT = (
        "2.2 회계정책과 공시의 변경 2.2.1 회사가 채택한 제ㆍ개정 기준서 및 해석서 회사는 "
        "2025년 1월 1일로 개시하는 회계기간부터 다음의 제ㆍ개정 기준서 및 해석서를 신규로 "
        "적용하였습니다. (1) 기업회계기준서 제1021호 '환율변동효과' 개정 교환가능성 평가 "
        "지침을 추가하였습니다.{gap}2.2.2{gap}공표되었으나 아직 시행되지 않은 기준서{gap}"
        "(1) 기업회계기준서 제1117호 '보험계약' 개정 추정기법의 차이에 대한 추가공시 규정입니다."
    )

    def _two_preamble_blocks(self, gap: str) -> dict:
        return {b.standard_no: b for b in split_policy_blocks(self._TWO_PREAMBLE_TEXT.format(gap=gap))}

    def test_lead_does_not_cross_a_section_heading(self) -> None:
        r"""Fix round 1(Important 2): 다음 절 표제가 인식되지 않는 형태여도 앞 절의 채택 서문이
        딸려 오면 안 된다. 2.2.2 항목은 2.2.1 의 「신규로 적용하였습니다」 를 근거로 삼을 수
        없다 — 귀속 불명으로 보류한다.

        Fix round 2: 절 표제 앞뒤 공백이 없는 형태(평문화 과정에서 흔히 사라진다)도 같아야 한다.
        예전 lookbehind `(?<![\d.])` 는 앞이 마침표이기만 하면 절 표제를 못 봐서, 2.2.2 항목의
        절이 2.2.1 로 읽히고 서문의 절과 **거짓 일치**해 검사가 통과했다."""
        spec = TOPICS["STD_EARLY_ADOPTION"]
        for gap in (" ", ""):
            with self.subTest(gap=repr(gap)):
                by_std = self._two_preamble_blocks(gap)
                same_section = judge(spec, by_std["1021"])
                self.assertEqual((same_section.value, by_std["1021"].section), ("ADOPTED", "2.2.1"))
                crossing = judge(spec, by_std["1117"])
                self.assertEqual(by_std["1117"].section, "2.2.2")
                self.assertNotEqual(crossing.value, "ADOPTED")
                self.assertEqual((crossing.value, crossing.held_reason), ("UNDETERMINED", "ATTRIBUTION_UNCLEAR"))

    def test_section_number_is_read_across_a_missing_space(self) -> None:
        """Fix round 2: 절 조회와 블록 경계가 같은 패턴을 쓰고, 그 패턴은 문장 끝 마침표 뒤에
        공백이 없어도 절 표제를 본다. 중첩 번호("1.2.2.2")의 안쪽 조각은 여전히 절이 아니다."""
        text = self._TWO_PREAMBLE_TEXT.format(gap="")
        self.assertEqual(find_section(text, text.index("제1117호")), "2.2.2")
        self.assertEqual(find_section("보고서의 1.2.2.2 항목은 다음과 같습니다.", 30), "1.2.2.2")
        self.assertEqual(find_section("직전 문장입니다.1.2.2.2 항목은 다음과 같습니다.", 30), "1.2.2.2")

    def test_mid_sentence_fragment_is_not_a_list_preamble(self) -> None:
        """Fix round 1(Important 2): 20250827000159/1115 재현 — 평문에서 마침표 뒤 공백이
        사라지면 종결 어미 "다." 가 한글 항목 기호로 보인다. 「…있다.회사가 … 적용한 회계정책은
        기업회계기준…」 이 채택 서문으로 잡혀 뒤 항목이 ADOPTED 로 뒤집혔다."""
        text = (
            "기업회계기준서 제1116호 리스 관련 회계정책은 다음과 같이 적용하고 있다."
            "회사가 리스제공자로서 비교기간에 적용한 회계정책은 기업회계기준서 제1115호와 "
            "일관됩니다. (3) 기업회계기준서 제1115호 '고객과의 계약에서 생기는 수익' "
            "수익인식 5단계 모형을 규정합니다."
        )
        block = next(b for b in split_policy_blocks(text) if b.standard_no == "1115")
        self.assertEqual(block.lead_polarity, "")
        j = judge(TOPICS["STD_EARLY_ADOPTION"], block)
        self.assertEqual((j.value, j.held_reason), ("UNDETERMINED", "EVIDENCE_INSUFFICIENT"))

    def test_적용_예정_서술은_주어에_따라_갈린다(self) -> None:
        """Fix round 1(minor): 「적용할 예정」과 「적용될 예정」이 같게 움직인다 — 주어가
        기준서면 PERMITTED_ONLY, 회사면 NOT_ADOPTED."""
        spec = TOPICS["STD_EARLY_ADOPTION"]
        for tail in ("적용할 예정입니다.", "적용될 예정입니다."):
            with self.subTest(tail=tail):
                std = judge(spec, split_policy_blocks(
                    f"(4) 기업회계기준서 제1118호 '재무제표 표시와 공시' 제정 동 기준서는 2027년 1월 1일부터 {tail}")[0])
                self.assertEqual((std.value, std.sentence_kind), ("PERMITTED_ONLY", "standard_description"))
                company = judge(spec, split_policy_blocks(
                    f"(4) 기업회계기준서 제1118호 '재무제표 표시와 공시' 제정 회사는 동 기준서를 2027년 1월 1일부터 {tail}")[0])
                self.assertEqual((company.value, company.sentence_kind), ("NOT_ADOPTED", "company_statement"))

    def test_regressions(self) -> None:
        self.assertIn("EARLY_ADOPTED", {j.value for j in _judge_std("20260316000929-koreazinc-policy.txt", "1109")})
        self.assertIn("PERMITTED_ONLY", {j.value for j in _judge_std("20260313001168-unisem-policy.txt", "1109")})


if __name__ == "__main__":
    unittest.main()
