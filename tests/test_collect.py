"""`dart_search_mcp.collect`(재사용 가능한 대량 공시 수집기)에 대한 테스트.

Step 1: OpenDART `list.json`을 창 분할 + 완전 페이지네이션으로 순회하며
대상 공시유형/이름 키워드로 필터링하고, `rcept_no`로 중복 제거한 뒤
체크포인트 가능한 매니페스트를 만든다. ZIP/XML 추출(Step 2)은 이 작업의
범위 밖이다.

`search_disclosures_structured`(HTTP 경계 바로 위의 구조화 계층)를
`unittest.mock.patch`로 직접 대체해, 수집기 자체의 창 분할/페이지네이션/
재시도/필터/중복제거/체크포인트 로직만 검증한다(HTTP/OpenDART 관련 사항은
`tests/test_disclosure_search_structured.py`가 이미 다룬다). 실제
OpenDART로는 어떤 요청도 나가지 않는다.
"""

from __future__ import annotations

import calendar
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from dart_search_mcp.collect import (
    CollectionManifest,
    DateWindow,
    classify_category,
    collect_disclosures,
    split_windows,
)
from dart_search_mcp.tools.disclosures import DisclosureRecord, DisclosureSearchError, DisclosureSearchResult


def _record(
    rcept_no: str,
    report_name: str = "감사보고서",
    rcept_dt: str = "20260101",
    corp_code: str = "00126380",
    corp_name: str = "삼성전자",
) -> DisclosureRecord:
    return DisclosureRecord(
        report_name=report_name,
        rcept_no=rcept_no,
        rcept_dt=rcept_dt,
        corp_code=corp_code,
        corp_name=corp_name,
        stock_code="005930",
        corp_cls="Y",
        source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        flr_nm="삼정회계법인",
        remark="",
    )


def _calendar_month_oracle_window_end(bgn_de: str) -> str:
    """운영 코드(`collect._add_months`)와 독립적으로 구현한 오라클.

    "시작일로부터 3 달력월 후 -1일"을 모듈로 트릭이 아니라 단순 반복으로
    계산해, 운영 코드의 버그를 우연히 상쇄하지 않도록 한다."""
    start = datetime.strptime(bgn_de, "%Y%m%d").date()
    year = start.year
    month = start.month + 3
    while month > 12:
        month -= 12
        year += 1
    last_day_of_target_month = calendar.monthrange(year, month)[1]
    day = min(start.day, last_day_of_target_month)
    limit = date(year, month, day) - timedelta(days=1)
    return limit.strftime("%Y%m%d")


class SplitWindowsTests(unittest.TestCase):
    def test_range_within_one_calendar_quarter_stays_one_window(self) -> None:
        windows = split_windows("20260101", "20260201")
        self.assertEqual(windows, [DateWindow(bgn_de="20260101", end_de="20260201")])

    def test_single_day_range_is_deterministic(self) -> None:
        self.assertEqual(
            split_windows("20260315", "20260315"),
            [DateWindow(bgn_de="20260315", end_de="20260315")],
        )

    def test_starts_that_overshot_under_the_old_fixed_92_day_window(self) -> None:
        # 이 시작일들은 예전 고정 92일 창에서 "3개월"을 2~3일 초과했다
        # (예: 20260101 -> 20260402, 캘린더 마감은 20260331). 독립 오라클로
        # 검증해 각 창의 끝이 정확히 "3 달력월 후 -1일"과 같아야 한다.
        cases = ["20260101", "20260201", "20251201", "20240101", "20240201"]
        for bgn_de in cases:
            with self.subTest(bgn_de=bgn_de):
                windows = split_windows(bgn_de, "20291231")
                first_window = windows[0]
                oracle_end = _calendar_month_oracle_window_end(bgn_de)
                self.assertLessEqual(
                    int(first_window.end_de),
                    int(oracle_end),
                    f"{bgn_de}에서 시작한 창이 3 달력월({oracle_end})을 초과했습니다: "
                    f"{first_window.end_de}",
                )
                self.assertEqual(
                    first_window.end_de,
                    oracle_end,
                    f"{bgn_de}에서 시작한 창의 끝({first_window.end_de})이 "
                    f"3 달력월 경계({oracle_end})와 일치하지 않습니다.",
                )

    def test_full_year_range_splits_into_four_windows_none_over_three_calendar_months(
        self,
    ) -> None:
        windows = split_windows("20250701", "20260630")
        self.assertEqual(len(windows), 4)
        for window in windows:
            oracle_end = _calendar_month_oracle_window_end(window.bgn_de)
            self.assertLessEqual(int(window.end_de), int(oracle_end))

    def test_windows_are_contiguous_no_gaps_or_overlaps_and_cover_full_range(self) -> None:
        bgn_de, end_de = "20250701", "20260630"
        windows = split_windows(bgn_de, end_de)
        self.assertEqual(windows[0].bgn_de, bgn_de)
        self.assertEqual(windows[-1].end_de, end_de)
        for prev, nxt in zip(windows, windows[1:]):
            prev_end = datetime.strptime(prev.end_de, "%Y%m%d").date()
            next_bgn = datetime.strptime(nxt.bgn_de, "%Y%m%d").date()
            self.assertEqual(
                next_bgn,
                prev_end + timedelta(days=1),
                "창 사이에 빠짐(gap) 또는 중복(overlap)이 있습니다.",
            )

    def test_deterministic_same_input_produces_same_output(self) -> None:
        self.assertEqual(
            split_windows("20250701", "20260630"),
            split_windows("20250701", "20260630"),
        )

    def test_start_after_end_raises(self) -> None:
        with self.assertRaises(ValueError):
            split_windows("20260201", "20260101")


