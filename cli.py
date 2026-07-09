"""
DART 전자공시 검색 CLI

한국 금융감독원 전자공시시스템(DART) Open API를 통해
기업 공시 정보, 재무제표, 지분공시, 정기보고서, 주요사항보고서 등을
커맨드라인에서 검색할 수 있는 CLI 도구입니다.
"""

import asyncio
import importlib.metadata
import os
import sys

import click
from dotenv import load_dotenv

load_dotenv()

from server import (
    MAJOR_EVENT_REGISTRY,
    PERIODIC_REPORT_REGISTRY,
    SECURITIES_REGISTRATION_REGISTRY,
    TemisExportError,
    download_document,
    download_xbrl,
    export_temis_topic_cases_core,
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


@cli.command()
def serve():
    """MCP 서버를 시작합니다."""
    mcp.run()


if __name__ == "__main__":
    cli()
