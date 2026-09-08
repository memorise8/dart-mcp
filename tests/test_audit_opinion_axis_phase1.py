"""Phase 1 작업 2 — 감사의견 축 분리(재무제표 의견 vs 내부회계관리제도 의견) 회귀 테스트.

회계사 검수(analysis §2 A2): `audit_facts.jsonl`은 「한 공시 = 한 줄」이라
사업보고서/감사보고서에 재무제표 감사보고서와 내부회계관리제도 감사보고서가
함께 실리면 두 의견이 섞인다. 동성제약(20260407002635)의 「부적정」은
내부회계관리제도 의견이고 재무제표 의견은 적정이며, HC홈센타
(20260323001089)는 재무제표·내부회계 둘 다 적정이다. 아래 픽스처가 그 판정을
실제 공시 원문으로 고정한다.

## 픽스처 출처 (읽기 전용 원문에서 잘라낸 범위)

픽스처는 `dart_collected/docs/<rcept_no>/<rcept_no>_<코드>.xml`을
`dart_search_mcp.audit_xml_parser._strip_tags_to_text`로 평문화한 뒤 아래 char
범위를 각각 `strip()`해 공백 하나로 이어붙인 것이다. 잘라낸 구간은 전부
**별첨 재무제표·주석과 그에 준하는 수치표**뿐이고, 감사보고서 산문은 하나도
빼지 않았다.

| 픽스처 | 원문 XML | 평문 char 범위 | bytes |
|---|---|---|---|
| `20260407002635-dongsung-audit.txt` | `20260407002635_00760.xml` | `[0:2073] + [5541:12703] + [84791:87914]` | 30,093 |
| `20260323001089-hchomecenta-audit.txt` | `20260323001089_00760.xml` | `[0:4379] + [77233:79626]` | 16,062 |
| `20260323001089-hchomecenta-consolidated-audit.txt` | `20260323001089_00761.xml` | `[0:4612]` | 10,815 |
| `20260323001719-pangsky-audit.txt` | `20260323001719_00761.xml` | `[0:2788]` | 6,063 |
| `20260407003361-sambo-audit.txt` | `20260407003361_00761.xml` | `[0:4822]` | 11,785 |

- 동성제약: 정정신고 대비표 앞부분(`[0:2073]`) + 대비표의 (주8)~(주10)
  내부회계관리제도 항목과 실제 재무제표 감사보고서 본문(`[5541:12703]`,
  표지·목차부터 「(첨부)재 무 제 표」 직전까지) + 문서 말미의 내부회계관리제도
  감사보고서(`[84791:87914]`, 「내부회계관리제도 감사 또는 검토의견」부터
  운영실태보고서 이미지 참조 직전까지). 뺀 것은 대비표의 수치 블록
  ((주3)~(주7), `[2073:5541]`)과 별첨 재무제표·주석(`[12703:84791]`)뿐이다.
  이 픽스처는 수집기가 실제로 파싱한 결과(`audit_facts.jsonl`의
  `audit_opinion="unknown"`, 내부회계 문장이 그대로 들어간 `opinion_snippet`)를
  그대로 재현한다 - 「독립된 감사인의 감사보고서」 랜드마크가 정정표에서 5번
  먼저 나오고 내부회계 의견 문단도 정정표에 먼저 나오는, 구역 나누기가 가장
  어려운 형태다.
- HC홈센타: 같은 공시에서 **두 XML을 다 픽스처로 뒀다**. 의견 축 판정은
  **별도** 감사보고서(`_00760`)로 고정한다 — 수집기가 실제로 고른
  `_00761`(연결)에는 내부회계관리제도 감사보고서가 아예 없고(연결 XML은 연결
  재무제표 감사보고서만 담는다) 이 공시의 내부회계 의견은 `_00760`에만 있기
  때문이다. `[0:4379]`가 표지·목차·재무제표 감사보고서 본문,
  `[77233:79626]`이 내부회계관리제도 감사보고서다. 그리고 수집기가 실제로
  파싱하는 `_00761`(연결)은 **현재 결과가 틀렸다는 사실 자체를** 스위트에
  드러내려고 따로 잘라 뒀다(`...-consolidated-audit.txt`,
  `test_hchomecenta_consolidated_known_bad_until_task3`) — 원인은 의견 축이
  아니라 낱말 안에 들어간 줄바꿈(「표시하고 있습니 다」)이고, 그 정규화 수정은
  Task 3 몫이다.
- 팡스카이(의견거절)·삼보산업(한정): 수집기가 고른 `_00761`(연결)의 표지부터
  「(첨부)」 직전까지. 두 건 다 내부회계관리제도 감사보고서가 없어
  `icfr_opinion`은 빈 문자열이며, 이 Task가 의견거절·한정 분류를 건드리지
  않았음을 고정하는 회귀용이다(Task 3이 계속기업·강조사항으로 같은 픽스처를
  재사용한다).

네트워크·LLM을 쓰지 않는다."""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from dart_search_mcp import audit_xml_parser as p
from dart_search_mcp.tools.extract_facts import extract_audit_facts

