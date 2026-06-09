import datetime

from dart_search_mcp.types import records_from

def _format_date(date_str: str) -> str:
    """YYYYMMDD 형식의 날짜를 YYYY-MM-DD로 변환합니다."""
    if date_str and len(date_str) == 8 and date_str.isdigit():
        return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    return date_str

def _default_date_range(bgn_de: str, end_de: str) -> tuple[str, str]:
    """
    bgn_de/end_de 미지정 시 기본 범위(최근 약 10년 ~ 오늘)를 채웁니다.
    주요사항보고서·증권신고서 API는 bgn_de/end_de가 필수이므로 생략 시 기본값을 적용.
    """
    today = datetime.date.today()
    if not end_de:
        end_de = today.strftime("%Y%m%d")
    if not bgn_de:
        bgn_de = f"{today.year - 10}0101"
    return bgn_de, end_de

def _format_amount(val: str) -> str:
    """숫자 문자열에 천 단위 콤마를 추가합니다."""
    if not val or val == "-":
        return "-"
    # 공백 제거
    cleaned = val.strip().replace(",", "")
    if not cleaned:
        return "-"
    try:
        return f"{int(cleaned):,}"
    except ValueError:
        try:
            return f"{float(cleaned):,.2f}"
        except ValueError:
            return val

def _format_generic_response(
    title: str,
    data: object,
    key_descriptions: dict[str, str] | None = None,
) -> str:
    """
    DART API 응답을 범용적으로 포맷팅합니다.
    정기보고서, 주요사항보고서 등 다양한 응답 구조에 대응합니다.

    Args:
        title: 출력 제목
        data: API 응답의 list 또는 단일 dict
        key_descriptions: 키에 대한 한글 설명 매핑 (선택)
    """
    lines = [
        "=" * 60,
        title,
        "=" * 60,
    ]

    items = records_from(data)

    if not items:
        lines.append("데이터가 없습니다.")
        lines.append("=" * 60)
        return "\n".join(lines)

    descs = key_descriptions or {}

    for idx, item in enumerate(items):
        if len(items) > 1:
            lines.append(f"\n--- [{idx + 1}] ---")

        for key, str_val in item.items():
            # status, message 등 메타 필드 제외
            if key in ("status", "message", "crtfc_key"):
                continue
            label = descs.get(key, key)
            if not str_val:
                str_val = "-"

            # 날짜 필드 자동 포맷
            if (key.endswith("_de") or key.endswith("_dt") or key == "est_dt") and len(str_val) == 8:
                str_val = _format_date(str_val)

            # 금액 필드 자동 포맷 (amount, cnt, qy 등 숫자성 필드)
            if any(kw in key for kw in ("amount", "amt", "_cnt", "_qy", "stkqy", "stkrt", "lmp_cnt", "lmp_rate")):
                str_val = _format_amount(str_val)

            lines.append(f"  {label}: {str_val}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)
