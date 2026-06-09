import unittest

from click.testing import CliRunner

import cli


class DiagnosticsCommandTests(unittest.TestCase):
    def test_diagnostics_reports_missing_key_without_network(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli.cli, ["diagnostics"], env={"DART_API_KEY": "", "dart_api": ""})

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("DART API key", result.output)
        self.assertIn("missing", result.output)

    def test_diagnostics_redacts_present_key(self) -> None:
        runner = CliRunner()

        result = runner.invoke(cli.cli, ["diagnostics"], env={"DART_API_KEY": "secret-test-key"})

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("DART API key", result.output)
        self.assertIn("configured", result.output)
        self.assertNotIn("secret-test-key", result.output)


if __name__ == "__main__":
    unittest.main()
