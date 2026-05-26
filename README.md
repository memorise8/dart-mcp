# dart-search-mcp

한국 금융감독원 전자공시시스템(DART) Open API를 통해 기업 공시 정보를 검색하는 MCP 서버입니다.

## 설정

### DART API 키 발급

1. [DART Open API](https://opendart.fss.or.kr/intro/main.do) 회원가입
2. API 키 신청 및 발급

### 환경 변수 설정

```bash
export DART_API_KEY="your_api_key_here"
```

### 의존성 설치

```bash
uv sync
```

### 서버 실행

```bash
uv run python server.py
```

## 제공 도구

### `search_disclosures` — 공시 검색

회사명, 기간, 공시유형 등으로 DART 공시 목록을 검색합니다.

```
search_disclosures(corp_name="삼성전자", bgn_de="20240101", end_de="20241231")
```

### `get_company_info` — 기업 개황

DART 고유번호로 기업 기본 정보를 조회합니다.

```
get_company_info(corp_code="00126380")
```

### `get_financial_statements` — 재무제표

특정 사업연도의 재무제표를 조회합니다.

```
get_financial_statements(corp_code="00126380", bsns_year="2024")
```

### `search_corp_code` — 고유번호 검색

회사명으로 DART 고유번호(corp_code)를 검색합니다.

```
search_corp_code(corp_name="삼성전자")
```

## Claude Desktop 설정 예시

```json
{
  "mcpServers": {
    "dart-search": {
      "command": "uv",
      "args": ["run", "python", "/path/to/dart-search-mcp/server.py"],
      "env": {
        "DART_API_KEY": "your_api_key_here"
      }
    }
  }
}
```
