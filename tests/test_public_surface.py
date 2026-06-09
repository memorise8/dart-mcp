import asyncio
import unittest

from click.testing import CliRunner

import cli
import server


EXPECTED_TOOL_NAMES = [
    "search_disclosures",
    "get_company_info",
    "search_corp_code",
    "get_financial_statements",
    "get_financial_statements_full",
    "get_multi_company_financials",
    "get_financial_indicators",
    "get_multi_company_indicators",
    "get_major_shareholders_report",
    "get_executive_stock_report",
    "get_periodic_report",
    "get_major_event_report",
    "download_document",
    "download_xbrl",
    "get_xbrl_taxonomy",
    "get_securities_report",
]

EXPECTED_COMMAND_NAMES = [
    "company",
    "disclosures",
    "download",
    "download-xbrl",
    "event",
    "executive-stock",
    "financial",
    "financial-full",
    "indicators",
    "multi-financial",
    "multi-indicators",
    "periodic",
    "search",
    "securities",
    "serve",
    "shareholders",
    "taxonomy",
]


class PublicSurfaceTests(unittest.TestCase):
    def test_mcp_tool_names_when_listed(self) -> None:
        async def list_tool_names() -> list[str]:
            tools = await server.mcp.list_tools()
            return [tool.name for tool in tools]

        self.assertEqual(asyncio.run(list_tool_names()), EXPECTED_TOOL_NAMES)

    def test_cli_help_lists_existing_commands_when_invoked(self) -> None:
        runner = CliRunner()
        result = runner.invoke(cli.cli, ["--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        for command_name in EXPECTED_COMMAND_NAMES:
            self.assertIn(command_name, result.output)

    def test_format_date_when_yyyymmdd(self) -> None:
        self.assertEqual(server._format_date("20240131"), "2024-01-31")
        self.assertEqual(server._format_date("2024-01-31"), "2024-01-31")

    def test_format_amount_when_numeric_or_empty(self) -> None:
        self.assertEqual(server._format_amount("1234567"), "1,234,567")
        self.assertEqual(server._format_amount("1234.5"), "1,234.50")
        self.assertEqual(server._format_amount("-"), "-")
        self.assertEqual(server._format_amount("not-a-number"), "not-a-number")

    def test_generic_response_when_dict_contains_metadata(self) -> None:
        result = server._format_generic_response(
            "제목",
            {"status": "000", "message": "ok", "rcept_dt": "20240131", "amount": "1000"},
        )

        self.assertIn("제목", result)
        self.assertIn("rcept_dt: 2024-01-31", result)
        self.assertIn("amount: 1,000", result)
        self.assertNotIn("status", result)
        self.assertNotIn("message", result)

    def test_required_argument_errors_do_not_call_network(self) -> None:
        financial = asyncio.run(server.get_financial_statements("", "2024"))
        periodic = asyncio.run(server.get_periodic_report("00126380", "2024", report_type=""))
        xbrl = asyncio.run(server.download_xbrl())

        self.assertIn("고유번호", financial)
        self.assertIn("report_type", periodic)
        self.assertIn("rcept_no", xbrl)


if __name__ == "__main__":
    unittest.main()
