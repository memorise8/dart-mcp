import os

import re

import zipfile



from dart_search_mcp.app import mcp

from dart_search_mcp.client import _fetch_dart, _fetch_dart_binary
from dart_search_mcp.types import records_from

@mcp.tool()
async def download_document(rcept_no: str, output_dir: str = ".") -> str:
    """
    공시서류 원본파일을 다운로드합니다.

    Parameters:
        rcept_no: 접수번호 (14자리, search_disclosures 결과에서 확인 가능)
        output_dir: 저장 디렉토리 (기본값: 현재 디렉토리)

    Returns:
        다운로드 결과 메시지 (파일 경로, 포함된 파일 목록)
    """
    if not rcept_no or not rcept_no.strip():
        return "오류: 접수번호(rcept_no)를 입력해주세요."

    data = await _fetch_dart_binary("document.xml", {"rcept_no": rcept_no.strip()})
    if isinstance(data, str):
        return data

    os.makedirs(output_dir, exist_ok=True)
    filepath = os.path.join(output_dir, f"{rcept_no.strip()}.zip")
    with open(filepath, "wb") as f:
        f.write(data)

    try:
        with zipfile.ZipFile(filepath) as z:
            file_list = z.namelist()
            lines = [
                "공시서류 원본파일 다운로드 완료",
                f"저장 경로: {os.path.abspath(filepath)}",
                f"파일 크기: {len(data):,} bytes",
                f"포함 파일 ({len(file_list)}개):",
            ]
            for fname in file_list:
                lines.append(f"  - {fname}")
            return "\n".join(lines)
    except Exception:
        return f"다운로드 완료: {os.path.abspath(filepath)} ({len(data):,} bytes)"

async def _resolve_rcept_no(corp_code: str, bsns_year: str, reprt_code: str) -> str:
    """
    고유번호+사업연도+보고서코드로 해당 정기보고서의 접수번호(rcept_no)를 조회합니다.
    fnlttXbrl.xml은 rcept_no를 요구하므로, 사용자 편의를 위해 list.json에서 역산합니다.

    보고서명의 기간표기 "(YYYY.MM)"를 파싱해 결산월과 무관하게 매칭합니다.
    - 사업보고서: 결산연도가 bsns_year인 보고서(12월 결산)를 우선하되, 없으면
      차년도 상반기 결산 보고서(비12월 결산, 예: 1월 결산 REIT의 "(YYYY+1.01)")도 포함.
    - 분기보고서: 회계연도 내 두 건(1분기/3분기)을 기간 순서로 구분(이른=1분기, 늦은=3분기).
    동일 기간의 정정본이 있으면 최신 접수본(정정본)을 선택합니다.
    성공 시 14자리 접수번호, 오류 시 "오류:..." 문자열, 미발견 시 "" 반환.
    """
    keyword_map = {
        "11011": "사업보고서",
        "11012": "반기보고서",
        "11013": "분기보고서",
        "11014": "분기보고서",
    }
    keyword = keyword_map.get(reprt_code, "보고서")
    try:
        by = int(bsns_year.strip())
    except (ValueError, AttributeError):
        return ""
    next_year = by + 1

    # 사업보고서는 결산 후(주로 다음 해) 제출 → 조회창을 차년도까지. 분기/반기는 당해 연도.
    if reprt_code == "11011":
        bgn, end = f"{by}0101", f"{next_year}1231"
    else:
        bgn, end = f"{by}0101", f"{by}1231"

    data = await _fetch_dart("list.json", {
        "corp_code": corp_code.strip(),
        "bgn_de": bgn,
        "end_de": end,
        "pblntf_ty": "A",
        "page_count": "100",
        "sort": "date",
        "sort_mth": "desc",
    })
    if isinstance(data, str):
        # "오류:..."는 그대로 전달, "조회된 데이터 없음"류는 미발견("")으로 처리
        return data if data.startswith("오류") else ""

    items = records_from(data.get("list", []))

    # (결산연도, 결산월, 접수일자, 접수번호) 후보 수집
    cands: list[tuple[int, int, str, str]] = []
    for item in items:
        nm = item.get("report_nm", "")
        if keyword not in nm:
            continue
        m = re.search(r"\((\d{4})[.\-/](\d{2})\)", nm)
        if not m:
            continue
        py, pm = int(m.group(1)), int(m.group(2))
        if reprt_code == "11011":
            in_scope = (py == by) or (py == next_year and pm <= 6)
        else:
            in_scope = (py == by)
        if in_scope:
            cands.append((py, pm, item.get("rcept_dt", ""), item.get("rcept_no", "")))

    if not cands:
        return ""

    if reprt_code in ("11011", "11012"):
        # 결산기간이 가장 최근 + 최신 접수(정정본 우선)
        cands.sort(key=lambda x: (x[0], x[1], x[2]))
        return cands[-1][3]

    # 분기: 기간(년,월)별로 정정본 정리 후 1분기=이른 기간 / 3분기=늦은 기간
    by_period: dict[tuple[int, int], tuple[str, str]] = {}
    for py, pm, dt, rno in cands:
        key = (py, pm)
        if key not in by_period or dt > by_period[key][0]:
            by_period[key] = (dt, rno)
    ordered = [by_period[k][1] for k in sorted(by_period)]
    if reprt_code == "11013":
        return ordered[0]
    return ordered[-1]

