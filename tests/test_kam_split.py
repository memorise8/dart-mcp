"""`dart_search_mcp.kam_split`에 대한 테스트.

Task A(finov2 `.superpowers/sdd/2026-09-02-dart-chat-ui/
task-A-kam-split-collector.md`): temis-backend `app/services/
dart_kam_split.py`(92줄) + `tests/test_dart_kam_split.py`(93줄)의 분할
테스트를 그대로 옮기고(`SplitKamItemsTests`), 신규 `item_kind` 판정 테스트를
더한다(`ItemKindClassificationTests` · `SplitKamItemsItemKindIntegrationTests`).
분할 동작 자체(마커 우선순위·최소 길이·제목 규칙)는 원본과 동일해야 하므로
옮긴 테스트도 기대값을 바꾸지 않는다.
"""

from __future__ import annotations

import unittest

from dart_search_mcp.kam_split import ItemKind, KamItem, classify_item_kind, split_kam_items

PREAMBLE = (
    "핵심감사사항은 우리의 전문가적 판단에 따라 당기 연결재무제표감사에서 가장 유의적인 사항들입니다. "
    "우리는 이런 사항에 대하여 별도의 의견을 제공하지는 않습니다. "
)
PAREN_SAMPLE = PREAMBLE + (
    "(1) 현금창출단위 손상검사 - 올레핀사업부 및 아로마틱사업부 연결회사는 손상징후가 존재한다고 판단하여 "
    "손상검사를 수행하였습니다. "
    "(2) 수익인식 회사는 고객과의 계약에서 생기는 수익을 인식하고 있습니다."
)
DOT_SAMPLE = (
    "1. 투입법에 따른 수익인식 총계약수익은 공사변경과 보상금 등 미래사건의 결과와 관련한 불확실성에 영향을 받습니다. "
    "2. 영업권 손상평가 회사는 매년 영업권에 대하여 손상검사를 수행하고 있습니다."
)
CIRCLED_SAMPLE = (
    "① 매출채권 손실충당금 회사는 연령분석에 기초하여 기대신용손실을 측정하고 있습니다. "
    "② 재고자산 순실현가능가치 회사는 재고자산을 취득원가와 순실현가능가치 중 낮은 금액으로 측정합니다."
)
SINGLE_SAMPLE = PREAMBLE + (
    "임대료수익의 발생사실 재무제표에 대한 주석 24에서 설명한 바와 같이 회사는 당기 중 "
    "8,070백만원의 임대료수익을 인식하였습니다."
)


class SplitKamItemsTests(unittest.TestCase):
    """temis-backend `tests/test_dart_kam_split.py`에서 그대로 옮긴 분할 테스트."""

    def test_splits_paren_markers(self) -> None:
        self.assertEqual([item.item_no for item in split_kam_items(PAREN_SAMPLE)], [1, 2])

    def test_preamble_is_dropped_when_markers_split(self) -> None:
        items = split_kam_items(PAREN_SAMPLE)
        self.assertTrue(all("전문가적 판단에 따라" not in item.body for item in items))

    def test_extracts_title_before_dash(self) -> None:
        self.assertEqual(split_kam_items(PAREN_SAMPLE)[0].title, "현금창출단위 손상검사")

    def test_title_is_whole_body_when_no_dash_and_short(self) -> None:
        self.assertEqual(
            split_kam_items(PAREN_SAMPLE)[1].title,
            "수익인식 회사는 고객과의 계약에서 생기는 수익을 인식하고 있습니다.",
        )

    def test_splits_numbered_dot_markers(self) -> None:
        items = split_kam_items(DOT_SAMPLE)
        self.assertEqual([item.item_no for item in items], [1, 2])
        self.assertTrue(items[1].title.startswith("영업권 손상평가"))

    def test_splits_circled_markers(self) -> None:
        items = split_kam_items(CIRCLED_SAMPLE)
        self.assertEqual([item.item_no for item in items], [1, 2])
        self.assertTrue(items[0].title.startswith("매출채권 손실충당금"))

    def test_single_item_when_no_markers(self) -> None:
        items = split_kam_items(SINGLE_SAMPLE)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_no, 1)
        self.assertTrue(items[0].title.startswith("임대료수익의 발생사실"))
        self.assertNotIn("전문가적 판단에 따라", items[0].body)

    def test_single_paren_marker_does_not_split(self) -> None:
        items = split_kam_items(
            "(1) 개발비 자산화 및 손상평가 - 회사는 내부창출 개발비의 자산화 요건 충족 여부를 검토하였습니다."
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "개발비 자산화 및 손상평가")

    def test_title_truncates_at_40_chars(self) -> None:
        items = split_kam_items("(1) " + "가" * 50)
        self.assertEqual(items[0].title, "가" * 40)
        self.assertEqual(items[0].body, "가" * 50)

    def test_returns_empty_for_blank_input(self) -> None:
        self.assertEqual(split_kam_items(""), [])
        self.assertEqual(split_kam_items("   "), [])

    def test_returns_empty_for_too_short_body(self) -> None:
        self.assertEqual(split_kam_items("핵심감사사항 없음"), [])

    def test_is_deterministic(self) -> None:
        self.assertEqual(split_kam_items(PAREN_SAMPLE), split_kam_items(PAREN_SAMPLE))
        self.assertIsInstance(split_kam_items(PAREN_SAMPLE)[0], KamItem)