from tests.phase1_util import fixture_meta, fixture_text, parse_fixture


class OpinionAxisPhase1(unittest.TestCase):
    def test_icfr_adverse_does_not_leak_into_fs_opinion(self) -> None:
        r = parse_fixture("20260407002635-dongsung-audit.txt", "[기재정정]사업보고서 (2024.12)")
        self.assertEqual(r.audit_opinion, "적정")
        self.assertEqual(r.opinion_status, "confirmed")
        self.assertEqual(r.icfr_opinion, "부적정")
        self.assertIn("내부회계관리제도", r.icfr_opinion_snippet)

    def test_two_opinion_paragraphs_still_confirm_fs_opinion(self) -> None:
        r = parse_fixture("20260323001089-hchomecenta-audit.txt")
        self.assertEqual(r.audit_opinion, "적정")
        self.assertEqual(r.opinion_status, "confirmed")
        self.assertEqual(r.icfr_opinion, "적정")

    def test_disclaimer_and_qualified_unchanged(self) -> None:
        self.assertEqual(parse_fixture("20260323001719-pangsky-audit.txt").audit_opinion, "의견거절")
        self.assertEqual(parse_fixture("20260407003361-sambo-audit.txt").audit_opinion, "한정")

    def test_version_and_count(self) -> None:
        r = parse_fixture("20260407002635-dongsung-audit.txt", "[기재정정]사업보고서 (2024.12)")
        self.assertEqual(r.parser_version, "af-v2")
        self.assertGreaterEqual(r.audit_report_count, 1)

    def test_hchomecenta_consolidated_known_bad_until_task3(self) -> None:
        """수집기가 실제로 파싱하는 `_00761`(연결)의 **현재 잘못된 결과**를 고정한다.

        이 XML의 의견 문장은 `...중요성의 관점에서 공정하게 표시하고 있습니 다.`로,
        낱말 「있습니다」 한가운데에 원문 줄바꿈이 들어가 있다. `_normalize_text`가
        줄바꿈을 공백 하나로 바꾸면서 판정 문구 「표시하고 있습니다」가 깨지고,
        그래서 재무제표 의견이 `unknown`으로 남는다 — **의견 축 문제가 아니라
        텍스트 정규화 문제**라 af-v2(Task 2)로는 고쳐지지 않는다.

        회계사가 지적한 「HC홈센타가 unknown」의 진짜 원인이 이것이므로, 고쳐지지
        않았다는 사실을 스위트에 드러내 둔다. **Task 3이 정규화를 고치면 이
        기대값은 `적정`/`confirmed`로 뒤집어야 한다** — 그때 이 테스트가 실패하는
        것이 정상이고, 실패가 곧 수정 완료 신호다."""

        r = parse_fixture("20260323001089-hchomecenta-consolidated-audit.txt")
        self.assertEqual(r.audit_opinion, "unknown")
        self.assertEqual(r.opinion_status, "unconfirmed")
        # 같은 공시의 별도 XML(_00760)은 af-v2에서 이미 제대로 나온다 - 대조군.
        self.assertEqual(parse_fixture("20260323001089-hchomecenta-audit.txt").audit_opinion, "적정")


