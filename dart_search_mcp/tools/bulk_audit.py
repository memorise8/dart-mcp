"""여러 필링의 감사/연결감사보고서 XML을 체크포인트 가능하게 일괄
추출하는 모듈(Step 2b의 bulk 버전).

Step 2b(`dart_search_mcp.tools.audit_docs.extract_audit_documents_core`)가
필링 한 건을 처리했다면, 이 모듈은 그 함수를 여러 필링(rcept_no)에 대해
반복 호출하면서:

- **입력 소스:** Step 1 수집기(`dart_search_mcp.collect.collect_disclosures`)가
  쓴 매니페스트 JSON(`records[].rcept_no`) 또는 접수번호 문자열 배열 JSON을
  읽어 대상 필링 목록을 만든다.
- **예외 격리:** 필링 한 건에서 어떤 예외가 나든(파손/암호화된 ZIP 등 포함)
  그 필링만 `failed`로 기록하고 전체 실행은 계속한다 - 절대 중단하지 않는다.
- **상태 분류:** 각 필링을 `succeeded`/`skipped_no_audit`/
  `skipped_no_consolidated`/`failed` 중 하나로 분류한다. 분류는
  `extract_audit_documents_core`가 항상 `rcept_no`를 직접 받아 호출되는
  bulk 경로에서 실제로 나올 수 있는 결과만 반영한다(예:
  corp_name 해석 실패류는 bulk에서 절대 발생하지 않으므로 `failed`로
  귀속된다) - `AuditDocsError.kind`라는 구조화된 판별자를 보고 분류하며,
  `AuditDocsError.message`의 한국어 문구를 문자열 매칭하지 않는다(문구가
  바뀌어도 분류는 바뀌지 않는다). `skipped_no_consolidated`는 오직
  `--require-consolidated`(`AuditDocsError(kind="no_consolidated")`)에서만
  나온다 - `--require-consolidated` 없이 연결감사보고서만 없는 성공 outcome은
  감사보고서가 수집됐다면 `succeeded`다(단일 필링 도구와 동일한 판정).
- **체크포인트/재개:** 완료된 필링별 상태를 체크포인트 경로에 저장해,
  `--resume` 시 이미 `succeeded`인 필링은 다시 처리하지 않는다.
  `dart_search_mcp.collect`의 체크포인트/원자적 쓰기/run-params 가드 패턴을
  그대로 재사용한다.

이 모듈은 어떤 MCP 도구도 등록하지 않는다(대량 처리는 오래 걸릴 수 있어
블로킹 MCP 도구 호출로 적합하지 않다) - `cli.py`의
`dart bulk-audit-documents` 명령을 통해서만 제공한다(CLI 전용). 단일 필링
추출은 계속 `extract_audit_documents`/`extract_audit_documents_core`를
사용한다.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dart_search_mcp.collect import _is_correction
from dart_search_mcp.redact import redact
from dart_search_mcp.tools.audit_docs import (
    AuditDocsError,
    AuditDocsOutcome,
    extract_audit_documents_core,
)

_BULK_MANIFEST_FILENAME = "bulk-manifest.json"

# `AuditDocsError.kind` -> bulk 상태. bulk는 항상 `rcept_no`를 직접 지정해
# `extract_audit_documents_core`를 호출하므로 corp_name 해석 관련 kind
# (validation/ambiguous_corp/corp_not_found 등)는 이 경로에서 실제로 발생하지
# 않지만, 안전하게 `failed`로 귀속해둔다. `no_consolidated`만 skip이다.
_ERROR_KIND_TO_STATUS: dict[str, str] = {
    "no_consolidated": "skipped_no_consolidated",
}
_DEFAULT_ERROR_STATUS = "failed"


class BulkAuditSourceError(Exception):
    """입력 소스(매니페스트 JSON 또는 rcept-json)를 읽거나 해석할 수 없을 때."""


@dataclass(frozen=True, slots=True)
class FilingInput:
    """일괄 처리 대상 필링 한 건. `corp_code`/`corp_name`/`report_name`은
    있으면 결과 매니페스트를 보강하는 용도일 뿐, 조회에는 쓰지 않는다
    (`rcept_no`를 항상 직접 지정해 `extract_audit_documents_core`를
    호출한다)."""

    rcept_no: str
    corp_code: str = ""
    corp_name: str = ""
    report_name: str = ""


@dataclass(frozen=True, slots=True)
class FilingResult:
    """필링 한 건의 처리 결과."""

    rcept_no: str
    corp_code: str = ""
    corp_name: str = ""
    status: str = "failed"
    output_path: str | None = None
    message: str | None = None


@dataclass(frozen=True, slots=True)
class BulkAuditManifest:
    """`bulk_extract_audit_documents`의 최종 결과. JSON으로 직렬화해
    `<output_dir>/bulk-manifest.json`에 쓴다."""

    results: list[FilingResult] = field(default_factory=list)
    counts_by_status: dict[str, int] = field(default_factory=dict)
    total: int = 0
    source: str = ""
    include: str = "both"
    require_consolidated: bool = False
    generated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "source": self.source,
            "include": self.include,
            "require_consolidated": self.require_consolidated,
            "total": self.total,
            "counts_by_status": self.counts_by_status,
            "results": [
                {
                    "rcept_no": result.rcept_no,
                    "corp_code": result.corp_code,
                    "corp_name": result.corp_name,
                    "status": result.status,
                    "output_path": result.output_path,
                    "message": result.message,
                }
                for result in self.results
            ],
        }


def load_filings_from_manifest(path: str | Path, *, exclude_corrections: bool = False) -> list[FilingInput]:
    """Step 1 수집 매니페스트 JSON(`records[].rcept_no`)에서 대상 필링
    목록을 읽는다."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BulkAuditSourceError(f"매니페스트를 읽을 수 없습니다 ({path}): {exc}") from exc

    records = data.get("records") if isinstance(data, dict) else None
    if records is None:
        raise BulkAuditSourceError(f"매니페스트 형식이 올바르지 않습니다(records 없음): {path}")

    filings: list[FilingInput] = []
    for record in records:
        report_name = record.get("report_name", "") if isinstance(record, dict) else ""
        if exclude_corrections and _is_correction(report_name):
            continue
        rcept_no = (record.get("rcept_no", "") if isinstance(record, dict) else "").strip()
        if not rcept_no:
            continue
        filings.append(
            FilingInput(
                rcept_no=rcept_no,
                corp_code=record.get("corp_code", ""),
                corp_name=record.get("corp_name", ""),
                report_name=report_name,
            )
        )
    return filings


