"""`dart_search_mcp.audit_xml_parser`에 대한 테스트.

픽스처(`tests/fixtures/audit_xml/*.xml`)는 실제 DART 감사보고서 XML에서 발췌한
조각이다(파일 상단 주석에 출처 rcept_no 명시). 각 픽스처의 meta 딕�셔너리는
`dart_collected/manifest.json`의 해당 rcept_no 레코드를 그대로 옮긴 것이다
(수집 스크립트가 만든 실제 값 - 조작하지 않았다).

이 파서는 순수·결정론 함수이므로 네트워크/파일시스템 접근을 전혀 모킹할 필요가
없다 - 픽스처 파일을 직접 읽어 바이트로 넘긴다."""

from __future__ import annotations

import os
import unittest

from dart_search_mcp.audit_xml_parser import (
    ParsedAuditReport,
    classify_opinion,
    derive_fiscal_year,
    detect_going_concern,
    extract_emphasis,
    extract_kam,
    parse_audit_xml,
)

_FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "audit_xml")


def _load(name: str) -> bytes:
    with open(os.path.join(_FIXTURES_DIR, name), "rb") as f:
        return f.read()


# manifest.json의 records[] 항목을 그대로 옮긴 실제 meta 값들.
_META_01_ITOMATO = {
    "category": "감사보고서",
    "report_name": "감사보고서 (2025.03)",
    "rcept_no": "20250701000004",
    "rcept_dt": "20250701",
    "corp_code": "00363255",
    "corp_name": "이토마토",
    "stock_code": "",
    "corp_cls": "E",
    "flr_nm": "대주회계법인",
    "remark": "",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250701000004",
}

_META_02_SAMSUNG_FN = {
    "category": "사업보고서",
    "report_name": "사업보고서 (2025.04)",
    "rcept_no": "20250710000299",
    "rcept_dt": "20250710",
    "corp_code": "01688896",
    "corp_name": "삼성FN리츠",
    "stock_code": "448730",
    "corp_cls": "Y",
    "flr_nm": "삼성FN리츠",
    "remark": "",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250710000299",
}

_META_03_SINWOO = {
    "category": "감사보고서",
    "report_name": "감사보고서 (2025.12)",
    "rcept_no": "20260311003282",
    "rcept_dt": "20260311",
    "corp_code": "00368056",
    "corp_name": "신우개발",
    "stock_code": "",
    "corp_cls": "E",
    "flr_nm": "신한회계법인",
    "remark": "",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260311003282",
}

_META_04_BLPHARMTECH = {
    "category": "사업보고서",
    "report_name": "사업보고서 (2025.12)",
    "rcept_no": "20260324000004",
    "rcept_dt": "20260324",
    "corp_code": "00384717",
    "corp_name": "비엘팜텍",
    "stock_code": "065170",
    "corp_cls": "K",
    "flr_nm": "비엘팜텍",
    "remark": "연",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260324000004",
}

_META_05_BACARDI = {
    "category": "감사보고서",
    "report_name": "감사보고서 (2025.03)",
    "rcept_no": "20250701000289",
    "rcept_dt": "20250701",
    "corp_code": "00983697",
    "corp_name": "바카디코리아",
    "stock_code": "",
    "corp_cls": "E",
    "flr_nm": "대주회계법인",
    "remark": "",
    "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250701000289",
}


# ---------------------------------------------------------------------------
# classify_opinion - 4개 의견 유형 (실측 문장 + 실측 부적정/의견거절 문장을
# 리터럴로 인용; 별도 픽스처 파일 없이도 실제 관찰에 기반한다)
# ---------------------------------------------------------------------------