# 합성 문서: 「내부회계관리제도」 보고서가 재무제표 감사보고서보다 **앞에** 오고,
# 그 표제가 랜드마크에 걸리지 않는 형태. 구역 나누기가 통하는 경우(A)와 통하지
# 않는 경우(B)를 모두 만든다. B는 실측 형태에서 따왔다 - 재무제표 감사보고서
# 표제가 「외부감사인의 감사보고서」인 공시가 실제로 있다(20260105000166).
_SYNTHETIC_ICFR_BLOCK = (
    "내부회계관리제도 운영실태 감사 결과 주식회사 가나다 주주 및 이사회 귀중 "
    '감사의견 우리는 2025년 12월 31일 현재 내부회계관리제도 설계 및 운영 개념체계에 근거한 '
    '주식회사 가나다(이하 "회사")의 내부회계관리제도를 감사하였습니다. '
    "우리의 의견으로는 부적정의견 근거 단락에 기술된 중요한 취약점의 영향 때문에 회사의 "
    "내부회계관리제도는 2025년 12월 31일 현재 효과적으로 설계 및 운영되고 있지 않습니다. "
    "부적정의견근거 우리는 대한민국의 회계감사기준에 따라 감사를 수행하였습니다. "
    "중요한 취약점은 내부회계관리제도 미비점 혹은 미비점들의 결합으로, 회사의 재무제표의 "
    "중요한 왜곡표시가 적시에 예방되거나 발견·수정되지 못할 가능성이 낮지 않은 경우에 "
    "존재합니다. "
)
_SYNTHETIC_FS_BODY = (
    '주식회사 가나다 주주 및 이사회 귀중 감사의견 우리는 주식회사 가나다(이하 "회사")의 '
    "재무제표를 감사하였습니다. 해당 재무제표는 2025년 12월 31일 현재의 재무상태표, 동일로 "
    "종료되는 보고기간의 포괄손익계산서, 자본변동표, 현금흐름표 그리고 중요한 회계정책 정보를 "
    "포함한 재무제표의 주석으로 구성되어 있습니다. 우리의 의견으로는 별첨된 회사의 재무제표는 "
    "회사의 2025년 12월 31일 현재의 재무상태와 동일로 종료되는 보고기간의 재무성과 및 "
    "현금흐름을 한국채택국제회계기준에 따라 중요성의 관점에서 공정하게 표시하고 있습니다. "
    "우리는 또한 대한민국의 회계감사기준에 따라 회사의 2025년 12월 31일 현재의 "
    "내부회계관리제도를 감사하였으며, 2026년 3월 20일자 감사보고서에서 부적정의견을 "
    "표명하였습니다. 감사의견근거 우리는 대한민국의 회계감사기준에 따라 감사를 수행하였습니다. "
)
_SYNTHETIC_META = {"rcept_no": "R_SYNTH", "report_name": "사업보고서 (2025.12)"}