class ClassifyCategoryTests(unittest.TestCase):
    def test_consolidated_audit_report(self) -> None:
        self.assertEqual(classify_category("연결감사보고서"), "연결감사보고서")

    def test_plain_audit_report(self) -> None:
        self.assertEqual(classify_category("감사보고서"), "감사보고서")

    def test_business_report(self) -> None:
        self.assertEqual(classify_category("사업보고서"), "사업보고서")

    def test_correction_variant_still_classified(self) -> None:
        self.assertEqual(classify_category("[기재정정]연결감사보고서"), "연결감사보고서")

    def test_unrelated_name_is_other(self) -> None:
        self.assertEqual(classify_category("주요사항보고서"), "기타")


def _fake_search(responses: dict) -> callable:
    """`(pblntf_ty, bgn_de, end_de, page_no)` -> 결과/예외 매핑으로 만든
    `search_disclosures_structured`의 대체 코루틴 함수를 반환한다."""

    async def fake(
        corp_name="",
        corp_code="",
        bgn_de="",
        end_de="",
        last_reprt_at="",
        pblntf_ty="",
        pblntf_detail_ty="",
        corp_cls="",
        sort="date",
        sort_mth="desc",
        page_no=1,
        page_count=20,
    ):
        key = (pblntf_ty, bgn_de, end_de, page_no)
        outcome = responses[key]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return fake


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_pages_of_a_window_are_fetched_and_collected(self) -> None:
        window_args = ("F", "20260101", "20260201")
        responses = {
            (*window_args, 1): DisclosureSearchResult(
                records=[_record("1"), _record("2")], total_count=5, total_page=3, page_no=1
            ),
            (*window_args, 2): DisclosureSearchResult(
                records=[_record("3")], total_count=5, total_page=3, page_no=2
            ),
            (*window_args, 3): DisclosureSearchResult(
                records=[_record("4"), _record("5")], total_count=5, total_page=3, page_no=3
            ),
        }

        with patch("dart_search_mcp.collect.search_disclosures_structured", _fake_search(responses)):
            manifest = await collect_disclosures(
                targets=[("F", "감사보고서")],
                bgn_de="20260101",
                end_de="20260201",
                pace_seconds=0,
            )

        self.assertIsInstance(manifest, CollectionManifest)
        self.assertEqual({r.rcept_no for r in manifest.records}, {"1", "2", "3", "4", "5"})
        self.assertEqual(manifest.total_records, 5)
        self.assertEqual(manifest.failed_pages, [])


