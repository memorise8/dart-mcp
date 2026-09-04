import json
import unittest
from dart_search_mcp.policy_blocks import PolicyBlock
from dart_search_mcp.policy_llm import judge_with_llm
from dart_search_mcp.policy_rules import TOPICS, UNDETERMINED


def _blk(text: str, std: str = "1118") -> PolicyBlock:
    return PolicyBlock(heading=text[:60], standard_no=std, topic_heading=None, start=0, text=text, capped=False)


class JudgeWithLlmTests(unittest.TestCase):
    spec = TOPICS["STD_EARLY_ADOPTION"]

    def test_비긍정값은_그대로_llm(self) -> None:
        fake = lambda messages, **kw: json.dumps({"label": "NOT_ADOPTED", "evidence": "영향을 평가 중"})
        j = judge_with_llm(self.spec, _blk("제1118호 … 회사는 영향을 평가 중입니다."), call_fn=fake)
        self.assertEqual((j.value, j.method, j.subject), ("NOT_ADOPTED", "llm", "1118"))

    def test_긍정값은_단정_패턴과_맞아야_채택(self) -> None:
        fake = lambda messages, **kw: json.dumps({"label": "EARLY_ADOPTED", "evidence": "비교정보는 재작성됩니다"})
        j = judge_with_llm(self.spec, _blk("제1118호 … 2026년 비교정보는 재작성됩니다."), call_fn=fake)
        self.assertEqual((j.value, j.method), (UNDETERMINED, "none"))

    def test_긍정값_검증_통과(self) -> None:
        fake = lambda messages, **kw: json.dumps({"label": "EARLY_ADOPTED", "evidence": "당사는 동 기준서를 조기적용하였습니다"})
        j = judge_with_llm(self.spec, _blk("제1109호 … 당사는 동 기준서를 조기적용하였습니다.", "1109"), call_fn=fake)
        self.assertEqual((j.value, j.method), ("EARLY_ADOPTED", "llm_verified"))

    def test_어휘_밖_라벨이나_깨진_JSON은_UNDETERMINED(self) -> None:
        for reply in ("{\"label\": \"MAYBE\"}", "판단할 수 없습니다"):
            j = judge_with_llm(self.spec, _blk("제1118호"), call_fn=lambda m, **kw: reply)
            self.assertEqual((j.value, j.method), (UNDETERMINED, "none"))

    def test_프롬프트에_기준서번호와_블록이_들어간다(self) -> None:
        seen: dict = {}
        def fake(messages, **kw):
            seen["content"] = messages[-1]["content"]; return json.dumps({"label": "NOT_ADOPTED", "evidence": "x"})
        judge_with_llm(self.spec, _blk("제1118호 본문"), call_fn=fake)
        self.assertIn("1118", seen["content"]); self.assertIn("제1118호 본문", seen["content"])

    def test_LLM_호출_실패는_UNDETERMINED(self) -> None:
        from dart_search_mcp.tools.kam_tagger import KamLlmError
        def boom(messages, **kw):
            raise KamLlmError("gateway down")
        j = judge_with_llm(self.spec, _blk("제1118호 본문"), call_fn=boom)
        self.assertEqual((j.value, j.method, j.evidence, j.subject), (UNDETERMINED, "none", "", "1118"))


class EvidenceAnchoringTests(unittest.TestCase):
    """R2: LLM 근거는 블록 원문에서 확인될 때만 채택하고, 채택되면 문장 전체로 넓힌다."""

    spec = TOPICS["STD_EARLY_ADOPTION"]

    def test_블록_안_구절이면_evidence가_그_구절을_품은_문장_전체가_된다(self) -> None:
        fake = lambda messages, **kw: json.dumps({"label": "NOT_ADOPTED", "evidence": "영향을 평가 중"})
        block = _blk("당사는 다른 기준서를 검토하였습니다. 회사는 제1118호를 아직 채택하지 않았으며 영향을 평가 중입니다.")
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertEqual((j.value, j.method), ("NOT_ADOPTED", "llm"))
        self.assertEqual(j.evidence, "회사는 제1118호를 아직 채택하지 않았으며 영향을 평가 중입니다.")

    def test_블록에_없는_구절이면_UNDETERMINED_none_빈_evidence(self) -> None:
        fake = lambda messages, **kw: json.dumps({"label": "NOT_ADOPTED", "evidence": "전혀 다른 구절입니다"})
        block = _blk("당사는 다른 기준서를 검토하였습니다. 회사는 제1118호를 아직 채택하지 않았으며 영향을 평가 중입니다.")
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertEqual((j.value, j.method, j.evidence), (UNDETERMINED, "none", ""))

    def test_긍정_라벨_앵커된_문장_안에_단정_패턴이_있으면_llm_verified(self) -> None:
        fake = lambda messages, **kw: json.dumps({"label": "EARLY_ADOPTED", "evidence": "조기적용하였습니다"})
        block = _blk("제1109호 개정. 조기적용이 허용됨에 따라 당사는 동 기준서를 조기적용하였습니다.", "1109")
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertEqual((j.value, j.method), ("EARLY_ADOPTED", "llm_verified"))
        self.assertIn("조기적용하였습니다", j.evidence)

    def test_문장_확장_후_검증하면_안된다_Q_C1_재현(self) -> None:
        """리뷰 Q-C1 재현: LLM 이 인용한 구절("제1118호는 조기적용이 허용됨")은 그 자체로는 단정
        패턴이 아니다. 같은 문장 안에 다른 기준서(제1116호)를 조기적용했다는 단정 문구가 있어도,
        검증은 인용구 자체로 해야 한다 — 문장 전체로 넓혀서 검증하면 제1116호 문장 조각이 제1118호
        행을 잘못 승인한다(수정 전엔 EARLY_ADOPTED/llm_verified 로 잘못 채택됨)."""
        fake = lambda messages, **kw: json.dumps(
            {"label": "EARLY_ADOPTED", "evidence": "제1118호는 조기적용이 허용됨"}
        )
        txt = (
            "기업회계기준서 제1118호 수익 당사는 제1116호를 조기적용하였으나 "
            "제1118호는 조기적용이 허용됨에 따라 적용 여부를 검토 중입니다."
        )
        block = _blk(txt, "1118")
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertEqual((j.value, j.method), (UNDETERMINED, "none"))


if __name__ == "__main__":
    unittest.main()
