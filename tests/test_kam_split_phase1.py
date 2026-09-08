"""Phase 1 작업 1 — 핵심감사사항 항목 경계(`kam-v2`) 회귀 테스트.

회계사 검수(analysis §2 A1): 「핵심감사사항으로 결정된 이유」·「핵심감사사항이
감사에서 다루어진 방법」 소절은 **항목이 아니라 항목의 속성**이다. 항목 =
제목 한 줄 + 이유 + 절차. 아래 픽스처는 그 판정을 실제 공시 원문으로 고정한다.

## 픽스처 출처 (읽기 전용 원문에서 잘라낸 범위)

픽스처는 `dart_collected/docs/<rcept_no>/<rcept_no>_<코드>.xml` 을
`dart_search_mcp.audit_xml_parser._strip_tags_to_text` 로 평문화한 뒤
`extract_kam` 이 돌려준 구간을 그대로 저장한 것이다(수집기가 실제로 쓴
`doc_path` 문서를 골랐고, 7건 모두 `dart_collected/audit_facts.jsonl` 의
`kam_raw` 와 바이트 단위로 같음을 확인했다).

| 픽스처 | 원문 XML | 평문 char 범위 | 길이 |
|---|---|---|---|
| `20260323001579-koiz-kam.txt` | `20260323001579_00761.xml` | `[1730:2881]` | 1151 |
| `20260323000861-dongil-kam.txt` | `20260323000861_00760.xml` | `[1590:2438]` | 848 |
| `20260319001215-vinatech-kam.txt` | `20260319001215_00761.xml` | `[1613:2245]` | 632 |
| `20260407002635-dongsung-kam.txt` | `20260407002635_00760.xml` | `[8947:9823]` | 876 |
| `20260407003361-sambo-kam.txt` | `20260407003361_00761.xml` | `[1645:2372]` | 727 |
| `20260318001001-hyosungitx-kam.txt` | `20260318001001_00761.xml` | `[1262:2075]` | 813 |
| `20260323001089-hchomecenta-kam.txt` | `20260323001089_00761.xml` | `[1321:2223]` | 902 |

앞 5건이 회계사가 틀렸다고 지적한 건이고, 뒤 2건(효성ITX·HC홈센타)은 현행
결과가 이미 정답(1건)인 회귀 고정용이다. 네트워크·LLM 을 쓰지 않는다.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from dart_search_mcp.kam_split import SPLITTER_VERSION, ItemKind, split_kam_items

FX = Path(__file__).parent / "fixtures" / "phase1"


def _kam_titles(name: str) -> list[str]:
    items = split_kam_items((FX / name).read_text(encoding="utf-8"))
    return [item.title for item in items if item.kind == ItemKind.KAM]


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


if __name__ == "__main__":
    unittest.main()