@mcp.tool()
async def download_xbrl(
    corp_code: str = "",
    bsns_year: str = "",
    reprt_code: str = "11011",
    output_dir: str = ".",
    rcept_no: str = "",
) -> str:
    """
    XBRL 재무제표 원본파일(DSD)을 다운로드합니다.

    DART의 fnlttXbrl.xml API는 접수번호(rcept_no)를 필요로 합니다.
    rcept_no를 직접 지정하거나, corp_code+bsns_year+reprt_code로 자동 조회할 수 있습니다.

    Parameters:
        corp_code: DART 고유번호 (8자리) — rcept_no 미지정 시 필수
        bsns_year: 사업연도 (예: "2024") — rcept_no 미지정 시 필수
        reprt_code: 보고서코드 (11013=1분기, 11012=반기, 11014=3분기, 11011=사업보고서)
        output_dir: 저장 디렉토리 (기본값: 현재 디렉토리)
        rcept_no: 접수번호 (14자리). 지정 시 corp_code/bsns_year 대신 직접 사용.

    Returns:
        다운로드 결과 메시지 (파일 경로, 포함된 파일 목록)
    """
    rcept_no = rcept_no.strip() if rcept_no else ""
    if not rcept_no:
        if not corp_code or not corp_code.strip() or not bsns_year or not bsns_year.strip():
            return "오류: rcept_no, 또는 corp_code와 bsns_year를 입력해주세요."
        resolved = await _resolve_rcept_no(corp_code, bsns_year, reprt_code)
        if isinstance(resolved, str) and resolved.startswith("오류"):
            return resolved
        if not resolved:
            return (
                f"오류: 해당 보고서의 접수번호를 찾지 못했습니다 "
                f"(corp_code={corp_code}, bsns_year={bsns_year}, reprt_code={reprt_code}).\n"
                f"search_disclosures로 접수번호를 확인한 뒤 rcept_no로 직접 지정하세요."
            )
        rcept_no = resolved

    params = {
        "rcept_no": rcept_no,
        "reprt_code": reprt_code,
    }

    data = await _fetch_dart_binary("fnlttXbrl.xml", params)
    if isinstance(data, str):
        return data

    os.makedirs(output_dir, exist_ok=True)
    filename = f"xbrl_{rcept_no}.zip"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "wb") as f:
        f.write(data)

    try:
        with zipfile.ZipFile(filepath) as z:
            file_list = z.namelist()
            lines = [
                "XBRL 재무제표 원본파일 다운로드 완료",
                f"저장 경로: {os.path.abspath(filepath)}",
                f"파일 크기: {len(data):,} bytes",
                f"포함 파일 ({len(file_list)}개):",
            ]
            for fname in file_list:
                lines.append(f"  - {fname}")
            return "\n".join(lines)
    except Exception:
        return f"다운로드 완료: {os.path.abspath(filepath)} ({len(data):,} bytes)"
