"""Phase 1 작업 3 — 계속기업 기재 위치·시점, 강조사항 상태, 구역 절단 회귀 테스트.

회계사 검수(analysis §2 A3·A4): `going_concern`(bool) 한 칸으로는 「계속기업
불확실성이 어디에 어떻게 적혀 있는지」를 못 담는다. 실제 감사보고서는 셋 중
하나다 — ① 「계속기업 관련 중요한 불확실성」이라는 **별도 구역**, ② 의견근거
단락(의견거절근거·한정의견근거 등) **안**, ③ 그 외 본문. 게다가 **전기(前期)
감사보고서를 가리키는 문장**은 당기 기재가 아닌데도 지금까지 「있음」으로
집계될 수 있었다. 이 모듈이 그 판정을 실제 공시 원문으로 고정한다.

## 픽스처 출처 (읽기 전용 원문에서 잘라낸 범위)

픽스처는 `dart_collected/docs/<rcept_no>/<rcept_no>_<코드>.xml`을 (Task 2 시점의)
`dart_search_mcp.audit_xml_parser._strip_tags_to_text`로 평문화한 뒤 아래 char
범위를 `strip()`한 것이다. 범위는 전부 **문서 처음부터 「(첨부)」 직전까지** —
감사보고서 산문은 한 줄도 빼지 않았고, 잘라낸 것은 별첨 재무제표·주석뿐이다.
수집기가 실제로 고르는 XML(연결 `_00761` > 별도 `_00760`)을 그대로 썼다.

| 픽스처 | 원문 XML | 평문 char 범위 | bytes |
|---|---|---|---|
| `20251219000526-stx-audit.txt` | `20251219000526_00761.xml` | `[0:2783]` | 5,975 |
| `20260413000920-stx-current-audit.txt` | `20260413000920_00760.xml` | `[0:1726]` | 4,097 |
| `20260323001579-koiz-audit.txt` | `20260323001579_00761.xml` | `[0:5066]` | 11,760 |
| `20260319001215-vinatech-audit.txt` | `20260319001215_00761.xml` | `[0:4582]` | 11,241 |
| `20260323001749-dynamic-audit.txt` | `20260323001749_00761.xml` | `[0:5214]` | 12,624 |

팡스카이(`20260323001719`)·삼보산업(`20260407003361`)·동성제약(`20260407002635`)·
HC홈센타(`20260323001089`) 픽스처는 Task 2가 만든 것을 그대로 재사용한다(출처
표는 `tests/test_audit_opinion_axis_phase1.py` 상단).

네트워크·LLM을 쓰지 않는다.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from dart_search_mcp.audit_xml_parser import parse_audit_text
from dart_search_mcp.tools.extract_facts import deserialize_parsed_report, extract_audit_facts

from tests.phase1_util import fixture_meta, fixture_text, parse_fixture


class SectionLandmarkPhase1(unittest.TestCase):
    """구역 끝 랜드마크는 **표제 자리**에서만 인정한다(컨트롤러 추가 범위 1).

    핵심감사사항 서문에는 표준 문안 「우리는 한정의견근거 단락에 기술된 사항에
    추가하여, 아래에 기술된 사항을 … 핵심감사사항으로 결정하였습니다」가 온다
    (한국공인회계사회 「감사보고서 작성사례」 6.2·5.1). 문장 **안**의
    「한정의견근거」를 구역 경계로 보면 핵심감사사항 구역이 「우리는」에서 잘린다.
    실제로 잘린 공시: 20260323001749(다이나믹디자인, 한정), 20260406004560
    (용마산건설, 한정), 20260506000528(동성제약, 적정)."""

    def test_kam_section_is_not_truncated_at_cross_reference(self) -> None:
        r = parse_fixture("20260323001749-dynamic-audit.txt")

        self.assertTrue(r.kam_present)
        self.assertFalse(r.kam_raw.rstrip().endswith("우리는"))
        # 서문 다음의 실제 핵심감사사항 항목 본문이 들어와야 한다.
        self.assertIn("몰드사업부", r.kam_raw)
        self.assertIn("핵심감사사항이 감사에서 다루어진 방법", r.kam_raw)
        # 그러면서도 다음 표제에서는 정확히 끊긴다.
        self.assertNotIn("경영진과 지배기구의 책임", r.kam_raw)

    def test_section_ends_at_heading_even_when_item_has_no_period(self) -> None:
        """반대 방향 회귀 - 표제 규칙을 넓게 걸면 구역이 **끝나지 않는다**.

        핵심감사사항 항목의 감사절차 목록은 마침표 없이 명사로 끝나는 것이 보통이라
        (「… 변수의 합리성 평가」, 「… 외부조회 확인」), 바로 뒤에 오는 표제의 앞
        글자가 한글이 된다. 「…경영진과 지배기구의 책임」·「기타사항」까지 표제
        자리에서만 인정하면 그 표제들을 놓쳐 구역이 다음 구역들을 삼킨다(실측
        kam_present 400건: 책임 문구 오염 1→246건, 기타/강조 구역 삼킴 0→86건)."""

        vinatech = parse_fixture("20260319001215-vinatech-audit.txt")
        # 「… 합리성 평가 연결재무제표에 대한 경영진과 지배기구의 책임 …」
        self.assertIn("영업권 손상검사", vinatech.kam_raw)
        self.assertTrue(vinatech.kam_raw.rstrip().endswith("합리성 평가"))
        self.assertNotIn("경영진과 지배기구의 책임", vinatech.kam_raw)

        sambo = parse_fixture("20260407003361-sambo-audit.txt")
        # 「… 외부조회 확인 기타사항 삼보산업주식회사와 그 종속기업들의 …」
        self.assertTrue(sambo.kam_raw.rstrip().endswith("외부조회 확인"))
        self.assertNotIn("기타사항", sambo.kam_raw)
        self.assertEqual(sambo.other_matter_status, "present")

    def test_synthetic_item_without_period_before_responsibility_heading(self) -> None:
        """같은 형태를 작성사례 6.1 골격의 합성 문서로도 고정한다."""

        text = (
            "독립된 감사인의 감사보고서 감사의견 우리의 의견으로는 별첨된 회사의 재무제표는 "
            "중요성의 관점에서 공정하게 표시하고 있습니다. 핵심감사사항 핵심감사사항은 우리의 "
            "전문가적 판단에 따라 당기 재무제표감사에서 가장 유의적인 사항들입니다. "
            "매출채권의 회수가능성 평가 가. 핵심감사사항으로 결정한 이유 회수가능성 평가에는 "
            "경영진의 유의적인 판단이 수반됩니다. 나. 감사에서 다루어진 방법 - 연령분석의 "
            "정확성 검증- 표본추출을 통한 외부조회 확인 "
            "재무제표에 대한 경영진과 지배기구의 책임 경영진은 재무제표를 작성할 때, 회사의 "
            "계속기업으로서의 존속능력을 평가할 책임이 있습니다."
        )

        r = parse_audit_text(text, {"rcept_no": "R_SYNTH_KAM", "report_name": "감사보고서 (2025.12)"})

        self.assertIn("매출채권의 회수가능성 평가", r.kam_raw)
        self.assertTrue(r.kam_raw.rstrip().endswith("외부조회 확인"))
        self.assertNotIn("경영진과 지배기구의 책임", r.kam_raw)

    def test_opinion_axis_unchanged_for_truncated_report(self) -> None:
        """구역 경계 규칙을 바꿔도 의견 축(Task 2)은 그대로여야 한다."""

        r = parse_fixture("20260323001749-dynamic-audit.txt")

        self.assertEqual(r.audit_opinion, "한정")
        self.assertEqual(r.opinion_status, "confirmed")


class GoingConcernPhase1(unittest.TestCase):
    """계속기업 불확실성이 **어디에** 적혀 있고 **어느 기간**의 것인지.

    회계사 확인(analysis §2 A3):

    - 팡스카이 20260323001719 — 의견거절근거 「(1) 계속기업가정에 대한
      불확실성」 **안**에 있다(af-v1은 아예 못 잡아 「없음」이었다).
    - 삼보산업 20260407003361 — 계속기업 내용이 「한정의견근거」 단락 안에 있고
      별도 표제가 없다. af-v1이 근거로 잡은 문단은 **핵심감사사항 서문**이었다.
    - 코이즈 20260323001579 — 별도 구역(회귀 고정).
    - STX마린서비스 — 당기 보고서에 계속기업 기재가 없고 **전기 감사보고서를
      언급한 문장**뿐인 형태.
    """

    def test_within_basis_for_opinion_is_disclosed(self) -> None:
        r = parse_fixture("20260323001719-pangsky-audit.txt")

        self.assertEqual(r.gc_material_uncertainty, "disclosed")
        self.assertEqual(r.gc_location, "within_basis_for_opinion")
        self.assertTrue(r.going_concern)
        self.assertIn("계속기업", r.going_concern_snippet)
        # 「기타사항」의 전기 타감사인 언급(계속기업가정의 중요한 불확실성을 사유로
        # 의견거절)이 근거로 잡히면 안 된다 - 당기 기재가 아니다.
        self.assertNotIn("타감사인", r.going_concern_snippet)

    def test_prior_year_reference_is_not_current_disclosure(self) -> None:
        """당기 본문에는 계속기업 기재가 없고 전기 감사보고서만 언급한 보고서.

        STX마린서비스의 **당기**(2025.12) 감사보고서다. 「기타사항」에
        「회사의 2024년 12월 31일로 종료되는 보고기간의 재무제표는 삼일회계법인에서
        감사하였으며, 2025년 12월 19일자 감사보고서에서 … 계속기업가정 불확실성으로
        의견거절을 표명하였습니다」가 있을 뿐이다(한국공인회계사회 「감사보고서
        작성사례」 10.6.1의 전기 타감사인 문안과 같은 꼴). 그 문장에는 「전기」·
        「타감사인」 같은 표지가 없으므로 **보고기간 연도 대조**로 걸러야 한다."""

        r = parse_fixture("20260413000920-stx-current-audit.txt", "감사보고서 (2025.12)")

        self.assertEqual(r.gc_material_uncertainty, "not_disclosed")
        self.assertEqual(r.gc_location, "")
        self.assertFalse(r.going_concern)
        self.assertEqual(r.going_concern_snippet, "")

    def test_referenced_prior_report_itself_discloses_current_uncertainty(self) -> None:
        """위 문장이 가리키는 **전기 보고서 자체**(20251219000526)는 「있음」이다.

        브리프는 이 rcept를 「전기 언급뿐」인 사례로 적었지만, 원문을 열어 보면
        의견거절근거가 「(1) 계속기업 관련 중요한 불확실성」으로 시작하는 당기
        기재다 — 회계사가 말한 「삼일, 2025-12-19 의견거절」은 이 보고서를
        **가리키는** 문장(위 테스트의 20260413000920)이고 이 보고서가 아니다.
        의견근거 단락 안의 번호매김 소제목이므로 별도 구역이 아니라
        `within_basis_for_opinion`이다(작성사례 5.3~5.8과 같은 꼴)."""

        r = parse_fixture("20251219000526-stx-audit.txt", "연결감사보고서 (2024.12)")

        self.assertEqual(r.gc_material_uncertainty, "disclosed")
        self.assertEqual(r.gc_location, "within_basis_for_opinion")
        self.assertIn("계속기업", r.going_concern_snippet)

    def test_qualified_report_gc_inside_basis_not_kam_preamble(self) -> None:
        r = parse_fixture("20260407003361-sambo-audit.txt")

        self.assertEqual(r.gc_material_uncertainty, "disclosed")
        self.assertEqual(r.gc_location, "within_basis_for_opinion")
        self.assertNotIn("핵심감사사항으로 결정하였습니다", r.going_concern_snippet)
        self.assertIn("계속기업가정의 불확실성", r.going_concern_snippet)

    def test_separate_section_regression(self) -> None:
        r = parse_fixture("20260323001579-koiz-audit.txt")

        self.assertEqual(r.gc_material_uncertainty, "disclosed")
        self.assertEqual(r.gc_location, "separate_section")
        self.assertTrue(r.going_concern)
        self.assertIn("주석 33", r.going_concern_snippet)

    def test_reports_without_going_concern_are_not_disclosed(self) -> None:
        """경영진·감사인 책임 보일러플레이트만 있는 보고서는 「없음」이다.

        두 픽스처 다 「계속기업으로서의 존속능력을 평가하고」·「유의적 의문을
        **초래할 수 있는**」 같은 가정법 보일러플레이트를 담고 있다 - 이것으로
        「있음」을 내면 사실상 모든 보고서가 계속기업 위험 보고서가 된다."""

        for fixture in ("20260319001215-vinatech-audit.txt", "20260323001749-dynamic-audit.txt"):
            with self.subTest(fixture=fixture):
                r = parse_fixture(fixture)
                self.assertEqual(r.gc_material_uncertainty, "not_disclosed")
                self.assertEqual(r.gc_location, "")
                self.assertFalse(r.going_concern)

    def test_going_concern_bool_mirrors_material_uncertainty(self) -> None:
        for fixture, expected in (
            ("20260323001719-pangsky-audit.txt", True),
            ("20260323001579-koiz-audit.txt", True),
            ("20260319001215-vinatech-audit.txt", False),
        ):
            with self.subTest(fixture=fixture):
                r = parse_fixture(fixture)
                self.assertEqual(r.going_concern, r.gc_material_uncertainty == "disclosed")
                self.assertEqual(r.going_concern, expected)


class EmphasisAndOtherMatterPhase1(unittest.TestCase):
    """강조사항/기타사항은 「찾았다/안 찾았다」가 아니라 **상태**로 낸다.

    회계사 확인(analysis §2 A4): 동성제약 20260407002635은 강조사항이 있는데도
    af-v1 결과가 「없음」이었다 - 정정신고 대비표의 「강조사항 (주1)」이 먼저 걸려
    자리표시자만 뽑혔기 때문이다. 비나텍 20260319001215은 있음(회귀 고정),
    코이즈 20260323001579은 없음."""

    def test_emphasis_status(self) -> None:
        self.assertEqual(
            parse_fixture("20260407002635-dongsung-audit.txt", "[기재정정]사업보고서 (2024.12)").emphasis_status,
            "present",
        )
        self.assertEqual(parse_fixture("20260319001215-vinatech-audit.txt").emphasis_status, "present")
        self.assertEqual(parse_fixture("20260323001579-koiz-audit.txt").emphasis_status, "absent")

    def test_emphasis_raw_skips_correction_table_placeholder(self) -> None:
        r = parse_fixture("20260407002635-dongsung-audit.txt", "[기재정정]사업보고서 (2024.12)")

        self.assertIn("주석 39", r.emphasis_raw)
        self.assertNotIn("(주1)", r.emphasis_raw)

    def test_other_matter_status(self) -> None:
        self.assertEqual(parse_fixture("20260407003361-sambo-audit.txt").other_matter_status, "present")
        self.assertEqual(parse_fixture("20260323001749-dynamic-audit.txt").other_matter_status, "present")
        # 「…모든 관계와 기타사항들 및…」은 감사인의 책임 보일러플레이트이지 구역이 아니다.
        self.assertEqual(parse_fixture("20260323001579-koiz-audit.txt").other_matter_status, "absent")
        self.assertEqual(parse_fixture("20260319001215-vinatech-audit.txt").other_matter_status, "absent")

    def test_status_is_unknown_without_body_text(self) -> None:
        r = parse_audit_text("", {"rcept_no": "R_EMPTY"})

        self.assertEqual(r.emphasis_status, "unknown")
        self.assertEqual(r.other_matter_status, "unknown")
        self.assertEqual(r.gc_material_uncertainty, "unknown")
        self.assertEqual(r.gc_location, "unknown")


class GoingConcernJsonlRowPhase1(unittest.TestCase):
    """새 열이 `dart extract-audit-facts`가 만드는 jsonl 행에도 실리는지.

    Task 2의 `OpinionAxisJsonlRowPhase1`과 같은 방식으로 임시 디렉토리에서
    공시 1건짜리 최소 실행을 돌린다(네트워크 없음)."""

    _FIXTURE = "20260323001579-koiz-audit.txt"

    def test_row_carries_new_going_concern_and_section_columns(self) -> None:
        meta = fixture_meta(self._FIXTURE)
        rcept_no = meta["rcept_no"]

        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "docs", rcept_no)
            os.makedirs(folder)
            with open(os.path.join(folder, f"{rcept_no}_00761.xml"), "wb") as f:
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
        self.assertEqual(row["gc_material_uncertainty"], "disclosed")
        self.assertEqual(row["gc_location"], "separate_section")
        self.assertEqual(row["emphasis_status"], "absent")
        self.assertEqual(row["other_matter_status"], "absent")
        # 기존 열은 그대로 - going_concern은 gc_material_uncertainty의 축약이다.
        self.assertTrue(row["going_concern"])
        self.assertEqual(row["audit_opinion"], "적정")

    def test_legacy_row_without_new_columns_deserializes_conservatively(self) -> None:
        """af-v2 이전 jsonl 행에는 이 열들이 없다 - 있는 척하면 안 된다."""

        restored = deserialize_parsed_report({"rcept_no": "R_LEGACY", "going_concern": True})

        self.assertEqual(restored.gc_material_uncertainty, "")
        self.assertEqual(restored.gc_location, "")
        self.assertEqual(restored.emphasis_status, "")
        self.assertEqual(restored.other_matter_status, "")
        self.assertTrue(restored.going_concern)


# 한국공인회계사회 「감사보고서 작성사례」(2018.10)의 표준 문안으로 만든 합성
# 보고서. 실측 코퍼스 픽스처를 대신하려는 것이 아니라(자리표시자 ABC주식회사·
# 20X1년), 위치 규칙이 **표준 문안 자체**에 대해서도 성립하는지 보는 대조군이다.
# 연도 자리표시자만 실제 연도(20X1→2025, 20X0→2024)로, 금액은 임의 값으로 바꿨다.
_KICPA_51_SEPARATE_SECTION = (
    "독립된 감사인의 감사보고서 ABC주식회사 주주 및 이사회 귀중 감사의견 "
    '우리는 ABC 주식회사(이하 "회사")의 재무제표를 감사하였습니다. 우리의 의견으로는 '
    "별첨된 회사의 재무제표는 회사의 2025년 12월 31일 현재의 재무상태와 동일로 종료되는 "
    "보고기간의 재무성과 및 현금흐름을 한국채택국제회계기준에 따라, 중요성의 관점에서 "
    "공정하게 표시하고 있습니다. 감사의견근거 우리는 대한민국의 회계감사기준에 따라 감사를 "
    "수행하였습니다. 우리가 입수한 감사증거가 감사의견을 위한 근거로서 충분하고 적합하다고 "
    "우리는 믿습니다. 계속기업 관련 중요한 불확실성 재무제표에 대한 주석 30에 주의를 "
    "기울여야 할 필요가 있습니다. 재무제표에 대한 주석 30은 2025년 12월 31일로 종료되는 "
    "보고기간에 순손실 12,000백만원이 발생하였고, 재무제표일 현재로 기업의 유동부채가 "
    "총자산보다 8,000백만원 만큼 더 많음을 나타내고 있습니다. 주석 30에서 기술된 바와 같이, "
    "이러한 사건이나 상황은 주석 30에서 설명하고 있는 다른 사항과 더불어 계속기업으로서의 "
    "존속능력에 유의적 의문을 제기할 만한 중요한 불확실성이 존재함을 나타냅니다. 우리의 "
    "의견은 이 사항으로부터 영향을 받지 아니합니다. 핵심감사사항 핵심감사사항은 우리의 "
    "전문가적 판단에 따라 당기 재무제표감사에서 가장 유의적인 사항들입니다. 우리는 계속기업 "
    "관련 중요한 불확실성 단락에 기술된 사항에 추가하여, 아래에 기술된 사항을 이 "
    "감사보고서에서 커뮤니케이션할 핵심감사사항으로 결정하였습니다. 매출채권의 회수가능성 "
    "평가 우리는 표본추출을 통해 외부조회를 수행하였습니다. "
    "재무제표에 대한 경영진과 지배기구의 책임 경영진은 재무제표를 작성할 때, 회사의 "
    "계속기업으로서의 존속능력을 평가하고 해당되는 경우, 계속기업 관련 사항을 공시할 책임이 "
    "있습니다."
)

_KICPA_57_WITHIN_BASIS = (
    "독립된 감사인의 감사보고서 ABC주식회사 주주 및 이사회 귀중 의견거절 "
    '우리는 ABC 주식회사(이하 "회사")의 재무제표에 대한 감사계약을 체결하였습니다. 우리는 '
    "별첨된 회사의 재무제표에 대하여 의견을 표명하지 않습니다. 우리는 이 감사보고서의 "
    "의견거절근거 단락에서 기술된 사항의 유의성 때문에 재무제표에 대한 감사의견의 근거를 "
    "제공하는 충분하고 적합한 감사증거를 입수할 수 없었습니다. 의견거절근거 회사의 재무제표는 "
    "회사가 계속기업으로서 존속한다는 가정을 전제로 작성되었으므로, 회사의 자산과 부채가 "
    "정상적인 사업활동과정을 통하여 회수되거나 상환될 수 있다는 가정 하에 회계처리되었습니다. "
    "그러나 재무제표에 대한 주석 3에서 설명하고 있는 바와 같이, 회사는 2025년 12월 31일로 "
    "종료되는 회계연도에 영업손실이 5,000백만원이며 당기순손실이 9,000백만원입니다. 이러한 "
    "상황은 회사의 계속기업으로의 존속능력에 대하여 유의적인 의문을 초래합니다. 회사가 "
    "계속기업으로서 존속할지의 여부는 회사의 향후 자금조달계획의 성패에 따라 좌우되는 중요한 "
    "불확실성을 내포하고 있습니다. 재무제표에 대한 경영진과 지배기구의 책임 경영진은 "
    "재무제표를 작성할 때, 회사의 계속기업으로서의 존속능력을 평가할 책임이 있습니다."
)

_KICPA_1061_PRIOR_AUDITOR = (
    "독립된 감사인의 감사보고서 ABC주식회사 주주 및 이사회 귀중 감사의견 우리의 의견으로는 "
    "별첨된 회사의 재무제표는 회사의 2025년 12월 31일 현재의 재무상태를 한국채택국제회계기준에 "
    "따라, 중요성의 관점에서 공정하게 표시하고 있습니다. 감사의견근거 우리가 입수한 감사증거가 "
    "감사의견을 위한 근거로서 충분하고 적합하다고 우리는 믿습니다. 기타사항 ABC 주식회사의 "
    "2024년 12월 31일로 종료된 보고기간의 재무제표는 타감사인이 감사하였으며, 이 감사인의 "
    "2025년 3월 31일자 감사보고서에는 해당 재무제표에 대하여 계속기업가정 불확실성을 사유로 "
    "의견거절이 표명되었습니다. 재무제표에 대한 경영진과 지배기구의 책임 경영진은 재무제표를 "
    "작성할 때, 회사의 계속기업으로서의 존속능력을 평가할 책임이 있습니다."
)

_TEMPLATE_META = {"rcept_no": "R_KICPA", "report_name": "감사보고서 (2025.12)"}


class KicpaTemplateGoingConcernPhase1(unittest.TestCase):
    """표준 문안(작성사례 5.1·5.7·10.6.1)에 대한 위치·시점 판정.

    실측 픽스처가 「이 회사가 실제로 이랬다」를 고정한다면, 이 합성 문서들은
    「업계 표준 문안이 이렇게 생겼다」를 고정한다. 두 축이 어긋나면 규칙이
    특정 공시에 과적합된 것이다."""

    def test_51_separate_section_wins_over_kam_cross_reference(self) -> None:
        r = parse_audit_text(_KICPA_51_SEPARATE_SECTION, _TEMPLATE_META)

        self.assertEqual(r.gc_material_uncertainty, "disclosed")
        self.assertEqual(r.gc_location, "separate_section")
        self.assertIn("주석 30", r.going_concern_snippet)
        self.assertNotIn("핵심감사사항으로 결정하였습니다", r.going_concern_snippet)

    def test_51_cross_reference_alone_is_not_a_disclosure(self) -> None:
        """상호참조 문장만 남기면(구역 본문을 지우면) 「없음」이어야 한다."""

        kam_preamble_only = (
            "독립된 감사인의 감사보고서 감사의견 우리의 의견으로는 별첨된 회사의 재무제표는 "
            "중요성의 관점에서 공정하게 표시하고 있습니다. 핵심감사사항 우리는 계속기업 관련 "
            "중요한 불확실성 단락에 기술된 사항에 추가하여, 아래에 기술된 사항을 이 "
            "감사보고서에서 커뮤니케이션할 핵심감사사항으로 결정하였습니다."
        )

        r = parse_audit_text(kam_preamble_only, _TEMPLATE_META)

        self.assertEqual(r.gc_material_uncertainty, "not_disclosed")
        self.assertFalse(r.going_concern)

    def test_57_disclaimer_basis_paragraph_is_within_basis(self) -> None:
        r = parse_audit_text(_KICPA_57_WITHIN_BASIS, _TEMPLATE_META)

        self.assertEqual(r.gc_material_uncertainty, "disclosed")
        self.assertEqual(r.gc_location, "within_basis_for_opinion")
        self.assertEqual(r.audit_opinion, "의견거절")

    def test_1061_prior_auditor_sentence_is_not_a_current_disclosure(self) -> None:
        r = parse_audit_text(_KICPA_1061_PRIOR_AUDITOR, _TEMPLATE_META)

        self.assertEqual(r.gc_material_uncertainty, "not_disclosed")
        self.assertFalse(r.going_concern)
        self.assertEqual(r.other_matter_status, "present")
        self.assertEqual(r.emphasis_status, "absent")

    def test_responsibility_boilerplate_alone_is_not_a_disclosure(self) -> None:
        """가정법 보일러플레이트(「초래할 수 있는」)는 트리거가 아니다."""

        boilerplate = (
            "독립된 감사인의 감사보고서 감사의견 우리의 의견으로는 별첨된 회사의 재무제표는 "
            "중요성의 관점에서 공정하게 표시하고 있습니다. 재무제표감사에 대한 감사인의 책임 "
            "경영진이 사용한 회계의 계속기업전제의 적절성과, 입수한 감사증거를 근거로 "
            "계속기업으로서의 존속능력에 대하여 유의적 의문을 초래할 수 있는 사건이나 상황과 "
            "관련된 중요한 불확실성이 존재하는지 여부에 대하여 결론을 내립니다."
        )

        r = parse_audit_text(boilerplate, _TEMPLATE_META)

        self.assertEqual(r.gc_material_uncertainty, "not_disclosed")
        self.assertFalse(r.going_concern)


class GoingConcernFilterEdgeCasesPhase1(unittest.TestCase):
    """오탐/누락의 경계 - 실측 분포에서 뽑은 두 경계선.

    ① 「의문을 초래」는 뒤에 「할」이 오면 감사인의 책임 보일러플레이트,
       그렇지 않으면(「초래합니다」·「초래하게 되었으며」) 실제 기재다
       (실측 1,200건 표본: 보일러플레이트 1,141회 대 실제 기재 14회).
    ② 계속기업 문단은 비교표시 때문에 「전기」를 자연스럽게 품는다
       (「전기말 결손금」). 낱말만 보고 전기 기재로 걸러 내면 안 된다 -
       **다른 감사보고서의 의견**을 옮겨 적은 문장일 때만 시점 필터를 건다."""

    def _wrap(self, gc_sentence: str) -> str:
        return (
            "독립된 감사인의 감사보고서 감사의견 우리의 의견으로는 별첨된 회사의 재무제표는 "
            "중요성의 관점에서 공정하게 표시하고 있습니다. 감사의견근거 우리가 입수한 감사증거가 "
            "감사의견을 위한 근거로서 충분하고 적합하다고 우리는 믿습니다. "
            "계속기업 관련 중요한 불확실성 " + gc_sentence
        )

    def test_declarative_initiation_wordings_are_disclosures(self) -> None:
        for tail_wording in ("초래합니다.", "초래하게 되었으며, 이를 타개하기 위하여 노력하고 있습니다.", "초래하며, 회사는 자구계획을 이행하고 있습니다."):
            with self.subTest(tail=tail_wording):
                text = self._wrap(
                    "이러한 상황은 회사의 계속기업으로서의 존속능력에 유의적 의문을 " + tail_wording
                )
                r = parse_audit_text(text, _TEMPLATE_META)
                self.assertEqual(r.gc_material_uncertainty, "disclosed")

    def test_comparative_prior_year_figures_do_not_hide_current_disclosure(self) -> None:
        text = self._wrap(
            "회사는 당기말 결손금 56,771백만원(전기말 49,230백만원)을 기록하였으며, 이러한 상황은 "
            "회사의 계속기업으로서의 존속능력에 유의적 의문을 제기할 만한 중요한 불확실성이 "
            "존재함을 나타냅니다."
        )

        r = parse_audit_text(text, _TEMPLATE_META)

        self.assertEqual(r.gc_material_uncertainty, "disclosed")
        self.assertEqual(r.gc_location, "separate_section")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