class ClassifyOpinionTests(unittest.TestCase):
    def test_unqualified_gongjeonghage(self) -> None:
        # rcept_no=20250701000004 (이토마토) 실측 문장.
        para = (
            "감사의견 우리의 의견으로는 별첨된 회사의 재무제표는 회사의 2025년 3월 31일과 "
            "2024년 3월 31일 현재의 재무상태와 동일로 종료되는 양 보고기간의 재무성과 및 "
            "현금흐름을일반기업회계기준에 따라, 중요성의 관점에서 공정하게 표시하고 있습니다."
        )
        self.assertEqual(classify_opinion(para), "적정")

    def test_unqualified_jeongjeonghage(self) -> None:
        # rcept_no=20260311003282 (신우개발, 부속명세서) 실측 문장.
        para = (
            "본 감사인의 의견으로는 별첨 부속명세서는 위에서 언급한 재무제표와 관련하여 동 "
            "부속명세서에 포함되어야 할 재무정보를 중요성의 관점에서 일반기업회계기준에 따라 "
            "적정하게 표시하고 있습니다."
        )
        self.assertEqual(classify_opinion(para), "적정")

    def test_qualified_je_oe_hago_neun(self) -> None:
        # rcept_no=20250701000289 (바카디코리아) 실측 문장.
        para = (
            "한정의견 우리의 의견으로는 별첨된 회사의 재무제표는 이 감사보고서의 한정의견근거 "
            "단락에기술된 사항이 미치는 영향을 제외하고는, 회사의 2025년 3월 31일 현재의 "
            "재무상태와 동일로 종료되는 보고기간의 재무성과 및 현금흐름을 일반기업회계기준에 "
            "따라, 중요성의 관점에서 공정하게 표시하고 있습니다."
        )
        self.assertEqual(classify_opinion(para), "한정")

    def test_adverse_pyosihago_itji_anhseumnida(self) -> None:
        # rcept_no=20250918000376 (엘라포니시) 실측 문장 - "표시하고 있지 않습니다".
        para = (
            "부적정의견 우리는 주식회사 엘라포니시(이하 \"회사\")의 재무제표를 감사하였습니다. "
            "우리의 의견으로는 별첨된 회사의 재무제표는 이 감사보고서의 부적정의견근거 단락에 "
            "기술된 사항의 유의성 때문에 회사의 2024년 12월 31일 현재의 재무상태와 동일로 "
            "종료되는 보고기간의 재무성과 및 현금흐름을대한민국의 일반기업회계기준에 따라, "
            "중요성의 관점에서 공정하게 표시하고 있지 않습니다."
        )
        self.assertEqual(classify_opinion(para), "부적정")

    def test_adverse_pyosihago_itji_anihamnida_variant(self) -> None:
        # rcept_no=20251030000341 (제이에프엠테크) 실측 문장 - "표시하고 있지 아니합니다"
        # 변형(브리프 규칙의 확장, report 참고). 이 변형을 별도로 처리하지 않으면
        # 규칙 4(적정)에도 걸리지 않아 unknown으로 새기 때문에 실측으로 발견 즉시
        # 규칙 1에 편입했다.
        para = (
            "부적정의견 우리의 의견으로는 별첨된 회사의 재무제표는 이 감사보고서의 부적정의견근거 "
            "단락에 기술된 사항의 유의성으로 때문에 회사의 2024년 12월 31일 현재의 재무상태와 "
            "동일로 종료되는 보고기간의 재무성과 및 현금흐름을 일반기업회계기준에 따라 중요성의 "
            "관점에서 공정하게 표시하고 있지 아니합니다."
        )
        self.assertEqual(classify_opinion(para), "부적정")

    def test_disclaimer_uigyeon_pyomyeong_haji_anhseumnida(self) -> None:
        # rcept_no=20260324000003 (비에프랩스) 실측 문장.
        para = (
            "의견거절 우리는 주식회사 비에프랩스(이하 \"회사\")의 재무제표에 대한 감사계약을 "
            "체결하였습니다. 우리는 별첨된 회사의 재무제표에 대하여 의견을 표명하지 않습니다."
        )
        self.assertEqual(classify_opinion(para), "의견거절")

    def test_empty_paragraph_is_unknown(self) -> None:
        self.assertEqual(classify_opinion(""), "unknown")

    def test_no_recognized_phrase_is_unknown(self) -> None:
        self.assertEqual(classify_opinion("전혀 관계없는 임의의 텍스트입니다."), "unknown")


# ---------------------------------------------------------------------------
# detect_going_concern - 오탐 방지가 핵심
# ---------------------------------------------------------------------------


