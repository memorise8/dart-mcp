"""`dart_search_mcp.kam_taxonomy`에 대한 테스트.

Task 1(`.omo/plans/dart-kam-llm-tagging.md`): KAM(핵심감사사항) 원문 LLM
태깅의 순수 부분 — 고정 태소노미, 프롬프트 빌더, 응답 파서. 이 모듈은
LLM/네트워크/시계를 전혀 호출하지 않으므로 여기서도 그런 호출을 mock할
필요가 없다(순수 함수 대상 테스트).
"""

from __future__ import annotations

import unittest

from dart_search_mcp.kam_taxonomy import (
    KAM_TAXONOMY,
    VALID_TAGS,
    build_tagging_prompt,
    parse_tag_response,
)


class KamTaxonomyValidityTests(unittest.TestCase):
    def test_not_empty(self) -> None:
        self.assertGreater(len(KAM_TAXONOMY), 0)

    def test_no_duplicate_tags(self) -> None:
        tags = [tag for tag, _ in KAM_TAXONOMY]
        self.assertEqual(len(tags), len(set(tags)))

    def test_valid_tags_matches_taxonomy(self) -> None:
        self.assertEqual(VALID_TAGS, frozenset(tag for tag, _ in KAM_TAXONOMY))

    def test_all_tags_and_definitions_are_non_empty_strings(self) -> None:
        for tag, definition in KAM_TAXONOMY:
            self.assertIsInstance(tag, str)
            self.assertTrue(tag.strip())
            self.assertIsInstance(definition, str)
            self.assertTrue(definition.strip())


class BuildTaggingPromptTests(unittest.TestCase):
    def test_contains_all_taxonomy_tags_and_kam_raw(self) -> None:
        kam_raw = "핵심감사사항은 수익인식과 관련된 추정의 불확실성입니다."
        prompt = build_tagging_prompt(kam_raw)
        flattened = "\n".join(message["content"] for message in prompt)

        for tag, _ in KAM_TAXONOMY:
            self.assertIn(tag, flattened)
        self.assertIn(kam_raw, flattened)

    def test_contains_required_instructions(self) -> None:
        prompt = build_tagging_prompt("아무 원문")
        flattened = "\n".join(message["content"] for message in prompt)

        self.assertIn("목록에 있는 태그만", flattened)
        self.assertIn("JSON 배열", flattened)
        self.assertIn("[]", flattened)

    def test_is_pure_and_deterministic(self) -> None:
        kam_raw = "동일 입력"
        self.assertEqual(build_tagging_prompt(kam_raw), build_tagging_prompt(kam_raw))

    def test_message_shape_is_openai_chat_format(self) -> None:
        prompt = build_tagging_prompt("원문")
        self.assertIsInstance(prompt, list)
        for message in prompt:
            self.assertIn("role", message)
            self.assertIn("content", message)
            self.assertIn(message["role"], ("system", "user"))


class ParseTagResponseTests(unittest.TestCase):
    def test_plain_array(self) -> None:
        valid, dropped = parse_tag_response('["수익인식","계속기업"]')
        self.assertEqual(valid, ["수익인식", "계속기업"])
        self.assertEqual(dropped, [])

    def test_code_fenced_array(self) -> None:
        valid, dropped = parse_tag_response('```json\n["손상"]\n```')
        self.assertEqual(valid, ["손상"])
        self.assertEqual(dropped, [])

    def test_out_of_taxonomy_tag_is_dropped(self) -> None:
        valid, dropped = parse_tag_response('["수익인식","환율변동"]')
        self.assertEqual(valid, ["수익인식"])
        self.assertEqual(dropped, ["환율변동"])

    def test_empty_array(self) -> None:
        valid, dropped = parse_tag_response("[]")
        self.assertEqual(valid, [])
        self.assertEqual(dropped, [])

    def test_non_json_noise_does_not_raise(self) -> None:
        valid, dropped = parse_tag_response("모르겠습니다")
        self.assertEqual(valid, [])
        self.assertNotEqual(dropped, [])

    def test_duplicate_tags_are_deduplicated(self) -> None:
        valid, dropped = parse_tag_response('["손상","손상"]')
        self.assertEqual(valid, ["손상"])
        self.assertEqual(dropped, [])

    def test_valid_order_follows_taxonomy_order_not_input_order(self) -> None:
        # 입력 순서는 태소노미 순서와 반대이지만, valid는 태소노미 순서를 따라야 한다.
        valid, _ = parse_tag_response('["계속기업","수익인식"]')
        self.assertEqual(valid, ["수익인식", "계속기업"])

    def test_noise_around_array_is_tolerated(self) -> None:
        valid, dropped = parse_tag_response('여기 태그입니다: ["손상"] 이상입니다.')
        self.assertEqual(valid, ["손상"])
        self.assertEqual(dropped, [])

    def test_single_object_wrapper_is_handled_defensively(self) -> None:
        valid, dropped = parse_tag_response('{"tags": ["손상", "수익인식"]}')
        self.assertEqual(valid, ["수익인식", "손상"])
        self.assertEqual(dropped, [])

    def test_never_raises_on_arbitrary_garbage(self) -> None:
        for garbage in ("", "   ", "{", "[", "null", "12345", "```"):
            try:
                parse_tag_response(garbage)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"parse_tag_response raised on {garbage!r}: {exc}")

    def test_never_raises_on_pathological_deeply_nested_input(self) -> None:
        # Fix B: 매우 깊게 중첩된 대괄호 문자열은 CPython json 디코더가
        # RecursionError를 일으킨다("[" * 20000으로 재현 가능) - 이전에는
        # `(JSONDecodeError, ValueError)`만 잡아서 이런 예외가 새어나갔다.
        # 이 함수의 계약은 "never raise"이므로 예외 없이 실패 마커를
        # 반환해야 한다.
        try:
            valid, dropped = parse_tag_response("[" * 20000)
        except Exception as exc:  # noqa: BLE001
            self.fail(f"parse_tag_response raised on pathological input: {exc}")
        self.assertEqual(valid, [])
        self.assertEqual(len(dropped), 1)


if __name__ == "__main__":
    unittest.main()
