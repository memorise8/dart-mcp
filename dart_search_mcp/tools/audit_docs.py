"""단일 필링의 공시서류 원본 ZIP에서 감사보고서/연결감사보고서 XML을 추출한다.

Step 2a(`dart_search_mcp.document_zip`)가 ZIP 엔트리를 분류하기만 했다면, 이
모듈(Step 2b)은 실제로 파일시스템에 써서 감사/연결감사 XML을 꺼내고 결과를
매니페스트 JSON으로 남긴다.

대상 접수번호(rcept_no) 해석 순서:
  1. `rcept_no`가 주어지면 그대로 사용한다.
  2. 없고 `corp_code`가 있으면 `bsns_year`와 함께
     `dart_search_mcp.tools.downloads._resolve_rcept_no`로 조회한다.
  3. 없고 `corp_name`만 있으면 `dart_search_mcp.corp.resolve_single_corp_code`로
     정확히 하나의 corp_code로 해석한 뒤(모호/미해결이면 OpenDART를 전혀
     호출하지 않고 오류), 2와 동일하게 `bsns_year`로 접수번호를 조회한다.
     `corp_name`을 OpenDART 파라미터로 직접 보내지 않는다.

`require_consolidated=True`인데 연결감사보고서 엔트리가 없으면 오류를
반환하며, 이 경우 대상 디렉토리는 전혀 만들지 않는다(부분 파일 없음).
`require_consolidated=False`(기본값)에서 연결감사보고서가 없으면 오류가
아니라 매니페스트에 "찾지 못함"으로 기록되는 정상 결과다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Literal

from dart_search_mcp.app import mcp
from dart_search_mcp.client import _fetch_dart_binary
from dart_search_mcp.corp import (
    CorpLoadError,
    CorpNameAmbiguous,
    CorpNameNotFound,
    CorpValidationError,
    resolve_single_corp_code,
)
from dart_search_mcp.document_zip import DocumentEntry, DocumentZipError, inspect_document_zip, read_entry
from dart_search_mcp.tools.downloads import _resolve_rcept_no

_VALID_INCLUDES = frozenset({"audit", "consolidated", "both"})

_MANIFEST_FILENAME = "manifest.json"

# 실패 종류를 나타내는 구조화된 판별자. 호출자(특히 `bulk_audit`)가 `message`의
# 한국어 문구를 문자열 매칭하지 않고도 실패를 분류할 수 있게 한다 - 이
# 모듈에서 `message` 문구를 리워딩해도 `kind`만 그대로면 분류는 바뀌지 않는다.
#
#   validation        입력 검증 실패 (필수 파라미터 누락, include 값 오류 등)
#   ambiguous_corp     corp_name이 둘 이상의 회사와 매치됨
#   corp_not_found     corp_name에 해당하는 회사가 없음
#   rcept_not_found    corp_code+bsns_year로 접수번호 조회에는 성공했지만 결과가 없음
#   download_error     OpenDART 호출(문서 ZIP 또는 접수번호 조회용 목록 조회) 자체가 실패
#   corrupt_zip        ZIP을 열거나 그 안의 엔트리를 읽을 수 없음(손상/유효하지 않음)
#   no_consolidated    require_consolidated=True인데 연결감사보고서 엔트리가 없음
#   error              그 밖의 모든 경우(기본값) - 절대 skip으로 취급되지 않는다
AuditDocsErrorKind = Literal[
    "validation",
    "ambiguous_corp",
    "corp_not_found",
    "rcept_not_found",
    "download_error",
    "corrupt_zip",
    "no_consolidated",
    "error",
]


@dataclass(frozen=True, slots=True)
class AuditDocsError:
    """입력 검증 실패, corp_name 해석 실패, 접수번호 미해결, 다운로드 오류,
    손상된 ZIP, 또는 `require_consolidated=True`인데 연결감사보고서가 없는
    경우. 이 경우 출력 디렉토리는 전혀 만들지 않는다(부분 파일 없음).

    `message`는 사람이 읽을 한국어 문구(단일 회사 도구의 사용자 노출 문구,
    변경하지 않음)이고, `kind`는 그 실패를 분류하는 안정적인 판별자다."""

    message: str
    kind: AuditDocsErrorKind = "error"


@dataclass(frozen=True, slots=True)
class WrittenAuditDocument:
    """실제로 디스크에 쓴 감사/연결감사 XML 엔트리 하나."""

    filename: str
    document_name: str
    acode: str
    size: int
    path: str


@dataclass(frozen=True, slots=True)
class AuditDocsOutcome:
    """`extract_audit_documents_core`가 성공적으로 만든 결과."""

    rcept_no: str
    corp_code: str
    output_dir: str
    manifest_path: str
    written: list[WrittenAuditDocument] = field(default_factory=list)
    audit_found: bool = False
    consolidated_found: bool = False
    message: str = ""


async def _resolve_target_rcept_no(
    *,
    rcept_no: str,
    corp_code: str,
    corp_name: str,
    bsns_year: str,
    reprt_code: str,
) -> tuple[str, str] | AuditDocsError:
    """(rcept_no, corp_code) 튜플 또는 `AuditDocsError`를 반환한다.

    `corp_code`는 `rcept_no`를 직접 지정한 경로에서는 알 수 없으므로 빈
    문자열일 수 있다."""
    rcept_no = (rcept_no or "").strip()
    corp_code = (corp_code or "").strip()
    corp_name = (corp_name or "").strip()
    bsns_year = (bsns_year or "").strip()

    if rcept_no:
        return rcept_no, corp_code

    if not corp_code:
        if not corp_name:
            return AuditDocsError(
                message="오류: rcept_no, 또는 (corp_code나 corp_name)과 bsns_year를 입력해주세요.",
                kind="validation",
            )

        resolution = await resolve_single_corp_code(corp_name)

        if isinstance(resolution, str):
            corp_code = resolution
        elif isinstance(resolution, (CorpValidationError, CorpLoadError)):
            return AuditDocsError(message=resolution.message, kind="validation")
        elif isinstance(resolution, CorpNameNotFound):
            return AuditDocsError(
                message=f"오류: 검색 결과가 없습니다.\n검색어: {resolution.corp_name}",
                kind="corp_not_found",
            )
        else:
            assert isinstance(resolution, CorpNameAmbiguous)
            lines = [
                f'오류: 회사명 "{resolution.corp_name}"에 해당하는 회사가 여러 건입니다. '
                "corp_code를 지정해 다시 시도해주세요."
            ]
            for candidate in resolution.candidates:
                lines.append(f"  - {candidate.corp_name} (corp_code={candidate.corp_code})")
            return AuditDocsError(message="\n".join(lines), kind="ambiguous_corp")

    if not bsns_year:
        return AuditDocsError(message="오류: 사업연도(bsns_year)를 입력해주세요.", kind="validation")

    resolved = await _resolve_rcept_no(corp_code, bsns_year, reprt_code)
    if isinstance(resolved, str) and resolved.startswith("오류"):
        return AuditDocsError(message=resolved, kind="download_error")
    if not resolved:
        return AuditDocsError(
            message=(
                "오류: 해당 보고서의 접수번호를 찾지 못했습니다 "
                f"(corp_code={corp_code}, bsns_year={bsns_year}, reprt_code={reprt_code})."
            ),
            kind="rcept_not_found",
        )
    return resolved, corp_code


def _write_entry_to_dir(data: bytes, entry: DocumentEntry, target_dir: str) -> WrittenAuditDocument | AuditDocsError:
    entry_bytes = read_entry(data, entry.filename)
    if isinstance(entry_bytes, DocumentZipError):
        return AuditDocsError(message=entry_bytes.message, kind="corrupt_zip")

    os.makedirs(target_dir, exist_ok=True)
    # 실제 DART ZIP 엔트리 이름에는 경로 구분자가 없지만, 방어적으로 basename만 사용한다.
    safe_name = os.path.basename(entry.filename)
    path = os.path.join(target_dir, safe_name)
    with open(path, "wb") as f:
        f.write(entry_bytes)

    return WrittenAuditDocument(
        filename=entry.filename,
        document_name=entry.document_name,
        acode=entry.acode,
        size=entry.size,
        path=path,
    )


def _entry_summary(entry: DocumentEntry | None, *, requested: bool) -> dict[str, object]:
    if not requested:
        return {"requested": False, "found": False}
    if entry is None:
        return {"requested": True, "found": False}
    return {
        "requested": True,
        "found": True,
        "filename": entry.filename,
        "document_name": entry.document_name,
        "acode": entry.acode,
        "size": entry.size,
    }


async def extract_audit_documents_core(
    *,
    rcept_no: str = "",
    corp_code: str = "",
    corp_name: str = "",
    bsns_year: str = "",
    reprt_code: str = "11011",
    output_dir: str,
    include: str = "both",
    require_consolidated: bool = False,
) -> AuditDocsOutcome | AuditDocsError:
    """필링의 문서 ZIP을 내려받아 감사/연결감사 XML을 `output_dir/<rcept_no>/`에
    추출하고, 같은 디렉토리에 `manifest.json`을 쓴다.

    실패(입력 검증, corp_name 해석 실패, 접수번호 미해결, 다운로드 오류, 손상된
    ZIP, `require_consolidated=True`인데 연결감사보고서가 없음) 시 대상
    디렉토리는 전혀 만들지 않는다 — 부분 파일이 남지 않는다.
    """
    include = (include or "").strip().lower()
    if include not in _VALID_INCLUDES:
        return AuditDocsError(
            message=f'오류: include는 "audit", "consolidated", "both" 중 하나여야 합니다: {include!r}',
            kind="validation",
        )

    if not output_dir or not output_dir.strip():
        return AuditDocsError(message="오류: 출력 디렉토리(output_dir)를 입력해주세요.", kind="validation")

    resolved = await _resolve_target_rcept_no(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name=corp_name,
        bsns_year=bsns_year,
        reprt_code=reprt_code,
    )
    if isinstance(resolved, AuditDocsError):
        return resolved
    target_rcept_no, resolved_corp_code = resolved

    data = await _fetch_dart_binary("document.xml", {"rcept_no": target_rcept_no})
    if isinstance(data, str):
        return AuditDocsError(message=data, kind="download_error")

    contents = inspect_document_zip(data)
    if isinstance(contents, DocumentZipError):
        return AuditDocsError(message=contents.message, kind="corrupt_zip")

    want_audit = include in ("audit", "both")
    # require_consolidated는 필링 자체에 연결감사보고서가 실재해야 한다는 전제조건이다.
    # 이 전제조건을 강제할 때는 include가 "audit"만 요청했더라도 연결감사보고서를
    # 실제 ZIP 내용과 대조해 확인하고 함께 추출한다.
    want_consolidated = include in ("consolidated", "both") or require_consolidated

    audit_entry = contents.audit_entries[0] if want_audit and contents.audit_entries else None
    consolidated_entry = (
        contents.consolidated_audit_entries[0] if want_consolidated and contents.consolidated_audit_entries else None
    )

    if require_consolidated and not contents.consolidated_audit_entries:
        return AuditDocsError(
            message=f"오류: 연결감사보고서를 찾을 수 없습니다 (rcept_no={target_rcept_no}).",
            kind="no_consolidated",
        )

    target_dir = os.path.join(output_dir, target_rcept_no)
    written: list[WrittenAuditDocument] = []

    for entry in (audit_entry, consolidated_entry):
        if entry is None:
            continue
        result = _write_entry_to_dir(data, entry, target_dir)
        if isinstance(result, AuditDocsError):
            return result
        written.append(result)

    os.makedirs(target_dir, exist_ok=True)
    manifest = {
        "rcept_no": target_rcept_no,
        "corp_code": resolved_corp_code,
        "include": include,
        "audit": _entry_summary(audit_entry, requested=want_audit),
        "consolidated_audit": _entry_summary(consolidated_entry, requested=want_consolidated),
        "files": [
            {
                "filename": w.filename,
                "document_name": w.document_name,
                "acode": w.acode,
                "size": w.size,
                "path": w.path,
            }
            for w in written
        ],
    }
    manifest_path = os.path.join(target_dir, _MANIFEST_FILENAME)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    lines = [f"감사서류 추출 완료 (rcept_no={target_rcept_no}): {len(written)}개 파일 -> {target_dir}"]
    if want_audit:
        lines.append(f"  - 감사보고서: {'찾음' if audit_entry is not None else '없음'}")
    if want_consolidated:
        lines.append(f"  - 연결감사보고서: {'찾음' if consolidated_entry is not None else '없음'}")
    lines.append(f"  - 매니페스트: {manifest_path}")

    return AuditDocsOutcome(
        rcept_no=target_rcept_no,
        corp_code=resolved_corp_code,
        output_dir=target_dir,
        manifest_path=manifest_path,
        written=written,
        audit_found=audit_entry is not None,
        consolidated_found=consolidated_entry is not None,
        message="\n".join(lines),
    )


@mcp.tool()
async def extract_audit_documents(
    output_dir: str,
    rcept_no: str = "",
    corp_code: str = "",
    corp_name: str = "",
    bsns_year: str = "",
    reprt_code: str = "11011",
    include: str = "both",
    require_consolidated: bool = False,
) -> str:
    """
    공시서류 원본 ZIP에서 감사보고서/연결감사보고서 XML을 추출해 저장합니다.

    접수번호(rcept_no)를 직접 지정하거나, corp_code 또는 corp_name과
    bsns_year로 자동 조회할 수 있습니다. corp_name은 정확히 하나의 회사로
    해석되어야 하며(모호하거나 매치가 없으면 OpenDART를 호출하지 않고 오류),
    OpenDART 파라미터로 직접 전달되지 않습니다.

    Parameters:
        output_dir: 저장 디렉토리 (실제 파일은 `output_dir/<rcept_no>/`에 저장됩니다)
        rcept_no: 접수번호 (14자리). 지정 시 다른 조회 파라미터 대신 직접 사용
        corp_code: DART 고유번호 (8자리). rcept_no 미지정 시 bsns_year와 함께 사용
        corp_name: 회사명. corp_code가 없을 때만 사용되며 정확히 하나의 회사로
            해석되어야 합니다
        bsns_year: 사업연도 (예: "2024"). rcept_no 미지정 시 필수
        reprt_code: 보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)
        include: 추출 대상 - "audit"(감사보고서), "consolidated"(연결감사보고서),
            "both"(둘 다, 기본값)
        require_consolidated: True면 연결감사보고서가 없을 때 오류로 처리하고
            아무 파일도 쓰지 않습니다. False(기본값)면 없음을 매니페스트에
            기록만 합니다

    Returns:
        추출 결과 메시지 (성공 시 저장 경로/파일 목록, 실패 시 "오류: ..." 메시지)
    """
    outcome = await extract_audit_documents_core(
        rcept_no=rcept_no,
        corp_code=corp_code,
        corp_name=corp_name,
        bsns_year=bsns_year,
        reprt_code=reprt_code,
        output_dir=output_dir,
        include=include,
        require_consolidated=require_consolidated,
    )
    return outcome.message
