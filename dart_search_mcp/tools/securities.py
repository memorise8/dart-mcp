from dart_search_mcp.app import mcp
from dart_search_mcp.client import _fetch_dart
from dart_search_mcp.formatting import _default_date_range, _format_amount, _format_date, _format_generic_response
from dart_search_mcp.registries import MAJOR_EVENT_REGISTRY, PERIODIC_REPORT_REGISTRY, SECURITIES_REGISTRATION_REGISTRY
from dart_search_mcp.types import QueryParams, object_records_from, records_from


@mcp.tool()
async def get_securities_report(
    corp_code: str,
    report_type: str,
    bgn_de: str = "",
    end_de: str = "",
) -> str:
    """
    증권신고서 주요정보를 유형별로 조회합니다.

    Parameters:
        corp_code: DART 고유번호 (8자리)
        report_type: 신고서 유형
            "지분증권" - 지분증권
            "채무증권" - 채무증권
            "증권예탁증권" - 증권예탁증권
            "합병" - 합병
            "주식의포괄적교환이전" - 주식의 포괄적 교환·이전
            "분할" - 분할
        bgn_de: 검색 시작일 YYYYMMDD (기본값: 빈문자열)
        end_de: 검색 종료일 YYYYMMDD (기본값: 빈문자열)

    Returns:
        증권신고서 주요정보
    """
    if not corp_code or not corp_code.strip():
        return "오류: 고유번호(corp_code)를 입력해주세요."

    if not report_type or not report_type.strip():
        available = "\n".join(f"  - {k}" for k in SECURITIES_REGISTRATION_REGISTRY)
        return f"오류: report_type을 입력해주세요.\n\n사용 가능한 report_type:\n{available}"

    report_type = report_type.strip()
    if report_type not in SECURITIES_REGISTRATION_REGISTRY:
        available = "\n".join(f"  - {k}" for k in SECURITIES_REGISTRATION_REGISTRY)
        return f"오류: 올바른 신고서 유형을 입력해주세요.\n사용 가능한 유형: {available}"

    endpoint = SECURITIES_REGISTRATION_REGISTRY[report_type]
    # bgn_de/end_de는 DART 필수값 → 생략 시 기본 범위 적용
    bgn_de, end_de = _default_date_range(bgn_de, end_de)
    params: QueryParams = {
        "corp_code": corp_code.strip(),
        "bgn_de": bgn_de,
        "end_de": end_de,
    }

    data = await _fetch_dart(f"{endpoint}.json", params)
    if isinstance(data, str):
        return data

    try:
        # group과 list 두 가지 응답 형식 처리
        groups = object_records_from(data.get("group"))
        items = records_from(data.get("list", []))

        if groups:
            # group 형식: 각 그룹별로 제목과 항목 포맷팅
            lines = [
                "=" * 60,
                f"증권신고서 - {report_type}",
                f"고유번호: {corp_code}",
                "=" * 60,
            ]

            for grp in groups:
                grp_title = str(grp.get("title") or "")
                grp_items = records_from(grp.get("list", []))

                if grp_title:
                    lines.append(f"\n{'─' * 40}")
                    lines.append(f"  {grp_title}")
                    lines.append(f"{'─' * 40}")

                for idx, item in enumerate(grp_items):
                    if len(grp_items) > 1:
                        lines.append(f"\n  --- [{idx + 1}] ---")
                    for key, str_val in item.items():
                        if key in ("status", "message", "crtfc_key"):
                            continue
                        if not str_val:
                            str_val = "-"
                        if (key.endswith("_de") or key.endswith("_dt")) and len(str_val) == 8:
                            str_val = _format_date(str_val)
                        if any(kw in key for kw in ("amount", "amt", "_cnt", "_qy", "stkqy", "stkrt")):
                            str_val = _format_amount(str_val)
                        lines.append(f"    {key}: {str_val}")

            lines.append("\n" + "=" * 60)
            return "\n".join(lines)

        # list 형식: 기존 범용 포맷터 사용
        date_range = ""
        if bgn_de or end_de:
            date_range = f" ({_format_date(bgn_de) if bgn_de else '~'} ~ {_format_date(end_de) if end_de else '~'})"

        title = f"증권신고서 - {report_type}{date_range}\n고유번호: {corp_code}"

        return _format_generic_response(title, items)

    except Exception as e:
        return f"오류: 응답 파싱 중 오류가 발생했습니다. {str(e)}"