class DetectGoingConcernTests(unittest.TestCase):
    def test_boilerplate_only_is_false(self) -> None:
        # 경영진/감사인의 책임 단락 표준 보일러플레이트 - 실제로 계속기업
        # 불확실성이 없는 절대다수 보고서에 등장하는 문구다. 이것만으로
        # True를 내면 오탐이다.
        text = (
            "경영진은 재무제표를 작성할 때, 회사의 계속기업으로서의 존속능력을 평가하고 "
            "해당되는 경우, 계속기업 관련 사항을 공시할 책임이 있습니다. 그리고 경영진이 "
            "기업을 청산하거나 영업을 중단할 의도가 없는 한, 회계의 계속기업전제의 사용에 "
            "대해서도 책임이 있습니다. 경영진이 사용한 회계의 계속기업전제의 적절성과, "
            "입수한 감사증거를 근거로 계속기업으로서의 존속능력에 대하여 유의적 의문을 "
            "초래할 수 있는 사건이나, 상황과 관련된 중요한 불확실성이 존재하는지 여부에 "
            "대하여 결론을 내립니다."
        )
        going_concern, snippet = detect_going_concern(text)
        self.assertFalse(going_concern)
        self.assertEqual(snippet, "")

    def test_material_uncertainty_header_is_true(self) -> None:
        text = (
            "우리가 입수한 감사증거가 감사의견을 위한 근거로서 충분하고 적합하다고 우리는 "
            "믿습니다. 계속기업 관련 중요한 불확실성 재무제표에 대한 주석 35에 주의를 "
            "기울여야 할 필요가 있습니다. 이러한 상황은 계속기업으로서의 존속능력에 유의적 "
            "의문을 제기할 만한 중요한 불확실성이 존재함을 나타냅니다."
        )
        going_concern, snippet = detect_going_concern(text)
        self.assertTrue(going_concern)
        self.assertIn("주석 35", snippet)

    def test_significant_uncertainty_header_variant_is_true(self) -> None:
        text = "감사의견근거 계속기업 관련 중대한 불확실성 재무제표에 대한 주석 20에 주의를 기울여야 합니다."
        going_concern, _snippet = detect_going_concern(text)
        self.assertTrue(going_concern)

    def test_empty_text_is_false(self) -> None:
        going_concern, snippet = detect_going_concern("")
        self.assertFalse(going_concern)
        self.assertEqual(snippet, "")


# ---------------------------------------------------------------------------
# extract_kam / extract_emphasis
# ---------------------------------------------------------------------------


class ExtractKamEmphasisTests(unittest.TestCase):
    def test_extract_kam_no_header_is_empty(self) -> None:
        self.assertEqual(extract_kam("감사의견 우리의 의견으로는 ... 표시하고 있습니다."), "")

    def test_extract_kam_stops_at_next_landmark(self) -> None:
        text = (
            "핵심감사사항 임대료수익의 발생사실에 대한 설명입니다. "
            "재무제표에 대한 경영진과 지배기구의 책임 경영진은 ..."
        )
        kam = extract_kam(text)
        self.assertIn("임대료수익", kam)
        self.assertNotIn("경영진과 지배기구", kam)

    def test_extract_emphasis_stops_at_next_landmark(self) -> None:
        text = "강조사항 주석 23의 재작성 관련 설명입니다. 핵심감사사항 임대료수익 관련 사항."
        emphasis = extract_emphasis(text)
        self.assertIn("주석 23", emphasis)
        self.assertNotIn("임대료수익", emphasis)

    def test_extract_emphasis_no_header_is_empty(self) -> None:
        self.assertEqual(extract_emphasis("감사의견 우리의 의견으로는 ..."), "")


# ---------------------------------------------------------------------------
# derive_fiscal_year
# ---------------------------------------------------------------------------


class DeriveFiscalYearTests(unittest.TestCase):
    def test_report_name_month_03(self) -> None:
        meta = {"report_name": "감사보고서 (2025.03)", "rcept_dt": "20250701"}
        self.assertEqual(derive_fiscal_year(meta, ""), (2025, 3))

    def test_report_name_month_12(self) -> None:
        meta = {"report_name": "사업보고서 (2025.12)", "rcept_dt": "20260324"}
        self.assertEqual(derive_fiscal_year(meta, ""), (2025, 12))

    def test_bracket_prefix_report_name(self) -> None:
        meta = {"report_name": "[기재정정]감사보고서 (2020.12)", "rcept_dt": "20250718"}
        self.assertEqual(derive_fiscal_year(meta, ""), (2020, 12))

    def test_fallback_to_xml_period_when_report_name_missing(self) -> None:
        meta = {"report_name": "", "rcept_dt": "20250701"}
        text = "제 32(당) 기 2025년 3월 31일 현재 재무상태표"
        self.assertEqual(derive_fiscal_year(meta, text), (2025, 3))

    def test_fallback_to_rcept_dt_when_all_else_missing(self) -> None:
        meta = {"report_name": "", "rcept_dt": "20250815"}
        self.assertEqual(derive_fiscal_year(meta, "관련 없는 텍스트"), (2025, 8))

    def test_all_fail_is_none_none(self) -> None:
        meta = {"report_name": "", "rcept_dt": ""}
        self.assertEqual(derive_fiscal_year(meta, "관련 없는 텍스트"), (None, None))


