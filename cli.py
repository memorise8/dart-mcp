"""
DART 전자공시 검색 CLI

한국 금융감독원 전자공시시스템(DART) Open API를 통해
기업 공시 정보, 재무제표, 지분공시, 정기보고서, 주요사항보고서 등을
커맨드라인에서 검색할 수 있는 CLI 도구입니다.
"""

import asyncio
import importlib.metadata
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

from dart_search_mcp.collect import AUDIT_AND_BUSINESS_REPORT_TARGETS, collect_disclosures
from server import (
    MAJOR_EVENT_REGISTRY,
    PERIODIC_REPORT_REGISTRY,
    SECURITIES_REGISTRATION_REGISTRY,
    AuditDocsError,
    TemisExportError,
    download_document,
    download_xbrl,
    export_temis_topic_cases_core,
    extract_audit_documents_core,
    get_company_info,
    get_executive_stock_report,
    get_financial_indicators,
    get_financial_statements,
    get_financial_statements_full,
    get_major_event_report,
    get_major_shareholders_report,
    get_multi_company_financials,
    get_multi_company_indicators,
    get_periodic_report,
    get_securities_report,
    get_xbrl_taxonomy,
    mcp,
    search_corp_code,
    search_disclosures,
)


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """DART 전자공시 검색 CLI - 한국 금융감독원 전자공시시스템 Open API"""
    pass


@cli.command()
def diagnostics():
    """로컬 설정과 사용 가능한 명령/도구를 점검합니다."""
    api_key_configured = bool(os.environ.get("DART_API_KEY") or os.environ.get("dart_api"))
    try:
        package_version = importlib.metadata.version("dart-search-mcp")
    except importlib.metadata.PackageNotFoundError:
        package_version = "not installed"

    async def tool_count() -> int:
        tools = await mcp.list_tools()
        return len(tools)

    click.echo("DART search MCP diagnostics")
    click.echo(f"Python: {sys.version.split()[0]}")
    click.echo(f"Package: dart-search-mcp {package_version}")
    click.echo(f"DART API key: {'configured' if api_key_configured else 'missing'}")
    click.echo("DART base URL: https://opendart.fss.or.kr/api")
    click.echo(f"MCP tools: {asyncio.run(tool_count())}")
    click.echo(f"CLI commands: {len(cli.commands)}")


@cli.command()
@click.argument("corp_name")
def search(corp_name):
    """회사명으로 DART 고유번호를 검색합니다."""
    result = asyncio.run(search_corp_code(corp_name))
    click.echo(result)


@cli.command()
@click.argument("corp_code")
def company(corp_code):
    """DART 고유번호로 기업 개황 정보를 조회합니다."""
    result = asyncio.run(get_company_info(corp_code))
    click.echo(result)


@cli.command()
@click.option("--corp", "-c", default="", help="회사명")
@click.option("--code", default="", help="DART 고유번호")
@click.option("--from", "bgn_de", default="", help="검색 시작일 (YYYYMMDD)")
@click.option("--to", "end_de", default="", help="검색 종료일 (YYYYMMDD)")
@click.option("--type", "pblntf_ty", default="", help="공시유형 (A=정기, B=주요사항, C=발행, D=지분, E=기타, F=외부감사, G=펀드, H=자산유동화, I=거래소, J=공정위)")
@click.option("--page", default=1, help="페이지 번호")
@click.option("--count", default=20, help="페이지당 건수 (최대 100)")
def disclosures(corp, code, bgn_de, end_de, pblntf_ty, page, count):
    """DART 공시 목록을 검색합니다."""
    result = asyncio.run(search_disclosures(
        corp_name=corp, corp_code=code, bgn_de=bgn_de, end_de=end_de,
        pblntf_ty=pblntf_ty, page_no=page, page_count=count
    ))
    click.echo(result)


