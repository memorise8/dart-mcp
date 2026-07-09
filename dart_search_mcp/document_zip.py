"""DART 공시서류 원본 ZIP의 XML 항목을 분류하는 순수 모듈.

`download_document`가 내려받는 `document.xml` ZIP은 필링 유형에 따라 구조가
다르다:

- 감사보고서/연결감사보고서 단독(F) 필링: 엔트리 하나(`<rcept_no>_00760.xml`
  또는 `_00761.xml`).
- 사업보고서(A) 필링: 본문 엔트리(`<rcept_no>.xml`)만 있을 수도 있고, 별도의
  `_00760.xml`/`_00761.xml` 감사보고서 엔트리가 함께 포함될 수도 있다. 별도
  엔트리가 없으면 감사보고서 내용은 본문 XML에 인라인으로 포함되어 있으며,
  이 모듈은 그 인라인 내용을 추출하지 않는다 (범위 밖: 별도 엔트리가 없으면
  `audit_entries`는 정상적인 "없음" 상태다).

이 모듈은 ZIP을 열어 각 XML 엔트리의 헤더에서 `DOCUMENT-NAME`/`ACODE`를 읽어
분류만 한다. 파일 시스템에 쓰지 않는다 (추출/저장은 이 계획의 다음 단계).
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO

# DOCUMENT-NAME 텍스트/ACODE는 XML 파일 맨 앞부분에 있으므로, 큰 첨부(수 MB)를
# 전부 읽지 않고 헤더만 읽는다.
_HEAD_READ_BYTES = 8192

# ACODE 속성 순서/공백은 실제로 안정적이지만, 속성 순서가 바뀌거나 다른
# 속성이 끼어드는 경우까지 대비해 두 가지 패턴을 모두 시도한다.
_DOCUMENT_NAME_WITH_ACODE_RE = re.compile(
    r'<DOCUMENT-NAME[^>]*\bACODE="(?P<acode>\d+)"[^>]*>(?P<name>[^<]*)</DOCUMENT-NAME>'
)
_DOCUMENT_NAME_ANY_RE = re.compile(r"<DOCUMENT-NAME[^>]*>(?P<name>[^<]*)</DOCUMENT-NAME>")

_AUDIT_NAME = "감사보고서"
_CONSOLIDATED_AUDIT_NAME = "연결감사보고서"
_AUDIT_ACODE = "00760"
_CONSOLIDATED_AUDIT_ACODE = "00761"
_MAIN_REPORT_ACODES = frozenset({"11011", "11012", "11013", "11014"})


@dataclass(frozen=True, slots=True)
class DocumentEntry:
    """DART 문서 ZIP 안의 XML 엔트리 하나에 대한 분류 결과."""

    filename: str
    document_name: str
    acode: str
    size: int
    is_main_report: bool
    is_audit: bool
    is_consolidated_audit: bool


@dataclass(frozen=True, slots=True)
class DocumentZipContents:
    """ZIP 안의 모든 XML 엔트리에 대한 분류 결과."""

    entries: list[DocumentEntry] = field(default_factory=list)

    @property
    def audit_entries(self) -> list[DocumentEntry]:
        return [e for e in self.entries if e.is_audit]

    @property
    def consolidated_audit_entries(self) -> list[DocumentEntry]:
        return [e for e in self.entries if e.is_consolidated_audit]

    @property
    def main_report_entry(self) -> DocumentEntry | None:
        for entry in self.entries:
            if entry.is_main_report:
                return entry
        return None


@dataclass(frozen=True, slots=True)
class DocumentZipError:
    """ZIP을 열거나 읽을 수 없을 때 반환하는 타입이 있는 오류 (원시 traceback 대신)."""

    message: str


type DocumentZipResult = DocumentZipContents | DocumentZipError


def _classify_name_and_acode(document_name: str, acode: str) -> tuple[bool, bool]:
    """(is_audit, is_consolidated_audit)를 반환한다.

    연결감사보고서를 먼저 확인해 감사보고서와 상호 배타적으로 유지한다.
    """
    is_consolidated_audit = document_name == _CONSOLIDATED_AUDIT_NAME or acode == _CONSOLIDATED_AUDIT_ACODE
    if is_consolidated_audit:
        return False, True

    is_audit = document_name == _AUDIT_NAME or acode == _AUDIT_ACODE
    return is_audit, False


def _is_main_report(filename: str, document_name: str, acode: str) -> bool:
    if acode in _MAIN_REPORT_ACODES:
        return True
    # 본문 엔트리는 접미사(_00760 등) 없이 `<rcept_no>.xml` 형태이다.
    stem = filename.rsplit("/", 1)[-1]
    if stem.endswith(".xml") and "_" not in stem:
        return True
    return bool(document_name) and document_name.endswith("보고서") and acode == ""


def _extract_document_name_and_acode(head: bytes) -> tuple[str, str]:
    text = head.decode("utf-8", errors="replace")

    match = _DOCUMENT_NAME_WITH_ACODE_RE.search(text)
    if match:
        return match.group("name"), match.group("acode")

    match = _DOCUMENT_NAME_ANY_RE.search(text)
    if match:
        return match.group("name"), ""

    return "", ""


def _classify_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> DocumentEntry:
    with zf.open(info) as fh:
        head = fh.read(_HEAD_READ_BYTES)

    document_name, acode = _extract_document_name_and_acode(head)
    is_audit, is_consolidated_audit = _classify_name_and_acode(document_name, acode)
    is_main_report = not (is_audit or is_consolidated_audit) and _is_main_report(
        info.filename, document_name, acode
    )

    return DocumentEntry(
        filename=info.filename,
        document_name=document_name,
        acode=acode,
        size=info.file_size,
        is_main_report=is_main_report,
        is_audit=is_audit,
        is_consolidated_audit=is_consolidated_audit,
    )


def inspect_document_zip(data: bytes | str) -> DocumentZipResult:
    """DART 문서 ZIP(바이트 또는 경로)을 열어 모든 XML 엔트리를 분류한다.

    손상된 ZIP, 빈/유효하지 않은 ZIP, 그 밖의 읽기 오류는 원시 예외를 올리지
    않고 `DocumentZipError`로 반환한다 (부분 상태 없음).
    """
    source: BytesIO | str
    if isinstance(data, bytes):
        source = BytesIO(data)
    else:
        source = data

    try:
        with zipfile.ZipFile(source) as zf:
            infos = [info for info in zf.infolist() if not info.is_dir() and info.filename.endswith(".xml")]
            entries = [_classify_entry(zf, info) for info in infos]
    except (zipfile.BadZipFile, OSError) as exc:
        return DocumentZipError(message=f"유효하지 않거나 손상된 문서 ZIP입니다: {exc}")

    return DocumentZipContents(entries=entries)


def read_entry(data: bytes | str, filename: str) -> bytes | DocumentZipError:
    """ZIP 안의 특정 엔트리 내용을 바이트로 반환한다 (파일로 쓰지 않음)."""
    source: BytesIO | str
    if isinstance(data, bytes):
        source = BytesIO(data)
    else:
        source = data

    try:
        with zipfile.ZipFile(source) as zf:
            return zf.read(filename)
    except (zipfile.BadZipFile, OSError, KeyError) as exc:
        return DocumentZipError(message=f"ZIP 엔트리를 읽을 수 없습니다 ({filename}): {exc}")