# ---------------------------------------------------------------------------
# parse_audit_xml - 통합 테스트(실제 픽스처 5종)
# ---------------------------------------------------------------------------


class ParseFixture01UnlistedNoKamTests(unittest.TestCase):
    """비상장(E), 적정, "공정하게", KAM 없음, 감사인 서명 = meta flr_nm 일치."""

    def setUp(self) -> None:
        xml_bytes = _load("01_unlisted_unqualified_no_kam.xml")
        self.result = parse_audit_xml(xml_bytes, _META_01_ITOMATO, doc_path="fixtures/01.xml")

    def test_is_parsed_audit_report(self) -> None:
        self.assertIsInstance(self.result, ParsedAuditReport)

    def test_opinion_is_unqualified(self) -> None:
        self.assertEqual(self.result.audit_opinion, "적정")
        self.assertIn("opinion", self.result.parse_flags)

    def test_going_concern_is_false_no_false_positive(self) -> None:
        self.assertFalse(self.result.going_concern)
        self.assertEqual(self.result.going_concern_snippet, "")
        self.assertNotIn("going_concern", self.result.parse_flags)

    def test_kam_absent(self) -> None:
        self.assertFalse(self.result.kam_present)
        self.assertEqual(self.result.kam_raw, "")
        self.assertNotIn("kam", self.result.parse_flags)

    def test_kam_tags_always_empty_tuple(self) -> None:
        self.assertEqual(self.result.kam_tags, ())

    def test_fiscal_year_from_report_name(self) -> None:
        self.assertEqual(self.result.fiscal_year, 2025)
        self.assertEqual(self.result.settlement_month, 3)
        self.assertIn("fiscal_year", self.result.parse_flags)

    def test_auditor_matches_signature_no_mismatch_flag(self) -> None:
        self.assertEqual(self.result.auditor, "대주회계법인")
        self.assertIn("auditor", self.result.parse_flags)
        self.assertNotIn("auditor_mismatch", self.result.parse_flags)

    def test_meta_passthrough_fields(self) -> None:
        self.assertEqual(self.result.rcept_no, "20250701000004")
        self.assertEqual(self.result.corp_name, "이토마토")
        self.assertEqual(self.result.corp_cls, "E")
        self.assertEqual(self.result.category, "감사보고서")
        self.assertEqual(self.result.doc_path, "fixtures/01.xml")


class ParseFixture02ListedWithKamMismatchTests(unittest.TestCase):
    """상장(Y), 적정, K-IFRS, "공정하게", KAM 있음, meta flr_nm != 실제 서명."""

    def setUp(self) -> None:
        xml_bytes = _load("02_listed_unqualified_with_kam.xml")
        self.result = parse_audit_xml(xml_bytes, _META_02_SAMSUNG_FN, doc_path="fixtures/02.xml")

    def test_opinion_is_unqualified(self) -> None:
        self.assertEqual(self.result.audit_opinion, "적정")

    def test_kam_present_and_non_empty(self) -> None:
        self.assertTrue(self.result.kam_present)
        self.assertNotEqual(self.result.kam_raw, "")
        self.assertIn("임대료수익", self.result.kam_raw)
        self.assertIn("kam", self.result.parse_flags)

    def test_going_concern_false(self) -> None:
        self.assertFalse(self.result.going_concern)

    def test_auditor_mismatch_flagged_but_value_stays_flr_nm(self) -> None:
        # meta flr_nm은 회사명("삼성FN리츠") 자체 - 실제 서명("이촌회계법인")과
        # 다르다. 브리프 규칙: auditor 값은 flr_nm을 유지하고 플래그만 추가한다.
        self.assertEqual(self.result.auditor, "삼성FN리츠")
        self.assertIn("auditor_mismatch", self.result.parse_flags)


