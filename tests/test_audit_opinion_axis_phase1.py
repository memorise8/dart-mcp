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
- HC홈센타: **별도** 감사보고서(`_00760`)를 썼다. 수집기가 실제로 고른
  `_00761`(연결)에는 내부회계관리제도 감사보고서가 아예 없고(연결 XML은 연결
  재무제표 감사보고서만 담는다) 이 공시의 내부회계 의견은 `_00760`에만 있기
  때문이다. `[0:4379]`가 표지·목차·재무제표 감사보고서 본문,
  `[77233:79626]`이 내부회계관리제도 감사보고서다.
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
