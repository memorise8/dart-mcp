"""
공시서류 원본파일(ZIP) 안전 텍스트 추출 헬퍼.

`download_document`/`download_xbrl`이 받아오는 OpenDART ZIP 바이트를 메모리에서만
다룬다. 이 모듈은 어떤 파일도 디스크에 쓰지 않는다 — 호출자가 지정한 출력 경로나
증적(evidence) 경로 밖으로 ZIP을 영속화하지 않는다는 계획 제약을 지키기 위함이다.

동작:
  1. ZIP 바이트를 열어 포함된 파일 목록을 나열한다.
  2. 각 파일을 안전하게 디코딩한다(UTF-8 → CP949 → EUC-KR 순 시도, 모두 실패하면
     손실 허용 폴백으로 깨지지 않게 처리).
  3. 표준 라이브러리(`html.parser`)만으로 태그를 제거해 평문을 얻는다.
  4. 주어진 토픽 키워드가 나타나는 위치 주변의 짧고 유한한 스니펫을 반환한다.

잘못된 입력(빈 바이트열, 손상된 ZIP, 빈 ZIP)에 대해서는 예외를 던지는 대신
`DocumentExtractionError`를 반환한다.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from io import BytesIO

from dart_search_mcp.redact import redact

# OpenDART 공시서류는 실무적으로 EUC-KR/CP949 계열로 인코딩된 경우가 많다.
# UTF-8을 먼저 시도해 실패하면(대부분의 실제 한글 바이트열은 UTF-8로 디코딩되지
# 않고 예외가 발생한다) 순서대로 폴백한다. 마지막 latin-1은 항상 성공하므로
# 사실상 손실 허용(loss-tolerant) 최후 수단이다.
_CANDIDATE_ENCODINGS: tuple[str, ...] = ("utf-8", "cp949", "euc-kr")

_TAG_PATTERN = re.compile(r"<[^>]+>")


@dataclass(frozen=True, slots=True)
class ExtractedSnippet:
    """추출된 스니펫 하나. 어떤 파일의, 어떤 키워드 주변에서 나왔는지 함께 담는다."""

    filename: str
    keyword: str
    snippet: str


@dataclass(frozen=True, slots=True)
class DocumentExtractionResult:
    """ZIP 추출 성공 결과. 포함 파일 목록과(키워드가 주어졌다면) 매칭된 스니펫들."""

    file_names: list[str]
    snippets: list[ExtractedSnippet]


@dataclass(frozen=True, slots=True)
class DocumentExtractionError:
    """ZIP 추출 실패. 잘못된 ZIP, 빈 ZIP, 예기치 못한 오류 등을 크래시 대신 담는다."""

    message: str


type DocumentExtractionOutcome = DocumentExtractionResult | DocumentExtractionError


class _TagStrippingParser(HTMLParser):
    """표준 라이브러리 `html.parser`만으로 태그를 제거하는 보수적인 파서.

    XML/HTML 마크업 모두 `html.parser`로 관대하게(malformed 허용) 처리 가능하며,
    텍스트 노드만 모아 공백을 정규화해 반환한다.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join(" ".join(self._chunks).split())


def _decode_bytes(raw: bytes) -> str:
    """바이트열을 후보 인코딩 순서대로 시도해 안전하게 디코딩한다.

    모든 후보가 실패하면 UTF-8 손실 허용(replace) 디코딩으로 절대 예외를
    던지지 않는다.
    """
    for encoding in _CANDIDATE_ENCODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def _strip_tags(text: str) -> str:
    """XML/HTML성 텍스트에서 태그를 제거하고 평문만 남긴다.

    `html.parser` 기반 처리를 우선 시도하고, 어떤 이유로든 텍스트를 전혀 뽑아내지
    못하면(예: 파서가 예외적으로 아무 데이터도 콜백하지 못하는 극단적인 입력)
    정규식 폴백으로 태그를 제거한다. 두 경로 모두 표준 라이브러리만 사용한다.
    """
    parser = _TagStrippingParser()
    try:
        parser.feed(text)
        parser.close()
        stripped = parser.get_text()
    except Exception:
        stripped = ""

    if stripped:
        return stripped
    return " ".join(_TAG_PATTERN.sub(" ", text).split())


def _bounded_snippet(text: str, index: int, keyword_len: int, radius: int) -> str:
    start = max(0, index - radius)
    end = min(len(text), index + keyword_len + radius)
    return text[start:end].strip()


def extract_snippets(
    zip_bytes: bytes,
    keywords: list[str],
    *,
    snippet_radius: int = 80,
    max_snippets_per_keyword: int = 3,
) -> DocumentExtractionOutcome:
    """OpenDART 공시서류 ZIP 바이트에서 키워드 주변 스니펫을 추출한다.

    입력 ZIP은 메모리(`io.BytesIO`)에서만 열리며, 이 함수는 어떤 파일도 디스크에
    쓰지 않는다. 잘못된 입력(빈 바이트열, 손상된 ZIP, 내용물이 없는 ZIP)은 예외
    대신 `DocumentExtractionError`로 반환한다.

    Parameters:
        zip_bytes: OpenDART document/XBRL ZIP 응답 바이트열.
        keywords: 검색할 토픽 키워드 목록. 비어 있으면 파일 목록만 반환하고
            텍스트 스캔은 수행하지 않는다.
        snippet_radius: 키워드 앞뒤로 포함할 문자 수.
        max_snippets_per_keyword: 파일 하나당 키워드 하나에 대해 반환할 최대
            스니펫 개수(무한정 커지는 것을 막기 위한 상한).

    Returns:
        `DocumentExtractionResult`(성공) 또는 `DocumentExtractionError`(실패).
    """
    if not isinstance(zip_bytes, (bytes, bytearray)) or not zip_bytes:
        return DocumentExtractionError(message="오류: 빈 데이터이거나 올바른 ZIP 바이트가 아닙니다.")

    try:
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            file_names = archive.namelist()
            if not file_names:
                return DocumentExtractionError(message="오류: ZIP 안에 포함된 파일이 없습니다.")

            normalized_keywords = [k for k in (keywords or []) if k and k.strip()]
            snippets: list[ExtractedSnippet] = []

            if normalized_keywords:
                for name in file_names:
                    info = archive.getinfo(name)
                    if info.is_dir():
                        continue
                    try:
                        raw = archive.read(name)
                    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError):
                        continue

                    text = _strip_tags(_decode_bytes(raw))
                    if not text:
                        continue

                    for keyword in normalized_keywords:
                        found = 0
                        search_from = 0
                        while found < max_snippets_per_keyword:
                            idx = text.find(keyword, search_from)
                            if idx == -1:
                                break
                            snippet = _bounded_snippet(text, idx, len(keyword), snippet_radius)
                            snippets.append(ExtractedSnippet(filename=name, keyword=keyword, snippet=snippet))
                            found += 1
                            search_from = idx + len(keyword)

            return DocumentExtractionResult(file_names=file_names, snippets=snippets)
    except zipfile.BadZipFile:
        return DocumentExtractionError(message="오류: 올바른 ZIP 파일이 아닙니다.")
    except Exception as exc:  # 크래시 대신 항상 타입 오류로 변환
        return DocumentExtractionError(
            message=f"오류: 문서 추출 중 예기치 못한 오류가 발생했습니다. {redact(str(exc))}"
        )
