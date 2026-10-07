import json
import unittest

from dart_search_mcp.topic_judge.judge import judge_window
from dart_search_mcp.topic_judge.topics import TOPIC_DEFS
from dart_search_mcp.topic_judge.window import Window

NH = "당사는 소액자산리스(가치가 낮은 소액 사무비품, IT 기기) 에 대해 인식면제 규정을 적용하고, 단기리스(리스기간이 12개월 이하인 리스)에 대해서는 인식면제 규정을 적용하지 않습니다."
W = Window(text=NH, spans=((0, len(NH)),), anchor="리스이용자")
LEASE = TOPIC_DEFS["LEASE_EXEMPTION"]


class JudgeWindowTests(unittest.TestCase):
    def test_정상_답은_사후검사를_거친다(self) -> None:
        prompts = []

        def fake(prompt: str) -> dict:
            prompts.append(prompt)
            return {"value": "LOW_VALUE", "evidence": NH, "subject": "self"}

        v = judge_window(LEASE, W, corp_name="농협금융지주", complete_json=fake)
        self.assertEqual((v.value, v.evidence, v.subject, v.held_reason), ("LOW_VALUE", NH, "self", None))
        self.assertEqual(len(prompts), 1)
        self.assertIn("[회사] 농협금융지주", prompts[0])
        self.assertIn(NH, prompts[0])

    def test_사후검사_실패도_전달된다(self) -> None:
        v = judge_window(LEASE, W, corp_name="x",
                         complete_json=lambda p: {"value": "BOTH", "evidence": NH, "subject": "standard_text"})
        self.assertEqual((v.value, v.held_reason), ("UNDETERMINED", "not_self"))

    def test_예외는_llm_error(self) -> None:
        def boom(prompt: str) -> dict:
            raise TimeoutError("gateway timeout")

        v = judge_window(LEASE, W, corp_name="x", complete_json=boom)
        self.assertEqual((v.value, v.held_reason, v.raw), ("UNDETERMINED", "llm_error", None))

    def test_문자열이나_value_없는_답은_parse(self) -> None:
        for reply in ("판단할 수 없습니다", json.dumps({"value": "BOTH"}), None, ["BOTH"], {"evidence": NH}):
            v = judge_window(LEASE, W, corp_name="x", complete_json=lambda p, r=reply: r)
            self.assertEqual((v.value, v.held_reason), ("UNDETERMINED", "parse"), reply)

    def test_값목록_밖은_invalid_value(self) -> None:
        v = judge_window(LEASE, W, corp_name="x",
                         complete_json=lambda p: {"value": "SEPARATED", "evidence": NH, "subject": "self"})
        self.assertEqual(v.held_reason, "invalid_value")


if __name__ == "__main__":
    unittest.main()
