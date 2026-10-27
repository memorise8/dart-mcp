"""정정 계보(supersede.py) 테스트 — K2-1."""
import unittest

from dart_search_mcp.ledger.supersede import base_report_name, supersede


def row(rc, scope="separate", topic="LEASE_EXEMPTION", value="BOTH"):
    return {"rcept_no": rc, "fs_scope": scope, "topic": topic, "value": value, "corp_code": None}


class SupersedeTest(unittest.TestCase):
    AUDIT = {"20260320001267": {"corp_code": "00438036", "report_name": "사업보고서 (2025.12)"},
             "20260323001094": {"corp_code": "00438036", "report_name": "[첨부정정]사업보고서 (2025.12)"},
             "20250314000001": {"corp_code": "00438036", "report_name": "사업보고서 (2024.12)"}}

    def test_base_report_name(self):
        self.assertEqual(base_report_name("[첨부정정]사업보고서 (2025.12)"), "사업보고서 (2025.12)")
        self.assertEqual(base_report_name("[기재정정] [첨부추가]감사보고서 (2025.03)"), "감사보고서 (2025.03)")
        self.assertEqual(base_report_name("사업보고서 (2025.12)"), "사업보고서 (2025.12)")

    def test_older_filing_row_dropped_when_latest_has_same_scope_topic(self):
        # 팅크웨어: 원본(2024년 첨부)과 [첨부정정](2025년) — 원본 행을 뺀다
        rows = [row("20260320001267", value="LOW_VALUE"), row("20260323001094")]
        kept, dropped = supersede(rows, self.AUDIT)
        self.assertEqual([r["rcept_no"] for r in kept], ["20260323001094"])
        self.assertEqual((dropped[0]["rcept_no"], dropped[0]["superseded_by"]), ("20260320001267", "20260323001094"))

    def test_older_row_kept_when_latest_lacks_scope_or_topic(self):
        rows = [row("20260320001267", scope="consolidated"), row("20260320001267", topic="IP_FAIR_VALUE"),
                row("20260323001094")]
        kept, dropped = supersede(rows, self.AUDIT)
        self.assertEqual(len(kept), 3)
        self.assertEqual(dropped, [])

    def test_different_report_period_or_unknown_untouched(self):
        rows = [row("20250314000001"), row("20260323001094"), row("29990101000001")]
        kept, dropped = supersede(rows, self.AUDIT)
        self.assertEqual((len(kept), len(dropped)), (3, 0))


if __name__ == "__main__":
    unittest.main()