def _parse_collect_targets(raw: str) -> list[tuple[str, str]]:
    """`"F:감사보고서,A:사업보고서"` 형식의 문자열을 (공시유형, 키워드) 쌍
    목록으로 해석한다. 빈 문자열이면 기본 프리셋을 사용한다."""
    if not raw.strip():
        return list(AUDIT_AND_BUSINESS_REPORT_TARGETS)

    targets: list[tuple[str, str]] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise click.BadParameter(f'"{chunk}"는 PBLNTF_TY:키워드 형식이 아닙니다 (예: "F:감사보고서")')
        pblntf_ty, keyword = chunk.split(":", 1)
        pblntf_ty = pblntf_ty.strip()
        keyword = keyword.strip()
        if not pblntf_ty or not keyword:
            raise click.BadParameter(f'"{chunk}"는 PBLNTF_TY:키워드 형식이 아닙니다 (예: "F:감사보고서")')
        targets.append((pblntf_ty, keyword))
    return targets


@cli.command("collect-disclosures")
@click.option("--from", "bgn_de", required=True, help="검색 시작일 (YYYYMMDD)")
@click.option("--to", "end_de", required=True, help="검색 종료일 (YYYYMMDD)")
@click.option(
    "--targets",
    default="",
    help='공시유형:키워드 쌍을 쉼표로 구분 (예: "F:감사보고서,A:사업보고서"). '
    "기본값: 감사보고서(+연결감사보고서)+사업보고서 프리셋",
)
@click.option("-o", "--output", "output_path", required=True, help="수집 결과 매니페스트 JSON을 쓸 파일 경로")
@click.option("--exclude-corrections", is_flag=True, default=False, help="[기재정정]/[첨부추가] 정정 공시를 제외")
@click.option("--resume", is_flag=True, default=False, help="기존 체크포인트가 있으면 이어서 수집 (없으면 새로 시작)")
@click.option("--max-retries", default=4, show_default=True, help="페이지별 최대 재시도 횟수")
@click.option("--sleep", "pace_seconds", default=0.15, show_default=True, help="API 호출 사이 대기 시간(초)")
def collect_disclosures_cmd(bgn_de, end_de, targets, output_path, exclude_corrections, resume, max_retries, pace_seconds):
    """지정한 기간의 공시를 3개월 이하 창으로 나눠 전체 페이지를 순회하며
    대량으로 수집하고, 체크포인트 가능한 매니페스트 JSON으로 저장합니다.

    대량(bulk) 수집은 이 CLI 명령을 통해서만 제공합니다(장시간 실행되는 MCP
    도구는 두지 않습니다). 단일 회사 공시 조회는 계속 search_disclosures /
    search_disclosures_structured를 사용하세요.

    --resume 없이 실행하면 기존 체크포인트(OUTPUT과 같은 디렉터리의
    "<output>.checkpoint.json")를 버리고 새로 시작합니다.
    """
    parsed_targets = _parse_collect_targets(targets)
    checkpoint_path = Path(f"{output_path}.checkpoint.json")
    if not resume:
        checkpoint_path.unlink(missing_ok=True)

    manifest = asyncio.run(
        collect_disclosures(
            targets=parsed_targets,
            bgn_de=bgn_de,
            end_de=end_de,
            max_retries=max_retries,
            exclude_corrections=exclude_corrections,
            pace_seconds=pace_seconds,
            checkpoint=checkpoint_path,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
    )

    Path(output_path).write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    click.echo(
        f"수집 완료: {manifest.total_records}건 (분류별: {manifest.counts_by_category}), "
        f"실패 페이지 {len(manifest.failed_pages)}건 -> {output_path}"
    )


@cli.command()
@click.argument("corp_code")
@click.argument("year")
@click.option("--report", "reprt_code", default="11011", help="보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
def financial(corp_code, year, reprt_code):
    """단일회사 주요계정 재무제표를 조회합니다."""
    result = asyncio.run(get_financial_statements(corp_code, year, reprt_code))
    click.echo(result)


@cli.command("financial-full")
@click.argument("corp_code")
@click.argument("year")
@click.option("--report", "reprt_code", default="11011", help="보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
@click.option("--fs", "fs_div", default="CFS", help="재무제표 구분 (CFS=연결, OFS=개별)")
def financial_full(corp_code, year, reprt_code, fs_div):
    """단일회사 전체 재무제표를 조회합니다."""
    result = asyncio.run(get_financial_statements_full(corp_code, year, reprt_code, fs_div))
    click.echo(result)


@cli.command()
@click.argument("corp_code")
@click.argument("year")
@click.option("--report", "reprt_code", default="11011", help="보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
@click.option("--class", "idx_cl_code", default="", help="지표분류코드 (M210000=수익성, M220000=안정성, M230000=성장성, M240000=활동성)")
def indicators(corp_code, year, reprt_code, idx_cl_code):
    """단일회사 주요 재무지표를 조회합니다."""
    result = asyncio.run(get_financial_indicators(corp_code, year, reprt_code, idx_cl_code))
    click.echo(result)


@cli.command("multi-financial")
@click.argument("corp_codes")
@click.argument("year")
@click.option("--report", "reprt_code", default="11011", help="보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
def multi_financial(corp_codes, year, reprt_code):
    """여러 회사의 주요계정 재무제표를 한 번에 조회합니다.

    CORP_CODES: 쉼표로 구분된 DART 고유번호 (예: "00126380,00164779")
    """
    result = asyncio.run(get_multi_company_financials(corp_codes, year, reprt_code))
    click.echo(result)


@cli.command("multi-indicators")
@click.argument("corp_codes")
@click.argument("year")
@click.option("--report", "reprt_code", default="11011", help="보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
@click.option("--class", "idx_cl_code", default="", help="지표분류코드 (M210000=수익성, M220000=안정성, M230000=성장성, M240000=활동성)")
def multi_indicators(corp_codes, year, reprt_code, idx_cl_code):
    """여러 회사의 주요 재무지표를 한 번에 조회합니다.

    CORP_CODES: 쉼표로 구분된 DART 고유번호 (예: "00126380,00164779")
    """
    result = asyncio.run(get_multi_company_indicators(corp_codes, year, reprt_code, idx_cl_code))
    click.echo(result)


@cli.command()
@click.argument("corp_code")
def shareholders(corp_code):
    """대량보유 상황보고 정보를 조회합니다."""
    result = asyncio.run(get_major_shareholders_report(corp_code))
    click.echo(result)


@cli.command("executive-stock")
@click.argument("corp_code")
def executive_stock(corp_code):
    """임원·주요주주 소유보고 정보를 조회합니다."""
    result = asyncio.run(get_executive_stock_report(corp_code))
    click.echo(result)


@cli.command()
@click.argument("corp_code")
@click.argument("year")
@click.argument("report_type")
@click.option("--report", "reprt_code", default="11011", help="보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
def periodic(corp_code, year, report_type, reprt_code):
    """정기보고서 주요정보를 유형별로 조회합니다.

    사용 가능한 REPORT_TYPE:\n
    증자감자현황, 배당, 자기주식취득처분, 최대주주현황, 최대주주변동,
    소액주주, 임원현황, 직원현황, 이사감사개인별보수, 이사감사전체보수,
    개인별보수지급, 타법인출자, 채무증권발행, 기업어음미상환, 단기사채미상환,
    회사채미상환, 신종자본증권미상환, 조건부자본증권미상환, 회계감사인,
    감사용역체결, 비감사용역계약, 사외이사변동, 미등기임원보수,
    이사감사보수승인금액, 이사감사보수유형별, 공모자금사용, 사모자금사용
    """
    result = asyncio.run(get_periodic_report(corp_code, year, reprt_code=reprt_code, report_type=report_type))
    click.echo(result)


@cli.command()
@click.argument("corp_code")
@click.argument("event_type")
@click.option("--from", "bgn_de", default="", help="검색 시작일 (YYYYMMDD)")
@click.option("--to", "end_de", default="", help="검색 종료일 (YYYYMMDD)")
def event(corp_code, event_type, bgn_de, end_de):
    """주요사항보고서를 이벤트 유형별로 조회합니다.

    사용 가능한 EVENT_TYPE:\n
    자산양수도, 부도발생, 영업정지, 회생절차, 해산사유, 유상증자결정,
    무상증자결정, 유무상증자결정, 감자결정, 관리절차개시, 소송,
    해외상장결정, 해외상장폐지결정, 해외상장, 해외상장폐지,
    전환사채발행, 신주인수권부사채발행, 교환사채발행, 관리절차중단,
    상각형조건부자본증권발행, 자기주식취득결정, 자기주식처분결정,
    자기주식신탁체결, 자기주식신탁해지, 영업양수결정, 영업양도결정,
    유형자산양수, 유형자산양도, 타법인주식양수, 타법인주식양도,
    사채권양수, 사채권양도, 회사합병, 회사분할, 회사분할합병, 주식교환이전
    """
    result = asyncio.run(get_major_event_report(corp_code, event_type=event_type, bgn_de=bgn_de, end_de=end_de))
    click.echo(result)


@cli.command()
@click.argument("rcept_no")
@click.option("-o", "--output", default=".", help="저장 디렉토리")
def download(rcept_no, output):
    """공시서류 원본파일을 다운로드합니다."""
    result = asyncio.run(download_document(rcept_no, output))
    click.echo(result)


@cli.command("download-xbrl")
@click.argument("corp_code")
@click.argument("year")
@click.option("--report", "reprt_code", default="11011", help="보고서코드")
@click.option("-o", "--output", default=".", help="저장 디렉토리")
def download_xbrl_cmd(corp_code, year, reprt_code, output):
    """XBRL 재무제표 원본파일을 다운로드합니다."""
    result = asyncio.run(download_xbrl(corp_code, year, reprt_code, output))
    click.echo(result)


@cli.command()
@click.argument("sj_div", default="BS1")
def taxonomy(sj_div):
    """XBRL 택사노미 재무제표 양식을 조회합니다."""
    result = asyncio.run(get_xbrl_taxonomy(sj_div))
    click.echo(result)


@cli.command()
@click.argument("corp_code")
@click.argument("report_type")
@click.option("--from", "bgn_de", default="", help="시작일 YYYYMMDD")
@click.option("--to", "end_de", default="", help="종료일 YYYYMMDD")
def securities(corp_code, report_type, bgn_de, end_de):
    """증권신고서 주요정보를 조회합니다.

    사용 가능한 REPORT_TYPE:\n
    지분증권, 채무증권, 증권예탁증권, 합병, 주식의포괄적교환이전, 분할
    """
    result = asyncio.run(get_securities_report(corp_code, report_type, bgn_de, end_de))
    click.echo(result)


@cli.command("temis-topic-cases")
@click.argument("year")
@click.option("--corp", "-c", default="", help="회사명 (--code 없을 때 사용; 여러 회사와 매치되면 오류)")
@click.option("--code", default="", help="DART 고유번호 (--corp 대신 사용)")
@click.option("--report", "reprt_code", default="11011", help="보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
@click.option("--keywords", default="", help='기본 토픽 키워드 사전에 추가할 항목, "slug:용어,slug2:용어2" 형식 (선택)')
@click.option("-o", "--output", "output_path", required=True, help="TEMIS DartTopicCase JSON 배열을 쓸 출력 파일 경로")
def temis_topic_cases(year, corp, code, reprt_code, keywords, output_path):
    """감사보고서 사실(회계감사인)을 TEMIS(finov2) DartTopicCase JSON 배열로
    변환해 OUTPUT 경로에 씁니다 (opt-in 운영 adapter 경계 명령).

    finov2는 OpenDART를 직접 호출하지 않습니다. 이 명령의 산출물은 항상 회사
    1건 단위이며, finov2는 현재 DART_TOPIC_CASES_PATH로 이 파일을 직접
    읽습니다(파일 하나에 회사 1건). 여러 회사를 누적하는 DB import(case_id를
    upsert 키로 사용) 모드는 finov2 쪽에서 별도로 진행 중입니다.

    --code 또는 --corp 중 하나가 필요합니다. 둘 다 없거나, --corp가 여러
    회사와 매치되거나(모호) 해석되지 않으면(결과 없음) 오류 메시지를 출력하고
    0이 아닌 종료 코드를 반환하며, 출력 파일은 전혀 만들지 않습니다.

    경고: OUTPUT은 항상 덮어씁니다 (기존 파일에 append하지 않습니다). Task 6
    변환기가 보장하는 case_id 유일성은 "한 번의 변환" 범위에서만 유효하므로,
    매 실행마다 파일 전체를 새로 쓰는 것만이 그 유일성을 파일 단위로
    지키는 방법입니다.
    """
    outcome = asyncio.run(
        export_temis_topic_cases_core(
            bsns_year=year,
            output_path=output_path,
            corp_code=code,
            corp_name=corp,
            reprt_code=reprt_code,
            extra_keywords=keywords,
        )
    )

    if isinstance(outcome, TemisExportError):
        click.echo(outcome.message, err=True)
        raise SystemExit(1)

    click.echo(outcome.message)


@cli.command("audit-documents")
@click.option("--rcept-no", "rcept_no", default="", help="접수번호 (14자리). 지정 시 다른 조회 파라미터 대신 직접 사용")
@click.option("--code", "corp_code", default="", help="DART 고유번호 (rcept_no 미지정 시 --year와 함께 사용)")
@click.option("--corp", "corp_name", default="", help="회사명 (--code 대신 사용; 여러 회사와 매치되면 오류)")
@click.option("--year", "bsns_year", default="", help="사업연도 (rcept_no 미지정 시 필수)")
@click.option("--report", "reprt_code", default="11011", help="보고서코드 (11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기)")
@click.option("-o", "--output", "output_dir", required=True, help="저장 디렉토리 (실제 파일은 <output>/<rcept_no>/에 저장)")
@click.option("--include", default="both", help='추출 대상: "audit", "consolidated", "both" 중 하나 (기본값: both)')
@click.option("--require-consolidated", is_flag=True, default=False, help="연결감사보고서가 없으면 오류로 처리 (기본값: 없음을 매니페스트에만 기록)")
def audit_documents(rcept_no, corp_code, corp_name, bsns_year, reprt_code, output_dir, include, require_consolidated):
    """공시서류 원본 ZIP에서 감사보고서/연결감사보고서 XML을 추출합니다.

    접수번호(--rcept-no)를 직접 지정하거나, --code 또는 --corp를 --year와
    함께 지정해 자동 조회할 수 있습니다. --corp는 정확히 하나의 회사로
    해석되어야 하며(모호하면 오류), OpenDART에 직접 전달되지 않습니다.

    실패 시(입력 검증, 회사명 해석 실패, 접수번호 미해결, 다운로드 오류, 손상된
    ZIP, --require-consolidated인데 연결감사보고서가 없음) 0이 아닌 종료
    코드를 반환하며 출력 디렉토리는 전혀 만들지 않습니다(부분 파일 없음).
    """
    outcome = asyncio.run(
        extract_audit_documents_core(
            rcept_no=rcept_no,
            corp_code=corp_code,
            corp_name=corp_name,
            bsns_year=bsns_year,
            reprt_code=reprt_code,
            output_dir=output_dir,
            include=include,
            require_consolidated=require_consolidated,
        )
    )

    if isinstance(outcome, AuditDocsError):
        click.echo(outcome.message, err=True)
        raise SystemExit(1)

    click.echo(outcome.message)


@cli.command()
def serve():
    """MCP 서버를 시작합니다."""
    mcp.run()


if __name__ == "__main__":
    cli()