class OpinionSentenceGuardPhase1(unittest.TestCase):
    """내부회계 의견 문장이 재무제표 의견 분류기에 들어가면 안 된다(브리프 Step 4).

    구역 나누기는 랜드마크에 의존하고 랜드마크는 문서마다 일정하지 않다. 그래서
    분류기에 넣는 텍스트를 「재무제표/연결재무제표를 가리키면서 판정 문구를 담은
    문장」으로 한정하는 문장 가드가 마지막 방어선이다."""

    def test_unrecognised_icfr_heading_before_fs_report_does_not_leak(self) -> None:
        r = p.parse_audit_text(_SYNTHETIC_ICFR_BLOCK + "독립된 감사인의 감사보고서 " + _SYNTHETIC_FS_BODY, _SYNTHETIC_META)

        self.assertEqual(r.audit_opinion, "적정")
        self.assertEqual(r.opinion_status, "confirmed")
        self.assertNotIn("내부회계관리제도", r.opinion_snippet)

    def test_sentence_guard_holds_when_carving_cannot_help(self) -> None:
        """재무제표 감사보고서 랜드마크조차 없어 구역 나누기가 못 도와주는 형태.

        af-v2 이전에는 의견 문장 폴백이 내부회계 판정 문장을 그대로 집어
        `unknown`이 됐다. 문장 가드가 그 문장을 건너뛰고 재무제표 의견 문장을
        찾는다. 다만 헤더 경로가 아니므로 확정값은 아니다(`unconfirmed`)."""

        r = p.parse_audit_text(_SYNTHETIC_ICFR_BLOCK + "외부감사인의 감사보고서 " + _SYNTHETIC_FS_BODY, _SYNTHETIC_META)

        self.assertEqual(r.audit_opinion, "적정")
        self.assertNotEqual(r.audit_opinion, "부적정")
        self.assertEqual(r.opinion_status, "unconfirmed")
        self.assertNotIn("내부회계관리제도", r.opinion_snippet)

    def test_icfr_verdict_sentence_is_never_the_classifier_input(self) -> None:
        para = (
            "우리의 의견으로는 별첨된 회사의 재무제표는 회사의 2025년 12월 31일 현재의 "
            "재무상태를 중요성의 관점에서 공정하게 표시하고 있습니다. "
            "우리의 의견으로는 회사의 내부회계관리제도는 2025년 12월 31일 현재 효과적으로 "
            "설계 및 운영되고 있지 않습니다."
        )

        guarded = p._fs_verdict_text(p._strip_icfr_sentences(para))

        self.assertIn("재무제표", guarded)
        self.assertNotIn("내부회계관리제도", guarded)
        self.assertEqual(p.classify_opinion(guarded), "적정")

    def test_verdict_group_keeps_following_comparative_period_sentence(self) -> None:
        """가드가 「문장 하나」가 아니라 「문장 무리」인 이유(신창모터스 20260413003086).

        당기 적정 문장 뒤에 비교표시 전기에 대한 한정 문장이 이어지는데, 그 문장은
        「재무제표」라는 낱말을 쓰지 않는다. 앞 문장만 분류기에 넣으면 한정이
        적정으로 바뀐다."""

        para = (
            "우리의 의견으로는 회사의 재무제표는 주식회사 신창모터스의 2025년 12월 31일 "
            "현재의 재무상태를 일반기업회계기준에 따라 중요성 관점에서 공정하게 표시하고 "
            "있습니다. 그리고, 아래문단에서 언급한 재고자산 등과 관련하여 만족할 수 있는 "
            "감사를 실시하였더라면 발견할 수도 있었던 수정사항의 영향을 제외하고는, "
            "2024년 12월 31일 현재의 재무상태를 일반기업회계기준에 따라 중요성의 관점에서 "
            "공정하게 표시하고 있습니다."
        )

        self.assertEqual(p.classify_opinion(p._fs_verdict_text(para)), "한정")


class OpinionAxisJsonlRowPhase1(unittest.TestCase):
    """새 열이 `dart extract-audit-facts`가 만드는 jsonl 행에도 실리는지.

    `extract_audit_facts`는 `cli.py`의 `extract-audit-facts`가 그대로 호출하는
    함수이고, 행 조립은 그 안의 `serialize_parsed_report`가 한다. 여기서는
    `--limit 1`에 해당하는 최소 실행(공시 1건)을 임시 디렉토리에서 돌린다."""

    _FIXTURE = "20260407002635-dongsung-audit.txt"

    def test_row_carries_new_opinion_axis_columns(self) -> None:
        meta = fixture_meta(self._FIXTURE, "[기재정정]사업보고서 (2024.12)")
        rcept_no = meta["rcept_no"]

        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "docs", rcept_no)
            os.makedirs(folder)
            with open(os.path.join(folder, f"{rcept_no}_00760.xml"), "wb") as f:
                f.write(fixture_text(self._FIXTURE).encode("utf-8"))

            manifest_path = os.path.join(tmp, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"records": [meta]}, f, ensure_ascii=False)

            output_path = os.path.join(tmp, "audit_facts.jsonl")
            summary = extract_audit_facts(manifest_path, os.path.join(tmp, "docs"), output_path)
            self.assertEqual(summary["parsed_ok"], 1)

            with open(output_path, encoding="utf-8") as f:
                rows = [json.loads(line) for line in f if line.strip()]

        self.assertEqual(len(rows), 1)
        row = rows[0]
        for key in ("opinion_status", "opinion_source", "icfr_opinion",
                    "icfr_opinion_snippet", "audit_report_count", "parser_version"):
            self.assertIn(key, row)
        self.assertEqual(row["audit_opinion"], "적정")
        self.assertEqual(row["opinion_status"], "confirmed")
        self.assertEqual(row["opinion_source"], "header")
        self.assertEqual(row["icfr_opinion"], "부적정")
        self.assertEqual(row["parser_version"], "af-v2")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