def load_filings_from_rcept_json(path: str | Path) -> list[FilingInput]:
    """접수번호 문자열 배열 JSON에서 대상 필링 목록을 읽는다."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BulkAuditSourceError(f"rcept-json을 읽을 수 없습니다 ({path}): {exc}") from exc

    if not isinstance(data, list):
        raise BulkAuditSourceError(f"rcept-json 형식이 올바르지 않습니다(배열이어야 함): {path}")

    return [FilingInput(rcept_no=str(item).strip()) for item in data if str(item).strip()]


def _classify_error_kind(kind: str) -> str:
    """`AuditDocsError.kind`를 bulk 상태로 매핑한다. `message`의 한국어
    문구는 전혀 보지 않는다 - 이 모듈이 그 문구를 리워딩해도 분류는
    바뀌지 않는다."""
    return _ERROR_KIND_TO_STATUS.get(kind, _DEFAULT_ERROR_STATUS)


def _classify_success_outcome(outcome: AuditDocsOutcome) -> str:
    """성공 outcome(오류 없이 완료됨)을 분류한다. `require_consolidated=True`인데
    연결감사보고서가 없는 경우는 이미 `AuditDocsError(kind="no_consolidated")`로
    걸러지므로, 여기 도달했다는 것은 require_consolidated가 요청되지
    않았거나 이미 만족됐다는 뜻이다 - 따라서 이 함수는 절대
    `skipped_no_consolidated`를 반환하지 않는다(그 상태는 오직
    `_ERROR_KIND_TO_STATUS`의 오류 경로에서만 나온다).

    `--require-consolidated` 없이 연결감사보고서가 없는 것은 정상적인
    "찾지 못함" 결과일 뿐이다 - 감사보고서(또는 연결감사보고서)가 실제로
    하나라도 추출/기록됐으면 `succeeded`다. 요청한 것이 ZIP 안에 전혀
    실재하지 않아 아무것도 쓰지 못했을 때만 `skipped_no_audit`이다."""
    if outcome.audit_found or outcome.consolidated_found:
        return "succeeded"
    return "skipped_no_audit"


async def _process_filing(
    filing: FilingInput,
    *,
    output_dir: str,
    include: str,
    require_consolidated: bool,
) -> FilingResult:
    """필링 한 건을 추출하고 결과를 분류한다. 어떤 예외가 발생하든(파손/암호화된
    ZIP 등에서 나올 수 있는 RuntimeError/NotImplementedError 포함) 여기서
    잡아서 `failed`로 반환한다 - 절대 전체 실행을 중단시키지 않는다."""
    try:
        outcome = await extract_audit_documents_core(
            rcept_no=filing.rcept_no,
            output_dir=output_dir,
            include=include,
            require_consolidated=require_consolidated,
        )
    except Exception as exc:  # noqa: BLE001 - 의도적으로 모든 예외를 이 필링에만 격리한다.
        return FilingResult(
            rcept_no=filing.rcept_no,
            corp_code=filing.corp_code,
            corp_name=filing.corp_name,
            status="failed",
            output_path=None,
            message=redact(str(exc)),
        )

    if isinstance(outcome, AuditDocsOutcome):
        status = _classify_success_outcome(outcome)
        return FilingResult(
            rcept_no=filing.rcept_no,
            corp_code=outcome.corp_code or filing.corp_code,
            corp_name=filing.corp_name,
            status=status,
            output_path=outcome.output_dir,
            message=None,
        )

    if isinstance(outcome, AuditDocsError):
        status = _classify_error_kind(outcome.kind)
        return FilingResult(
            rcept_no=filing.rcept_no,
            corp_code=filing.corp_code,
            corp_name=filing.corp_name,
            status=status,
            output_path=None,
            message=redact(outcome.message),
        )

    # 방어적 처리: 계약상 나올 수 없지만, 미래에 반환 타입이 바뀌어도 이 필링만
    # failed로 격리하고 전체 실행은 계속한다.
    return FilingResult(
        rcept_no=filing.rcept_no,
        corp_code=filing.corp_code,
        corp_name=filing.corp_name,
        status="failed",
        output_path=None,
        message=redact(f"오류: 예상치 못한 반환 타입입니다: {outcome!r}"),
    )


def _empty_state() -> dict[str, Any]:
    return {"filings": {}, "run_params": None}


def _load_checkpoint(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_state()
    if not isinstance(data, dict):
        return _empty_state()
    data.setdefault("filings", {})
    data.setdefault("run_params", None)
    return data


def _run_params_header(rcept_nos: list[str], *, include: str, require_consolidated: bool) -> dict[str, Any]:
    """체크포인트 헤더에 저장할, JSON 직렬화 가능한 실행 파라미터 스냅샷."""
    return {
        "rcept_nos": rcept_nos,
        "include": include,
        "require_consolidated": require_consolidated,
    }


def _ensure_run_params_match_checkpoint(state: dict[str, Any], run_params: dict[str, Any]) -> None:
    """`--resume`으로 체크포인트를 이어 쓸 때, 이번 호출의 대상 필링 목록/옵션이
    체크포인트에 기록된 이전 실행과 다르면 명확한 오류를 낸다(다른 소스/옵션의
    진행 상황이 이번 실행과 뒤섞이는 것을 막는다)."""
    existing = state.get("run_params")
    if existing is not None and existing != run_params:
        raise ValueError(
            "체크포인트의 실행 파라미터가 현재 호출과 다릅니다 - 다른 입력 소스나 "
            "include/require_consolidated 옵션으로 같은 체크포인트를 재사용하면 "
            "이전 실행의 진행 상황이 이번 실행과 맞지 않아 결과가 뒤섞일 수 있습니다. "
            f"체크포인트: {existing}, 현재 호출: {run_params}. "
            "--resume 없이 새로 시작하거나 다른 출력 디렉토리를 사용하세요."
        )
    state["run_params"] = run_params


def _save_checkpoint(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(path)


def _write_bulk_manifest(output_dir: str, manifest: BulkAuditManifest) -> str:
    os.makedirs(output_dir, exist_ok=True)
    manifest_path = os.path.join(output_dir, _BULK_MANIFEST_FILENAME)
    tmp_path = manifest_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest.to_dict(), f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, manifest_path)
    return manifest_path


async def bulk_extract_audit_documents(
    *,
    filings: list[FilingInput],
    output_dir: str,
    include: str = "both",
    require_consolidated: bool = False,
    limit: int | None = None,
    sleep_seconds: float = 0.2,
    checkpoint: str | Path | None = None,
    generated_at: str | None = None,
    source: str = "",
) -> BulkAuditManifest:
    """`filings`의 각 필링에 대해 `extract_audit_documents_core`를 호출해
    감사/연결감사 XML을 `output_dir/<rcept_no>/`에 추출하고, 같은 디렉토리에
    실행 매니페스트(`bulk-manifest.json`)를 쓴다.

    필링 한 건의 실패(반환된 `AuditDocsError` 또는 raise된 예외 모두)는 그
    필링만 실패로 기록하고 전체 실행을 중단시키지 않는다.

    `checkpoint`가 주어지면 필링별 상태를 그 경로에 저장한다 - 같은
    `checkpoint` 경로로 다시 호출하면 이미 `succeeded`인 필링은 다시
    처리하지 않고 이어서 처리한다(`failed`/`skipped_*`/미처리 필링은 재시도).

    `limit`이 주어지면 처리 대상 필링 목록 자체를 앞에서부터 `limit`개로
    제한한다(이미 성공한 필링의 재사용 여부와 무관하게 이번 실행이 다루는
    전체 범위를 줄인다).

    이 함수는 `datetime.now()`를 호출하지 않는다 - `generated_at`은 호출자가
    전달해야 한다.
    """
    filings_list = list(filings)
    if limit is not None:
        filings_list = filings_list[: max(0, limit)]

    checkpoint_path = Path(checkpoint) if checkpoint else None
    state = _load_checkpoint(checkpoint_path)

    run_params = _run_params_header(
        [filing.rcept_no for filing in filings_list],
        include=include,
        require_consolidated=require_consolidated,
    )
    _ensure_run_params_match_checkpoint(state, run_params)
    _save_checkpoint(checkpoint_path, state)

    for filing in filings_list:
        existing = state["filings"].get(filing.rcept_no)
        if existing is not None and existing.get("status") == "succeeded":
            # --resume: 이미 성공한 필링은 재처리하지 않는다.
            continue

        result = await _process_filing(
            filing,
            output_dir=output_dir,
            include=include,
            require_consolidated=require_consolidated,
        )
        state["filings"][filing.rcept_no] = {
            "rcept_no": result.rcept_no,
            "corp_code": result.corp_code,
            "corp_name": result.corp_name,
            "status": result.status,
            "output_path": result.output_path,
            "message": result.message,
        }
        _save_checkpoint(checkpoint_path, state)

        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)

    results = [FilingResult(**state["filings"][filing.rcept_no]) for filing in filings_list]

    counts_by_status: dict[str, int] = {}
    for result in results:
        counts_by_status[result.status] = counts_by_status.get(result.status, 0) + 1

    manifest = BulkAuditManifest(
        results=results,
        counts_by_status=counts_by_status,
        total=len(results),
        source=source,
        include=include,
        require_consolidated=require_consolidated,
        generated_at=generated_at or "",
    )

    _write_bulk_manifest(output_dir, manifest)

    return manifest
