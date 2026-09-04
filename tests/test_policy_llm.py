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
        """S-M3: evidence 는 픽스처 블록 안에 실제로 있는 구절이어야 한다 — 그래야 나중에 앵커
        성공 경로가 망가져도(예: _locate 리팩터 실수) 이 테스트가 evidence 로 잡아낸다."""
        fake = lambda messages, **kw: json.dumps({"label": "NOT_ADOPTED", "evidence": "영향을 평가 중"})
        block = _blk("제1118호 … 회사는 영향을 평가 중입니다.")
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertEqual((j.value, j.method, j.subject), ("NOT_ADOPTED", "llm", "1118"))
        self.assertEqual(j.evidence, "제1118호 … 회사는 영향을 평가 중입니다.")

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
        """S-M3: evidence "x" 는 블록에 없어 예전엔 이 테스트가 (프롬프트만 단언하므로 무해하게)
        앵커 실패 경로로 흘러갔다. 블록 안 실제 구절로 바꾸고 반환값도 같이 확인한다."""
        seen: dict = {}
        def fake(messages, **kw):
            seen["content"] = messages[-1]["content"]
            return json.dumps({"label": "NOT_ADOPTED", "evidence": "본문 내용을 검토 중"})
        block = _blk("제1118호 본문 내용을 검토 중입니다.")
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertIn("1118", seen["content"]); self.assertIn("제1118호 본문 내용을 검토 중입니다.", seen["content"])
        self.assertEqual((j.value, j.method), ("NOT_ADOPTED", "llm"))
        self.assertIn("검토 중", j.evidence)

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

    def test_블록에_개행과_연속_공백이_있어도_앵커된다_Q_M4(self) -> None:
        """구현자 보고가 "브리프에 없는 구현 세부"로 꼽은 _normalize_with_map 의 공백 축약 분기가
        지금까지 단일 공백 픽스처로만 테스트돼 미검증이었다(Q-M4). 개행·연속 공백이 있는 블록에서도
        LLM 이 돌려준(단일 공백) 인용구가 앵커되는지 직접 고정한다."""
        fake = lambda messages, **kw: json.dumps(
            {"label": "NOT_ADOPTED", "evidence": "제1118호를 아직 채택하지 않았으며"}
        )
        block = _blk("당사는\n\n  다른   기준서를 검토하였습니다. 회사는   제1118호를    아직  채택하지 않았으며 영향을 평가 중입니다.")
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertEqual((j.value, j.method), ("NOT_ADOPTED", "llm"))
        self.assertIn("채택하지 않았으며", j.evidence)

    def test_20자_폴백으로_앵커된다_Q_M5(self) -> None:
        """리뷰가 직접 재현해 정확함을 확인한 예시를 그대로 고정한다: LLM 인용구 전체는 블록에
        없지만(뒷부분이 의역됨) 앞 20자는 원문과 그대로 일치 — 20자 폴백으로 앵커돼야 한다."""
        from dart_search_mcp.policy_llm import _locate

        text = "회사는 제1118호를 아직 채택하지 않았으며 영향을 평가 중입니다."
        quote = "회사는 제1118호를 아직 채택하지 않았으나 향후 영향을 검토할 예정입니다."  # 20자 이후 의역
        self.assertNotIn(quote, text)  # 전체 일치로는 못 찾는다 — 폴백이 실제로 필요한 상황인지 확인
        anchor = _locate(text, quote)
        self.assertIsNotNone(anchor)
        index, matched_len = anchor
        self.assertEqual(index, 0)
        self.assertEqual(matched_len, 20)  # 폴백은 20자에 대응하는 원문 길이만 돌려준다(N1)

        # NOT_ADOPTED 는 긍정값이 아니므로 폴백 앵커도 그대로 받는다(N1 은 긍정값만 제한한다).
        fake = lambda messages, **kw: json.dumps({"label": "NOT_ADOPTED", "evidence": quote})
        block = _blk(text)
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertEqual((j.value, j.method), ("NOT_ADOPTED", "llm"))
        self.assertNotEqual(j.evidence, "")

    def test_긍정값은_20자_폴백_앵커를_받지_않는다_N1_재현(self) -> None:
        """재리뷰 N1 재현: quoted 를 LLM 응답 그대로 검증하면, 20자 폴백 앵커(앞부분만 원문
        일치)에서 21자 이후를 LLM 이 지어내도(원문에 없어도) 단정 패턴 검증을 통과했다 —
        값과 모순되는 근거("영향을 검토 중입니다")가 EARLY_ADOPTED/llm_verified 로 채택되는
        위양성이었다. 지금은 긍정값에 한해 폴백을 거부해 UNDETERMINED/none/"unanchored" 여야 한다."""
        orig = "기업회계기준서 제1118호 수익 회사는 동 기준서의 적용이 재무제표에 미치는 영향을 검토 중입니다."
        quote = "기업회계기준서 제1118호 수익 회사는 동 기준서를 조기적용하였습니다"  # 뒷부분 날조, 원문에 없음
        self.assertNotIn(quote, orig)
        fake = lambda messages, **kw: json.dumps({"label": "EARLY_ADOPTED", "evidence": quote})
        block = _blk(orig, "1118")
        tr: dict = {}
        j = judge_with_llm(self.spec, block, call_fn=fake, trace=tr)
        self.assertEqual((j.value, j.method, j.evidence), (UNDETERMINED, "none", ""))
        self.assertEqual(tr.get("reason"), "unanchored")

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


class LabelSupportGuardTests(unittest.TestCase):
    """V7 R2: 비긍정 라벨(값이 spec.positive_values 밖이고 UNDETERMINED 도 아님)은 block.text 에
    그 값을 뒷받침하는 키워드가 하나도 없으면 채택하지 않는다."""

    spec = TOPICS["STD_EARLY_ADOPTION"]

    def test_뒷받침_키워드가_블록에_없으면_unsupported(self) -> None:
        fake = lambda messages, **kw: json.dumps(
            {"label": "PERMITTED_ONLY", "evidence": "이 기준서의 제정에따라"}
        )
        block = _blk("이 기준서의 제정에따라")
        tr: dict = {}
        j = judge_with_llm(self.spec, block, call_fn=fake, trace=tr)
        self.assertEqual((j.value, j.method, j.evidence), (UNDETERMINED, "none", ""))
        self.assertEqual(tr.get("reason"), "unsupported")

    def test_뒷받침_키워드가_블록에_있으면_llm_채택(self) -> None:
        fake = lambda messages, **kw: json.dumps(
            {"label": "PERMITTED_ONLY", "evidence": "이 기준서의 제정에따라"}
        )
        block = _blk("이 기준서의 제정에따라 조기적용이 허용됩니다.")
        j = judge_with_llm(self.spec, block, call_fn=fake)
        self.assertEqual((j.value, j.method), ("PERMITTED_ONLY", "llm"))


if __name__ == "__main__":
    unittest.main()
