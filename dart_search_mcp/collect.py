"""OpenDART `list.json`을 이용한 재사용 가능한 대량(bulk) 공시 수집기.

`dart_search_mcp.tools.disclosures.search_disclosures_structured`를 그대로
재사용해, 다음을 처리한다:

- **창 분할(windowing):** `corp_code` 없이 `list.json`을 조회하면 OpenDART가
  검색기간을 3개월로 제한한다(`status 100`). 임의의 `[bgn_de, end_de]` 구간을
  ``MAX_WINDOW_DAYS``(92일) 이하의 연속된 창으로 결정적으로 분할한다.
- **완전 페이지네이션:** 각 (대상, 창) 조합마다 1페이지를 조회해 `total_page`를
  얻은 뒤 전체 페이지를 순회한다.
- **재시도:** 예외로 실패하는 경우와, `search_disclosures_structured`가
  예외 없이 `DisclosureSearchError`를 "반환"하는 경우를 모두 재시도한다
  (반환된 오류만 재시도하지 않으면 페이지가 조용히 누락된다 - 과거
  프로토타입에서 실제로 ~200건이 이렇게 누락된 사례가 있었다).
- **필터/분류:** `report_name`에 대상 키워드가 포함된 레코드만 남기고,
  연결감사보고서/감사보고서/사업보고서/기타로 분류한다.
- **중복 제거:** `rcept_no` 기준으로 전체 창/대상에 걸쳐 중복을 제거한다.
- **체크포인트/재개:** `checkpoint` 경로가 주어지면 완료된 (대상, 창, 페이지)
  단위와 수집된 레코드를 그 경로에 저장해, 중단된 실행을 재개할 때 이미
  성공한 페이지를 다시 조회하지 않는다.

이 모듈은 순수 핵심 로직만 제공하며 어떤 MCP 도구도 등록하지 않는다. 대량
수집은 오래 걸릴 수 있어(연간 수만 건, 수백 회의 페이지 호출) 하나의 블로킹
MCP 도구 호출로 적합하지 않으므로, 대량 수집은 `cli.py`의
`dart collect-disclosures` 명령을 통해서만 제공한다(CLI 전용). 단일 회사
공시 조회는 계속 기존 `search_disclosures`/`search_disclosures_structured`
도구를 사용한다.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from dart_search_mcp.tools.disclosures import (
    DisclosureSearchResult,
    search_disclosures_structured,
)

# OpenDART가 `corp_code` 없는 `list.json` 조회에 강제하는 "3개월" 제한을
# 안전하게 지키기 위한 창 길이(일). 91~92일이 3개월에 해당한다.
MAX_WINDOW_DAYS = 92

# 재사용 가능한 프리셋: 감사보고서(+연결감사보고서) + 사업보고서.
# "감사보고서" 키워드는 "연결감사보고서"도 부분일치로 포함한다.
AUDIT_AND_BUSINESS_REPORT_TARGETS: tuple[tuple[str, str], ...] = (
    ("F", "감사보고서"),
    ("A", "사업보고서"),
)

_CORRECTION_MARKERS: tuple[str, ...] = ("[기재정정]", "[첨부추가]")


@dataclass(frozen=True, slots=True)
class DateWindow:
    """`list.json` 조회 한 번에 대응하는 날짜 구간(YYYYMMDD, 양끝 포함)."""

    bgn_de: str
    end_de: str


@dataclass(frozen=True, slots=True)
class CollectedRecord:
    """필터·분류를 마친 뒤 매니페스트에 최종적으로 담기는 공시 레코드."""

    category: str
    report_name: str
    rcept_no: str
    rcept_dt: str
    corp_code: str
    corp_name: str
    stock_code: str
    corp_cls: str
    flr_nm: str
    remark: str
    source_url: str


@dataclass(frozen=True, slots=True)
class FailedPage:
    """`max_retries`를 모두 소진하고도 실패한 (대상, 창, 페이지)."""

    pblntf_ty: str
    name_keyword: str
    bgn_de: str
    end_de: str
    page_no: int
    message: str


@dataclass(frozen=True, slots=True)
class CollectionManifest:
    """`collect_disclosures`의 최종 결과. JSON으로 직렬화해 파일로 쓴다."""

    records: list[CollectedRecord]
    counts_by_category: dict[str, int]
    total_records: int
    windows: list[DateWindow]
    targets: list[tuple[str, str]]
    failed_pages: list[FailedPage]
    generated_at: str

    def to_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "targets": [[pblntf_ty, keyword] for pblntf_ty, keyword in self.targets],
            "windows": [[window.bgn_de, window.end_de] for window in self.windows],
            "total_records": self.total_records,
            "counts_by_category": self.counts_by_category,
            "failed_pages": [
                {
                    "pblntf_ty": failed.pblntf_ty,
                    "name_keyword": failed.name_keyword,
                    "bgn_de": failed.bgn_de,
                    "end_de": failed.end_de,
                    "page_no": failed.page_no,
                    "message": failed.message,
                }
                for failed in self.failed_pages
            ],
            "records": [
                {
                    "category": record.category,
                    "report_name": record.report_name,
                    "rcept_no": record.rcept_no,
                    "rcept_dt": record.rcept_dt,
                    "corp_code": record.corp_code,
                    "corp_name": record.corp_name,
                    "stock_code": record.stock_code,
                    "corp_cls": record.corp_cls,
                    "flr_nm": record.flr_nm,
                    "remark": record.remark,
                    "source_url": record.source_url,
                }
                for record in self.records
            ],
        }


def split_windows(bgn_de: str, end_de: str, max_days: int = MAX_WINDOW_DAYS) -> list[DateWindow]:
    """`[bgn_de, end_de]`(YYYYMMDD)를 `max_days`일 이하의 연속된 창으로 나눈다.

    입력에서만 결정적으로 계산한다(`datetime.now()`를 호출하지 않는다).
    """
    start = datetime.strptime(bgn_de, "%Y%m%d").date()
    end = datetime.strptime(end_de, "%Y%m%d").date()
    if start > end:
        raise ValueError(f"bgn_de({bgn_de})는 end_de({end_de})보다 이후일 수 없습니다.")

    windows: list[DateWindow] = []
    current = start
    while current <= end:
        window_end = min(current + timedelta(days=max_days - 1), end)
        windows.append(
            DateWindow(bgn_de=current.strftime("%Y%m%d"), end_de=window_end.strftime("%Y%m%d"))
        )
        current = window_end + timedelta(days=1)
    return windows


def classify_category(report_name: str) -> str:
    """`report_name` 부분일치로 연결감사보고서/감사보고서/사업보고서/기타를 분류한다."""
    if "연결감사보고서" in report_name:
        return "연결감사보고서"
    if "감사보고서" in report_name:
        return "감사보고서"
    if "사업보고서" in report_name:
        return "사업보고서"
    return "기타"


def _is_correction(report_name: str) -> bool:
    return any(marker in report_name for marker in _CORRECTION_MARKERS)


@dataclass(frozen=True, slots=True)
class _PageFailure:
    message: str


async def _fetch_page_with_retry(
    *,
    pblntf_ty: str,
    window: DateWindow,
    page_no: int,
    page_count: int,
    max_retries: int,
    pace_seconds: float,
) -> DisclosureSearchResult | _PageFailure:
    """한 페이지를 조회하고, 예외 또는 "반환된" `DisclosureSearchError` 모두에
    대해 `max_retries`번까지 재시도한다."""
    attempts = max(1, max_retries)
    last_message = "알 수 없는 오류로 페이지 조회에 실패했습니다."

    for attempt in range(attempts):
        try:
            result = await search_disclosures_structured(
                bgn_de=window.bgn_de,
                end_de=window.end_de,
                pblntf_ty=pblntf_ty,
                page_no=page_no,
                page_count=page_count,
            )
        except Exception as exc:  # 네트워크/파싱 예외 -> 재시도
            last_message = str(exc)
        else:
            if isinstance(result, DisclosureSearchResult):
                return result
            # `DisclosureSearchError`(또는 방어적으로 다른 타입) - 예외 없이
            # "반환된" 오류도 반드시 재시도해야 한다(모듈 docstring 참고).
            last_message = getattr(result, "message", repr(result))

        if attempt < attempts - 1 and pace_seconds:
            await asyncio.sleep(pace_seconds * (attempt + 1))

    return _PageFailure(message=last_message)


def _merge_records(
    state: dict, records: list, name_keyword: str, exclude_corrections: bool
) -> None:
    for record in records:
        if name_keyword not in record.report_name:
            continue
        if exclude_corrections and _is_correction(record.report_name):
            continue
        state["records"][record.rcept_no] = {
            "category": classify_category(record.report_name),
            "report_name": record.report_name,
            "rcept_no": record.rcept_no,
            "rcept_dt": record.rcept_dt,
            "corp_code": record.corp_code,
            "corp_name": record.corp_name,
            "stock_code": record.stock_code,
            "corp_cls": record.corp_cls,
            "flr_nm": record.flr_nm,
            "remark": record.remark,
            "source_url": record.source_url,
        }


def _task_key(pblntf_ty: str, name_keyword: str, window: DateWindow) -> str:
    return f"{pblntf_ty}|{name_keyword}|{window.bgn_de}|{window.end_de}"


def _empty_state() -> dict:
    return {"tasks": {}, "records": {}}


def _load_checkpoint(path: Path | None) -> dict:
    if path is None or not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("tasks", {})
    data.setdefault("records", {})
    return data


def _save_checkpoint(path: Path | None, state: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


async def _collect_task(
    *,
    pblntf_ty: str,
    name_keyword: str,
    window: DateWindow,
    page_count: int,
    max_retries: int,
    exclude_corrections: bool,
    pace_seconds: float,
    state: dict,
    failed_pages: list[FailedPage],
    checkpoint_path: Path | None,
) -> None:
    task_state = state["tasks"].setdefault(
        _task_key(pblntf_ty, name_keyword, window), {"total_page": None, "succeeded_pages": []}
    )

    page_no = 1
    while True:
        total_page = task_state["total_page"]
        if total_page is not None and page_no > total_page:
            break

        if page_no in task_state["succeeded_pages"]:
            page_no += 1
            continue

        outcome = await _fetch_page_with_retry(
            pblntf_ty=pblntf_ty,
            window=window,
            page_no=page_no,
            page_count=page_count,
            max_retries=max_retries,
            pace_seconds=pace_seconds,
        )

        if isinstance(outcome, _PageFailure):
            failed_pages.append(
                FailedPage(
                    pblntf_ty=pblntf_ty,
                    name_keyword=name_keyword,
                    bgn_de=window.bgn_de,
                    end_de=window.end_de,
                    page_no=page_no,
                    message=outcome.message,
                )
            )
            if total_page is None:
                # 1페이지가 실패해 total_page를 아직 모른다 - 이 (대상, 창)은
                # 더 진행할 수 없다. 재실행 시 체크포인트에 succeeded_pages가
                # 없으므로 1페이지부터 다시 시도된다.
                _save_checkpoint(checkpoint_path, state)
                break
        else:
            if total_page is None:
                task_state["total_page"] = outcome.total_page
                total_page = outcome.total_page
            _merge_records(state, outcome.records, name_keyword, exclude_corrections)
            task_state["succeeded_pages"].append(page_no)

        _save_checkpoint(checkpoint_path, state)

        if pace_seconds:
            await asyncio.sleep(pace_seconds)

        page_no += 1


async def collect_disclosures(
    *,
    targets: Iterable[tuple[str, str]],
    bgn_de: str,
    end_de: str,
    page_count: int = 100,
    max_retries: int = 4,
    exclude_corrections: bool = False,
    pace_seconds: float = 0.15,
    checkpoint: str | Path | None = None,
    generated_at: str | None = None,
) -> CollectionManifest:
    """`[bgn_de, end_de]` 구간에서 `targets`(공시유형, report_name 키워드 쌍)에
    해당하는 공시를 창 분할 + 완전 페이지네이션으로 수집해 중복 제거된
    매니페스트를 반환한다.

    `checkpoint`가 주어지면 진행 상황과 지금까지 수집된 레코드를 그 경로에
    저장한다 - 같은 `checkpoint` 경로로 다시 호출하면 이미 성공한 페이지는
    다시 조회하지 않고 이어서 수집한다.

    이 함수는 `datetime.now()`를 호출하지 않는다(입력에서만 결정적).
    매니페스트의 `generated_at`은 호출자가 전달해야 한다(예: CLI가
    `datetime.now(timezone.utc).isoformat()`을 전달).
    """
    targets_list = list(targets)
    windows = split_windows(bgn_de, end_de)
    checkpoint_path = Path(checkpoint) if checkpoint else None
    state = _load_checkpoint(checkpoint_path)

    failed_pages: list[FailedPage] = []

    for pblntf_ty, name_keyword in targets_list:
        for window in windows:
            await _collect_task(
                pblntf_ty=pblntf_ty,
                name_keyword=name_keyword,
                window=window,
                page_count=page_count,
                max_retries=max_retries,
                exclude_corrections=exclude_corrections,
                pace_seconds=pace_seconds,
                state=state,
                failed_pages=failed_pages,
                checkpoint_path=checkpoint_path,
            )

    records = [CollectedRecord(**value) for value in state["records"].values()]
    records.sort(key=lambda record: (record.rcept_dt, record.rcept_no))

    counts_by_category: dict[str, int] = {}
    for record in records:
        counts_by_category[record.category] = counts_by_category.get(record.category, 0) + 1

    return CollectionManifest(
        records=records,
        counts_by_category=counts_by_category,
        total_records=len(records),
        windows=windows,
        targets=targets_list,
        failed_pages=failed_pages,
        generated_at=generated_at or "",
    )
