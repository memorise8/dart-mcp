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


if __name__ == "__main__":
    unittest.main()