class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_returned_disclosure_search_error_is_retried_and_records_recovered(self) -> None:
        """회귀 방지: 예외가 아니라 "반환된" DisclosureSearchError도 재시도해야
        한다. 프로토타입은 예외만 재시도해서 이런 페이지의 레코드가 누락됐다."""
        window_args = ("F", "20260101", "20260201")
        calls: list[int] = []

        async def flaky(**kwargs):
            page_no = kwargs["page_no"]
            if page_no == 1:
                calls.append(1)
                if len(calls) == 1:
                    return DisclosureSearchError(message="일시적인 오류입니다.")
                return DisclosureSearchResult(
                    records=[_record("100")], total_count=1, total_page=1, page_no=1
                )
            raise AssertionError(f"unexpected page {page_no}")

        with patch("dart_search_mcp.collect.search_disclosures_structured", flaky):
            manifest = await collect_disclosures(
                targets=[("F", "감사보고서")],
                bgn_de="20260101",
                end_de="20260201",
                max_retries=3,
                pace_seconds=0,
            )

        self.assertEqual(len(calls), 2, "실패 후 재시도가 이루어져야 한다")
        self.assertEqual({r.rcept_no for r in manifest.records}, {"100"})
        self.assertEqual(manifest.failed_pages, [])

    async def test_page_failing_all_retries_is_recorded_and_run_completes(self) -> None:
        window_args = ("F", "20260101", "20260201")
        attempts = {"count": 0}

        async def always_fails(**kwargs):
            attempts["count"] += 1
            return DisclosureSearchError(message="영구적인 오류입니다.")

        with patch("dart_search_mcp.collect.search_disclosures_structured", always_fails):
            manifest = await collect_disclosures(
                targets=[("F", "감사보고서")],
                bgn_de="20260101",
                end_de="20260201",
                max_retries=3,
                pace_seconds=0,
            )

        self.assertEqual(attempts["count"], 3)
        self.assertEqual(manifest.records, [])
        self.assertEqual(len(manifest.failed_pages), 1)
        self.assertEqual(manifest.failed_pages[0].page_no, 1)
        self.assertIn("영구적인 오류", manifest.failed_pages[0].message)

    async def test_exception_is_also_retried(self) -> None:
        attempts = {"count": 0}

        async def raises_then_succeeds(**kwargs):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("네트워크 오류")
            return DisclosureSearchResult(records=[_record("200")], total_count=1, total_page=1, page_no=1)

        with patch("dart_search_mcp.collect.search_disclosures_structured", raises_then_succeeds):
            manifest = await collect_disclosures(
                targets=[("F", "감사보고서")],
                bgn_de="20260101",
                end_de="20260201",
                max_retries=3,
                pace_seconds=0,
            )

        self.assertEqual(attempts["count"], 2)
        self.assertEqual({r.rcept_no for r in manifest.records}, {"200"})


class FilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_matching_keyword_is_kept(self) -> None:
        async def fake(**kwargs):
            return DisclosureSearchResult(
                records=[
                    _record("1", report_name="감사보고서"),
                    _record("2", report_name="주요사항보고서"),
                ],
                total_count=2,
                total_page=1,
                page_no=1,
            )

        with patch("dart_search_mcp.collect.search_disclosures_structured", fake):
            manifest = await collect_disclosures(
                targets=[("F", "감사보고서")], bgn_de="20260101", end_de="20260201", pace_seconds=0
            )

        self.assertEqual([r.rcept_no for r in manifest.records], ["1"])

    async def test_consolidated_vs_plain_audit_report_classification(self) -> None:
        async def fake(**kwargs):
            return DisclosureSearchResult(
                records=[
                    _record("1", report_name="감사보고서"),
                    _record("2", report_name="연결감사보고서"),
                ],
                total_count=2,
                total_page=1,
                page_no=1,
            )

        with patch("dart_search_mcp.collect.search_disclosures_structured", fake):
            manifest = await collect_disclosures(
                targets=[("F", "감사보고서")], bgn_de="20260101", end_de="20260201", pace_seconds=0
            )

        by_rcept = {r.rcept_no: r.category for r in manifest.records}
        self.assertEqual(by_rcept, {"1": "감사보고서", "2": "연결감사보고서"})
        self.assertEqual(manifest.counts_by_category, {"감사보고서": 1, "연결감사보고서": 1})

    async def test_exclude_corrections_drops_bracketed_variants(self) -> None:
        async def fake(**kwargs):
            return DisclosureSearchResult(
                records=[
                    _record("1", report_name="감사보고서"),
                    _record("2", report_name="[기재정정]감사보고서"),
                    _record("3", report_name="사업보고서[첨부추가]"),
                ],
                total_count=3,
                total_page=1,
                page_no=1,
            )

        with patch("dart_search_mcp.collect.search_disclosures_structured", fake):
            kept_with_corrections = await collect_disclosures(
                targets=[("F", "감사보고서"), ("A", "사업보고서")],
                bgn_de="20260101",
                end_de="20260201",
                pace_seconds=0,
            )
            excluded = await collect_disclosures(
                targets=[("F", "감사보고서"), ("A", "사업보고서")],
                bgn_de="20260101",
                end_de="20260201",
                exclude_corrections=True,
                pace_seconds=0,
            )

        self.assertEqual({r.rcept_no for r in kept_with_corrections.records}, {"1", "2", "3"})
        self.assertEqual({r.rcept_no for r in excluded.records}, {"1"})


class DedupeTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_rcept_no_across_two_windows_collapses_to_one(self) -> None:
        # 2026-01-01 ~ 2026-12-31 => 4개 창으로 분할. 첫 두 창에서 같은
        # rcept_no가 나오는 경우를 시뮬레이션한다.
        async def fake(bgn_de="", **kwargs):
            return DisclosureSearchResult(
                records=[_record("dup", report_name="감사보고서")], total_count=1, total_page=1, page_no=1
            )

        with patch("dart_search_mcp.collect.search_disclosures_structured", fake):
            manifest = await collect_disclosures(
                targets=[("F", "감사보고서")], bgn_de="20260101", end_de="20261231", pace_seconds=0
            )

        self.assertEqual(len(manifest.records), 1)
        self.assertEqual(manifest.records[0].rcept_no, "dup")


class CheckpointResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_skips_succeeded_pages_and_produces_complete_set(self) -> None:
        window_args = ("F", "20260101", "20260201")
        page1_calls = {"count": 0}

        async def fails_page2_first_run(**kwargs):
            page_no = kwargs["page_no"]
            if page_no == 1:
                page1_calls["count"] += 1
                return DisclosureSearchResult(
                    records=[_record("1")], total_count=2, total_page=2, page_no=1
                )
            raise RuntimeError("일시적으로 중단됨")

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_path = Path(tmp) / "run.checkpoint.json"

            with patch(
                "dart_search_mcp.collect.search_disclosures_structured", fails_page2_first_run
            ):
                first_manifest = await collect_disclosures(
                    targets=[("F", "감사보고서")],
                    bgn_de="20260101",
                    end_de="20260201",
                    max_retries=1,
                    pace_seconds=0,
                    checkpoint=checkpoint_path,
                )

            self.assertEqual({r.rcept_no for r in first_manifest.records}, {"1"})
            self.assertEqual(len(first_manifest.failed_pages), 1)
            self.assertTrue(checkpoint_path.exists())

            page1_calls_before_resume = page1_calls["count"]

            async def succeeds_on_resume(**kwargs):
                page_no = kwargs["page_no"]
                self.assertNotEqual(page_no, 1, "이미 성공한 1페이지는 재조회하지 않아야 한다")
                return DisclosureSearchResult(
                    records=[_record("2")], total_count=2, total_page=2, page_no=page_no
                )

            with patch(
                "dart_search_mcp.collect.search_disclosures_structured", succeeds_on_resume
            ):
                second_manifest = await collect_disclosures(
                    targets=[("F", "감사보고서")],
                    bgn_de="20260101",
                    end_de="20260201",
                    max_retries=1,
                    pace_seconds=0,
                    checkpoint=checkpoint_path,
                )

            self.assertEqual(page1_calls_before_resume, 1)
            self.assertEqual({r.rcept_no for r in second_manifest.records}, {"1", "2"})
            self.assertEqual(second_manifest.failed_pages, [])


class CheckpointRunParamsGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_resume_with_different_end_de_raises_clear_error(self) -> None:
        async def fake(**kwargs):
            return DisclosureSearchResult(
                records=[_record("1")], total_count=1, total_page=1, page_no=1
            )

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_path = Path(tmp) / "run.checkpoint.json"

            with patch("dart_search_mcp.collect.search_disclosures_structured", fake):
                await collect_disclosures(
                    targets=[("F", "감사보고서")],
                    bgn_de="20260101",
                    end_de="20260201",
                    pace_seconds=0,
                    checkpoint=checkpoint_path,
                )

                with self.assertRaises(ValueError) as ctx:
                    await collect_disclosures(
                        targets=[("F", "감사보고서")],
                        bgn_de="20260101",
                        end_de="20260301",  # 이전 실행과 다른 end_de
                        pace_seconds=0,
                        checkpoint=checkpoint_path,
                    )

        message = str(ctx.exception)
        self.assertIn("체크포인트", message)

    async def test_resume_with_different_targets_raises_clear_error(self) -> None:
        async def fake(**kwargs):
            return DisclosureSearchResult(
                records=[_record("1")], total_count=1, total_page=1, page_no=1
            )

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_path = Path(tmp) / "run.checkpoint.json"

            with patch("dart_search_mcp.collect.search_disclosures_structured", fake):
                await collect_disclosures(
                    targets=[("F", "감사보고서")],
                    bgn_de="20260101",
                    end_de="20260201",
                    pace_seconds=0,
                    checkpoint=checkpoint_path,
                )

                with self.assertRaises(ValueError):
                    await collect_disclosures(
                        targets=[("A", "사업보고서")],  # 이전 실행과 다른 targets
                        bgn_de="20260101",
                        end_de="20260201",
                        pace_seconds=0,
                        checkpoint=checkpoint_path,
                    )

    async def test_resume_with_same_params_does_not_raise(self) -> None:
        async def fake(**kwargs):
            return DisclosureSearchResult(
                records=[_record("1")], total_count=1, total_page=1, page_no=1
            )

        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_path = Path(tmp) / "run.checkpoint.json"

            with patch("dart_search_mcp.collect.search_disclosures_structured", fake):
                await collect_disclosures(
                    targets=[("F", "감사보고서")],
                    bgn_de="20260101",
                    end_de="20260201",
                    pace_seconds=0,
                    checkpoint=checkpoint_path,
                )
                # 같은 파라미터로 재개하면 오류 없이 정상 진행되어야 한다.
                manifest = await collect_disclosures(
                    targets=[("F", "감사보고서")],
                    bgn_de="20260101",
                    end_de="20260201",
                    pace_seconds=0,
                    checkpoint=checkpoint_path,
                )

        self.assertEqual({r.rcept_no for r in manifest.records}, {"1"})


class ManifestSerializationTests(unittest.TestCase):
    def test_to_dict_round_trips_through_json(self) -> None:
        from dart_search_mcp.collect import CollectedRecord, FailedPage

        manifest = CollectionManifest(
            records=[
                CollectedRecord(
                    category="감사보고서",
                    report_name="감사보고서",
                    rcept_no="1",
                    rcept_dt="20260101",
                    corp_code="00126380",
                    corp_name="삼성전자",
                    stock_code="005930",
                    corp_cls="Y",
                    flr_nm="삼정회계법인",
                    remark="",
                    source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=1",
                )
            ],
            counts_by_category={"감사보고서": 1},
            total_records=1,
            windows=[DateWindow(bgn_de="20260101", end_de="20260201")],
            targets=[("F", "감사보고서")],
            failed_pages=[FailedPage("F", "감사보고서", "20260101", "20260201", 2, "오류")],
            generated_at="2026-07-09T00:00:00+00:00",
        )

        payload = json.loads(json.dumps(manifest.to_dict(), ensure_ascii=False))
        self.assertEqual(payload["total_records"], 1)
        self.assertEqual(payload["records"][0]["rcept_no"], "1")
        self.assertEqual(payload["windows"], [["20260101", "20260201"]])
        self.assertEqual(payload["targets"], [["F", "감사보고서"]])
        self.assertEqual(payload["failed_pages"][0]["page_no"], 2)


class CollectDisclosuresCliHelpTests(unittest.TestCase):
    def test_help_exits_zero(self) -> None:
        from click.testing import CliRunner

        import cli as cli_module

        runner = CliRunner()
        result = runner.invoke(cli_module.cli, ["collect-disclosures", "--help"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--from", result.output)
        self.assertIn("--to", result.output)
        self.assertIn("--targets", result.output)


if __name__ == "__main__":
    unittest.main()