class ParseFixture03JeongjeonghagePhraseTests(unittest.TestCase):
    """"적정하게" 문구 - 감사의견 표준 헤더 없이 "본 감사인의 의견으로는" 문장만
    존재하는 구형식(부속명세서) 실측 사례. 헤더 기반 추출이 실패하고 폴백
    문장 정규식으로만 잡혀야 한다."""

    def setUp(self) -> None:
        xml_bytes = _load("03_jeongjeonghage_phrase.xml")
        self.result = parse_audit_xml(xml_bytes, _META_03_SINWOO, doc_path="fixtures/03.xml")

    def test_opinion_is_unqualified_via_jeongjeonghage(self) -> None:
        self.assertEqual(self.result.audit_opinion, "적정")
        self.assertIn("적정하게", self.result.opinion_snippet)

    def test_fiscal_year_from_report_name(self) -> None:
        self.assertEqual(self.result.fiscal_year, 2025)
        self.assertEqual(self.result.settlement_month, 12)


class ParseFixture04GoingConcernTests(unittest.TestCase):
    """상장(K), 적정 + 계속기업 관련 중요한 불확실성(True) + KAM 있음."""

    def setUp(self) -> None:
        xml_bytes = _load("04_going_concern_material_uncertainty.xml")
        self.result = parse_audit_xml(xml_bytes, _META_04_BLPHARMTECH, doc_path="fixtures/04.xml")

    def test_opinion_is_unqualified(self) -> None:
        self.assertEqual(self.result.audit_opinion, "적정")

    def test_going_concern_true_with_snippet(self) -> None:
        self.assertTrue(self.result.going_concern)
        self.assertNotEqual(self.result.going_concern_snippet, "")
        self.assertIn("going_concern", self.result.parse_flags)

    def test_kam_present(self) -> None:
        self.assertTrue(self.result.kam_present)
        self.assertIn("비엘멜라니스", self.result.kam_raw)

    def test_fiscal_year_month_12(self) -> None:
        self.assertEqual(self.result.fiscal_year, 2025)
        self.assertEqual(self.result.settlement_month, 12)


class ParseFixture05QualifiedOpinionTests(unittest.TestCase):
    """한정의견 실측 사례 - "...제외하고는, ... 표시하고 있습니다."."""

    def setUp(self) -> None:
        xml_bytes = _load("05_qualified_opinion.xml")
        self.result = parse_audit_xml(xml_bytes, _META_05_BACARDI, doc_path="fixtures/05.xml")

    def test_opinion_is_qualified(self) -> None:
        self.assertEqual(self.result.audit_opinion, "한정")
        self.assertIn("opinion", self.result.parse_flags)

    def test_going_concern_false_despite_responsibility_boilerplate(self) -> None:
        # 이 픽스처는 "재무제표에 대한 경영진의 책임과 지배기구의 책임" +
        # "재무제표감사에 대한 감사인의 책임" 보일러플레이트를 포함하지만
        # 계속기업 관련 불확실성 섹션 자체가 없다 - False여야 오탐 방지 검증.
        self.assertFalse(self.result.going_concern)

    def test_auditor_matches_no_mismatch(self) -> None:
        self.assertEqual(self.result.auditor, "대주회계법인")
        self.assertNotIn("auditor_mismatch", self.result.parse_flags)


# ---------------------------------------------------------------------------
# 순수성/견고성 - 예외를 던지지 않아야 한다
# ---------------------------------------------------------------------------


class RobustnessTests(unittest.TestCase):
    def test_empty_bytes_does_not_raise(self) -> None:
        result = parse_audit_xml(b"", {})
        self.assertEqual(result.audit_opinion, "unknown")
        self.assertFalse(result.going_concern)
        self.assertFalse(result.kam_present)
        self.assertEqual(result.kam_tags, ())
        self.assertEqual(result.fiscal_year, None)

    def test_garbage_bytes_does_not_raise(self) -> None:
        result = parse_audit_xml(b"\xff\xfe\x00not xml at all<<<>>>", {"rcept_no": "x"})
        self.assertEqual(result.rcept_no, "x")

    def test_missing_meta_keys_default_to_empty(self) -> None:
        result = parse_audit_xml(b"<P>hello</P>", {})
        self.assertEqual(result.corp_name, "")
        self.assertEqual(result.auditor, "")
        self.assertNotIn("auditor", result.parse_flags)


if __name__ == "__main__":
    unittest.main()
