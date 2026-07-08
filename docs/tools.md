# MCP Tools

이 문서는 `scripts/generate_docs.py`로 생성됩니다.

## 도구 목록

- `search_disclosures`: DART 전자공시시스템에서 공시 목록을 검색합니다.
- `get_company_info`: DART 고유번호로 기업 개황 정보를 조회합니다.
- `search_corp_code`: 회사명으로 DART 고유번호(corp_code)를 검색합니다.
- `get_financial_statements`: DART에서 단일회사의 주요계정 재무제표를 조회합니다.
- `get_financial_statements_full`: DART에서 단일회사의 전체 재무제표를 조회합니다.
- `get_multi_company_financials`: DART에서 여러 회사의 주요계정 재무제표를 한 번에 조회합니다.
- `get_financial_indicators`: DART에서 단일회사의 주요 재무지표를 조회합니다.
- `get_multi_company_indicators`: DART에서 여러 회사의 주요 재무지표를 한 번에 조회합니다.
- `get_major_shareholders_report`: DART에서 대량보유 상황보고 정보를 조회합니다.
- `get_executive_stock_report`: DART에서 임원 및 주요주주의 주식 소유보고 정보를 조회합니다.
- `get_periodic_report`: DART 정기보고서의 주요정보를 유형별로 조회합니다.
- `get_major_event_report`: DART 주요사항보고서를 이벤트 유형별로 조회합니다.
- `download_document`: 공시서류 원본파일을 다운로드합니다.
- `download_xbrl`: XBRL 재무제표 원본파일(DSD)을 다운로드합니다.
- `get_xbrl_taxonomy`: XBRL 택사노미 재무제표 양식(표준계정과목체계)을 조회합니다.
- `get_securities_report`: 증권신고서 주요정보를 유형별로 조회합니다.
- `export_temis_topic_cases`: 감사보고서 사실(회계감사인)을 TEMIS(finov2) `DartTopicCase` JSON 배열로

## 주요 사용 흐름

1. `search_corp_code`로 회사명을 DART 고유번호로 변환합니다.
2. `search_disclosures`로 기간, 공시유형, 접수번호를 확인합니다.
3. 재무 데이터는 `get_financial_statements`, `get_financial_statements_full`, `get_financial_indicators`를 사용합니다.
4. 원문 ZIP은 `download_document`, XBRL ZIP은 `download_xbrl`로 내려받습니다.

## XBRL

- `download_xbrl`은 `rcept_no`를 직접 받거나 `corp_code` + `bsns_year` + `reprt_code`로 접수번호를 자동 조회합니다.
- `get_xbrl_taxonomy`는 `BS1`, `BS2`, `BS3`, `IS1`, `IS2`, `IS3`, `IS4`, `CIS1`, `CIS2`, `CIS3`, `CIS4`, `DC1`, `DC2` 양식을 조회합니다.