class ItemKindClassificationTests(unittest.TestCase):
    """`classify_item_kind`(신규) — 실측 정형 문구·띄어쓰기 변형에 대한 판정.

    실측 근거: finov2 task-A 지시서(2026-09-02). 넥스트칩 2025 「계속기업관련
    공시의 적절성…」은 실제 KAM 선정 항목이므로 `kam`으로 남아야 한다(게이트
    ③) — going_concern_notice 정형 문구와 표현이 비슷해 보이지만 다르다.
    """

    def test_nextchip_style_title_is_kam(self) -> None:
        title = "계속기업관련 공시의 적절성 회사는 계속기업으로서의 존속능력에 관한 중요한 불확실성이 존재한다고 판단하였습니다"
        self.assertEqual(classify_item_kind(title), ItemKind.KAM)

    def test_going_concern_regular_form_is_notice(self) -> None:
        title = "우리는 계속기업 관련 중요한 불확실성 단락에 기술된 사항에 추가하여, 아래 사항을 핵심감사사항으로 결정하였습니다"
        self.assertEqual(classify_item_kind(title), ItemKind.GOING_CONCERN_NOTICE)

    def test_going_concern_spacing_variant_is_notice(self) -> None:
        # 이원컴포텍(2025): "단락 에"로 띄어쓰기된 변형. 정규화(공백 전부
        # 제거) 없이 매칭하면 놓친다.
        title = "우리는 계속기업 관련 중요한 불확실성 단락 에 기술된 사항에 추가하여, 아래 사항을 기재합니다"
        self.assertEqual(classify_item_kind(title), ItemKind.GOING_CONCERN_NOTICE)

    def test_qualified_opinion_basis_variant_is_notice(self) -> None:
        # 동진글로벌(2024) 변형: 「한정의견 근거 단락에 기술된 사항을 제외하고는」
        title = "한정의견 근거 단락에 기술된 사항을 제외하고는 다음 사항을 핵심감사사항으로 결정하였습니다"
        self.assertEqual(classify_item_kind(title), ItemKind.GOING_CONCERN_NOTICE)

    def test_selection_declaration_form_is_preamble(self) -> None:
        title = "핵심감사사항은 우리의 전문가적 판단에 따라 당기 재무제표감사에서 가장 유의적인 사항들입니다"
        self.assertEqual(classify_item_kind(title), ItemKind.PREAMBLE)

    def test_backlog_industry_declaration_form_is_preamble(self) -> None:
        title = "수주산업핵심감사사항은 우리의 전문가적 판단에 따라 당기 재무제표감사에서 가장 유의적인 사항들입니다"
        self.assertEqual(classify_item_kind(title), ItemKind.PREAMBLE)

    def test_communication_declaration_form_is_preamble(self) -> None:
        title = "우리는 아래에 기술된 사항을 이 감사보고서에서 커뮤니케이션할 핵심감사사항으로 결정하였습니다"
        self.assertEqual(classify_item_kind(title), ItemKind.PREAMBLE)

    def test_communication_mark_mid_sentence_is_preamble(self) -> None:
        title = "당기 감사에서 이사회와 커뮤니케이션할 핵심감사사항으로 다음 사항을 결정하였습니다"
        self.assertEqual(classify_item_kind(title), ItemKind.PREAMBLE)

    def test_response_fragment_form_stays_kam(self) -> None:
        # 유형 D(대응방법 조각, 134건) — 이번 범위 아님(고정 확인용). "핵심감사
        # 사항이"(조사 "은"이 아님)로 시작해 선정 선언 규칙에 걸리지 않는다.
        title = "핵심감사사항이 감사에서 다루어진 방법 핵심감사사항에 대응하기 위하여 우리는 다음과 같은 절차를 수행하였습니다"
        self.assertEqual(classify_item_kind(title), ItemKind.KAM)

    def test_is_deterministic(self) -> None:
        title = "우리는 계속기업 관련 중요한 불확실성 단락에 기술된 사항에 추가하여, 아래 사항을 핵심감사사항으로 결정하였습니다"
        self.assertEqual(classify_item_kind(title), classify_item_kind(title))


class SplitKamItemsItemKindIntegrationTests(unittest.TestCase):
    """`split_kam_items`가 실제로 각 항목에 `kind`를 붙이는지 확인한다."""

    def test_split_items_carry_kam_kind(self) -> None:
        items = split_kam_items(PAREN_SAMPLE)
        self.assertTrue(all(item.kind == ItemKind.KAM for item in items))

    def test_single_item_carries_kind_field(self) -> None:
        items = split_kam_items(SINGLE_SAMPLE)
        self.assertEqual(items[0].kind, ItemKind.KAM)


if __name__ == "__main__":
    unittest.main()
