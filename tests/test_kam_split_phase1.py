"""Phase 1 작업 1 — 핵심감사사항 항목 경계(`kam-v2`) 회귀 테스트.

회계사 검수(analysis §2 A1): 「핵심감사사항으로 결정된 이유」·「핵심감사사항이
감사에서 다루어진 방법」 소절은 **항목이 아니라 항목의 속성**이다. 항목 =
제목 한 줄 + 이유 + 절차. 아래 픽스처는 그 판정을 실제 공시 원문으로 고정한다.

## 픽스처 출처 (읽기 전용 원문에서 잘라낸 범위)

픽스처는 `dart_collected/docs/<rcept_no>/<rcept_no>_<코드>.xml` 을
`dart_search_mcp.audit_xml_parser._strip_tags_to_text` 로 평문화한 뒤
`extract_kam` 이 돌려준 구간을 그대로 저장한 것이다(수집기가 실제로 쓴
`doc_path` 문서를 골랐다). 처음 만들 때는 af-v1 산출물인
`dart_collected/audit_facts.jsonl` 의 `kam_raw` 와 바이트 단위로 같았고, Task 3 이
구역 끝 랜드마크 규칙을 고친 뒤에는 **af-v2 `extract_kam` 출력과 바이트 단위로
같다** — 9 건 중 3 건(비나텍·효성ITX·HC홈센타)에서 꼬리에 붙어 있던 다음 표제의
조각(「… 평가 연결」의 "연결")이 사라져 그만큼 짧아졌고, 그 3 건을 새 출력으로 다시
만들었다. 항목 분할 결과(아래 단언들)는 그대로다.

| 픽스처 | 원문 XML | 평문 char 범위 | 길이 |
|---|---|---|---|
| `20260323001579-koiz-kam.txt` | `20260323001579_00761.xml` | `[1730:2881]` | 1151 |
| `20260323000861-dongil-kam.txt` | `20260323000861_00760.xml` | `[1590:2438]` | 848 |
| `20260319001215-vinatech-kam.txt` | `20260319001215_00761.xml` | `[1613:2242]` | 629 |
| `20260407002635-dongsung-kam.txt` | `20260407002635_00760.xml` | `[8947:9823]` | 876 |
| `20260407003361-sambo-kam.txt` | `20260407003361_00761.xml` | `[1645:2372]` | 727 |
| `20260318001001-hyosungitx-kam.txt` | `20260318001001_00761.xml` | `[1262:2072]` | 810 |
| `20260323001089-hchomecenta-kam.txt` | `20260323001089_00761.xml` | `[1320:2219]` | 899 |
| `20260305000633-kyobo19spac-kam.txt` | `20260305000633_00760.xml` | `[1417:1583]` | 166 |
| `20250821000379-dongjin-kam.txt` | `20250821000379_00760.xml` | `[1506:2138]` | 632 |

앞 5건이 회계사가 틀렸다고 지적한 건이고, 그다음 2건(효성ITX·HC홈센타)은 현행
결과가 이미 정답(1건)인 회귀 고정용이다. 마지막 2건은 1차 검토(Important 1·2)
에서 드러난 「항목이 아닌데 항목이 된」 두 부류다 — 「핵심감사사항이 없다」
선언(교보19호스팩)과 계속기업 불확실성 문단(동진글로벌). 네트워크·LLM 을 쓰지
않는다.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dart_search_mcp.kam_split import (
    SPLITTER_VERSION,
    ItemKind,
    classify_item_kind,
    split_kam_items,
)

FX = Path(__file__).parent / "fixtures" / "phase1"


def _items(name: str) -> list:
    return split_kam_items((FX / name).read_text(encoding="utf-8"))


def _kam_titles(name: str) -> list[str]:
    return [item.title for item in _items(name) if item.kind == ItemKind.KAM]


class KamBoundaryPhase1(unittest.TestCase):
    def test_reason_and_procedure_subsections_are_not_items(self) -> None:
        self.assertEqual(len(_kam_titles("20260323000861-dongil-kam.txt")), 1)  # 재고자산의 평가
        self.assertEqual(len(_kam_titles("20260319001215-vinatech-kam.txt")), 1)  # 영업권 손상검사
        self.assertEqual(len(_kam_titles("20260407002635-dongsung-kam.txt")), 1)  # 환불부채 측정

    def test_going_concern_titled_item_under_kam_is_a_kam(self) -> None:
        titles = _kam_titles("20260323001579-koiz-kam.txt")
        self.assertEqual(len(titles), 2)
        self.assertTrue(any("계속기업" in title for title in titles))

    def test_qualified_opinion_report_has_one_kam(self) -> None:
        self.assertEqual(len(_kam_titles("20260407003361-sambo-kam.txt")), 1)  # 수익인식

    def test_regressions_keep_single_item(self) -> None:
        for name in ("20260318001001-hyosungitx-kam.txt", "20260323001089-hchomecenta-kam.txt"):
            self.assertEqual(len(_kam_titles(name)), 1, name)

    def test_titles_match_the_accountant_answer(self) -> None:
        # 소절 흡수의 부수 효과 고정: 제목이 「제목 한 줄」이 되고 소절 문구를
        # 삼키지 않는다. 회계사 정답(analysis §2 A1)의 제목 그대로다.
        self.assertEqual(
            _kam_titles("20260323001579-koiz-kam.txt"), ["현금창출단위의 손상검사", "계속기업가정"]
        )
        self.assertEqual(_kam_titles("20260323000861-dongil-kam.txt"), ["재고자산의 평가"])
        self.assertEqual(_kam_titles("20260319001215-vinatech-kam.txt"), ["영업권 손상검사"])
        self.assertEqual(_kam_titles("20260407002635-dongsung-kam.txt"), ["환불부채 측정"])
        self.assertEqual(_kam_titles("20260407003361-sambo-kam.txt"), ["수익인식"])

    def test_version_constant(self) -> None:
        self.assertEqual(SPLITTER_VERSION, "kam-v2")


class NotAnItemPhase1(unittest.TestCase):
    """항목이 아닌 것이 항목으로 새지 않는지 — 1차 검토 Important 1·2."""

    def test_no_kam_declaration_is_not_an_item(self) -> None:
        # 교보19호스팩 2025: 원문 전체가 「...보고해야 할 핵심감사사항이 없다고
        # 결정하였습니다」 선언뿐이다. 유형 A 도입 문장을 걷어낸 뒤 남는 본문이
        # 50자를 넘어 kam 으로 새던 부류(스팩 다수).
        items = _items("20260305000633-kyobo19spac-kam.txt")
        self.assertEqual(_kam_titles("20260305000633-kyobo19spac-kam.txt"), [])
        self.assertTrue(all(item.kind is ItemKind.PREAMBLE for item in items), items)

    def test_going_concern_uncertainty_paragraph_is_a_notice(self) -> None:
        # 동진글로벌 2024: 「다른 핵심감사사항이 없다」 + 계속기업 불확실성 문단.
        # 제목에는 계속기업 도입 표지가 없고 본문에만 있어, 본문을 봐야 잡힌다.
        items = _items("20250821000379-dongjin-kam.txt")
        self.assertEqual(_kam_titles("20250821000379-dongjin-kam.txt"), [])
        self.assertEqual([item.kind for item in items], [ItemKind.GOING_CONCERN_NOTICE])


class GoingConcernNarrowingPhase1(unittest.TestCase):
    """브리프 Step 4 의 좁히기 규칙 자체를 건너는 두 갈래 — 1차 검토 Important 3.

    같은 계속기업 도입 제목이라도 본문에 「결정된 이유」 소절이 있으면 이유와
    절차를 갖춘 실제 항목이므로 `kam` 이다.
    """

    TITLE = (
        "우리는 계속기업 관련 중요한 불확실성 단락에 기술된 사항에 추가하여, "
        "아래에 기술된 사항을 핵심감사사항으로 결정하였습니다"
    )

    def test_plain_body_stays_going_concern_notice(self) -> None:
        self.assertEqual(
            classify_item_kind(self.TITLE, self.TITLE), ItemKind.GOING_CONCERN_NOTICE
        )

    def test_body_with_reason_subsection_is_kam(self) -> None:
        body = self.TITLE + " 수익인식 핵심감사사항으로 결정된 이유 연결실체는 상장회사로서..."
        self.assertEqual(classify_item_kind(self.TITLE, body), ItemKind.KAM)


if __name__ == "__main__":
    unittest.main()
