"""
`dart_search_mcp.redact`에 대한 단위 테스트.

이 모듈은 OpenDART API 키(`crtfc_key`)가 URL, 쿼리 파라미터, 예외 메시지 등
어디에도 평문으로 노출되지 않도록 하는 단일 리댁션 헬퍼를 검증한다.
"""

import importlib
import logging
import unittest


class RedactUrlTests(unittest.TestCase):
    def test_redacts_crtfc_key_in_middle_of_query_string(self) -> None:
        from dart_search_mcp.redact import REDACTED, redact

        url = "https://opendart.fss.or.kr/api/list.json?crtfc_key=abcdef123456&corp_code=00126380"

        result = redact(url)

        self.assertNotIn("abcdef123456", result)
        self.assertIn(f"crtfc_key={REDACTED}", result)
        self.assertIn("corp_code=00126380", result)

    def test_redacts_crtfc_key_as_last_query_param(self) -> None:
        from dart_search_mcp.redact import redact

        url = "https://opendart.fss.or.kr/api/list.json?corp_code=00126380&crtfc_key=abcdef123456"

        result = redact(url)

        self.assertNotIn("abcdef123456", result)

    def test_redacts_configured_api_key_value_wherever_it_appears(self) -> None:
        import dart_search_mcp.redact as redact_module

        original = redact_module.API_KEY
        redact_module.API_KEY = "super-secret-value"
        try:
            message = "오류: 네트워크 오류가 발생했습니다. connect to host with super-secret-value failed"

            result = redact_module.redact(message)

            self.assertNotIn("super-secret-value", result)
        finally:
            redact_module.API_KEY = original

    def test_plain_text_without_key_is_unaffected(self) -> None:
        from dart_search_mcp.redact import redact

        message = "오류: 요청 시간이 초과되었습니다. 잠시 후 다시 시도해주세요."

        self.assertEqual(redact(message), message)


class RedactParamsTests(unittest.TestCase):
    def test_redacts_crtfc_key_param_value(self) -> None:
        from dart_search_mcp.redact import REDACTED, redact

        params = {"crtfc_key": "abcdef123456", "corp_code": "00126380"}

        result = redact(params)

        self.assertEqual(result["crtfc_key"], REDACTED)
        self.assertEqual(result["corp_code"], "00126380")

    def test_does_not_mutate_original_params(self) -> None:
        from dart_search_mcp.redact import redact

        params = {"crtfc_key": "abcdef123456"}

        redact(params)

        self.assertEqual(params["crtfc_key"], "abcdef123456")


class HttpxLoggingSuppressionTests(unittest.TestCase):
    def test_importing_redact_suppresses_httpx_info_logging(self) -> None:
        import dart_search_mcp.redact as redact_module

        importlib.reload(redact_module)

        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
