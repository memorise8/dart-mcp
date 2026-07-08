# dart-search-mcp

한국 금융감독원 전자공시시스템(DART) Open API를 MCP 서버와 CLI로 사용할 수 있게 해주는 도구입니다.

공시 검색, 기업 개황, 재무제표, 재무지표, 지분공시, 정기보고서 주요정보, 주요사항보고서, 증권신고서, 원문 ZIP, XBRL ZIP 다운로드를 지원합니다.

## 빠른 시작

1. DART Open API 키를 발급합니다.
   - https://opendart.fss.or.kr/intro/main.do

2. 환경 변수를 설정합니다.

```bash
export DART_API_KEY="your_api_key_here"
```

`.env` 파일에 넣어서 관리해도 됩니다(`dotenv`를 자동으로 읽습니다). 다만 `.env`는
**절대 커밋하지 마세요** — 이 저장소의 `.gitignore`는 이미 `.env`, `.env.*`를
제외하고 있습니다. API 키 값은 이 저장소의 어떤 로그·예외·문서에도 평문으로
남지 않도록 항상 리댁션(`<redacted>`)됩니다(`dart_search_mcp/redact.py`).

3. 의존성을 설치합니다.

```bash
uv sync
```

4. 로컬 상태를 점검합니다.

```bash
uv run dart diagnostics
```

정상적으로 로드되면 Python 버전, 패키지 상태, API 키 설정 여부, MCP 도구 수, CLI 명령 수가 출력됩니다. API 키 값은 출력하지 않습니다.

## MCP 서버 실행

```bash
uv run dart serve
```

또는 호환 엔트리포인트로 직접 실행할 수 있습니다.

```bash
uv run python server.py
```

## MCP 클라이언트 설정 예시

Claude Desktop 등 MCP 클라이언트에는 이 저장소의 절대 경로를 넣어 연결합니다.

```json
{
  "mcpServers": {
    "dart-search": {
      "command": "uv",
      "args": ["run", "python", "/absolute/path/to/dart-search-mcp/server.py"],
      "env": {
        "DART_API_KEY": "your_api_key_here"
      }
    }
  }
}
```

## CLI 사용법

```bash
uv run dart search 삼성전자
uv run dart company 00126380
uv run dart disclosures --corp 삼성전자 --from 20240101 --to 20241231 --type A
uv run dart financial 00126380 2024
uv run dart financial-full 00126380 2024 --fs CFS
uv run dart indicators 00126380 2024 --class M210000
uv run dart multi-financial 00126380,00164779 2024
uv run dart shareholders 00126380
uv run dart periodic 00126380 2024 배당
uv run dart event 00126380 유상증자결정
uv run dart securities 00126380 지분증권
```

전체 CLI 명령 목록은 `docs/cli.md`에 생성되어 있습니다.

## XBRL 사용법

XBRL 재무제표 원본 ZIP은 `download-xbrl` 명령 또는 MCP 도구 `download_xbrl`로 내려받습니다.

```bash
uv run dart download-xbrl 00126380 2024 --report 11011 -o ./downloads
```

보고서 코드는 다음 값을 사용합니다.

- `11011`: 사업보고서
- `11012`: 반기보고서
- `11013`: 1분기보고서
- `11014`: 3분기보고서

접수번호를 알고 있으면 MCP 도구에서는 `rcept_no`를 직접 지정할 수 있습니다. 접수번호를 모르면 `corp_code`, `bsns_year`, `reprt_code`로 정기공시 목록에서 자동 조회합니다.

XBRL 표준계정과목체계는 다음처럼 조회합니다.

```bash
uv run dart taxonomy BS1
```

지원 양식은 `BS1`, `BS2`, `BS3`, `IS1`, `IS2`, `IS3`, `IS4`, `CIS1`, `CIS2`, `CIS3`, `CIS4`, `DC1`, `DC2`입니다.

## MCP 도구

현재 MCP 서버는 17개 도구를 제공합니다.

- 공시/기업: `search_disclosures`, `search_corp_code`, `get_company_info`
- 재무: `get_financial_statements`, `get_financial_statements_full`, `get_multi_company_financials`
- 지표: `get_financial_indicators`, `get_multi_company_indicators`
- 지분공시: `get_major_shareholders_report`, `get_executive_stock_report`
- 보고서 주요정보: `get_periodic_report`, `get_major_event_report`, `get_securities_report`
- 다운로드/XBRL: `download_document`, `download_xbrl`, `get_xbrl_taxonomy`
- TEMIS 내보내기: `export_temis_topic_cases` (opt-in, 아래 "TEMIS(finov2) 연동" 참고)

전체 MCP 도구 목록은 `docs/tools.md`에 생성되어 있습니다.

## TEMIS(finov2) 연동: 안전한 내보내기 흐름

이 저장소는 OpenDART 호출·파싱·변환을 전담하는 adapter/exporter입니다. finov2는
OpenDART를 직접 호출하지 않고, 이 저장소가 만든 JSON 파일을 읽기만 하는 **파일
소비자**입니다. 전체 흐름은 다음과 같습니다.

1. **API 키 설정** — 위 "빠른 시작"의 `DART_API_KEY`를 설정합니다. `.env`를
   사용하더라도 커밋하지 않습니다.

2. **회사명 → corp_code 조회**

   ```bash
   uv run dart search 삼성전자
   ```

   MCP 도구로는 `search_corp_code`를 사용합니다. 검색 결과가 여러 건이면(모호)
   corp_code를 직접 지정해야 합니다.

3. **corp_code로 공시 검색**

   ```bash
   uv run dart disclosures --corp 삼성전자 --from 20240101 --to 20241231 --type A
   ```

   `corp_code`가 검색의 canonical key입니다. 회사명은 항상 2단계의 resolver를
   거쳐 corp_code로 해석된 뒤에만 OpenDART `list.json` 조회에 쓰이며, 회사명 자체를
   OpenDART 파라미터로 직접 보내지 않습니다.

4. **감사보고서 사실(회계감사인) 확인**

   ```bash
   uv run dart periodic 00126380 2024 회계감사인
   ```

   이 항목은 아래 5단계에서 `get_audit_report_structured`가 구조화된 필드(감사인,
   감사의견, 핵심감사사항 등)로 추출하는 것과 동일한 데이터입니다.

5. **TEMIS 토픽 케이스 내보내기**

   ```bash
   uv run dart temis-topic-cases 2024 --corp 삼성전자 --report 11011 \
     -o /absolute/path/to/dart_topic_cases.json
   ```

   또는 MCP 도구 `export_temis_topic_cases`(`bsns_year`, `output_path`,
   `corp_code`/`corp_name`, `reprt_code`, `extra_keywords`)를 사용합니다.

   ⚠️ `output_path`는 실행할 때마다 항상 **덮어씁니다**(overwrite) — 기존 파일에
   append하지 않습니다. `corp_code`/`corp_name`이 둘 다 없거나, 회사명이 여러
   회사와 매치되거나(모호), 해석되지 않거나(미해결), DART API 오류가 발생하면
   출력 파일은 전혀 건드리지 않습니다(기존 파일이 있어도 그대로 남습니다).
   출력은 finov2 `DartTopicCase` 스키마와 호환되는 JSON 배열입니다.

6. **finov2에 연결** — finov2 쪽 환경 변수를 설정합니다.

   ```bash
   DART_TOPIC_CASES_PATH=/absolute/path/to/dart_topic_cases.json
   DART_TOPIC_SEARCH_ENABLED=true
   ```

   **finov2는 별도의 OpenDART adapter가 필요 없습니다.** 이 저장소가 OpenDART
   수집·파싱·`DartTopicCase` 변환을 전담하며, finov2는 `DART_TOPIC_CASES_PATH`가
   가리키는 JSON 파일을 읽기만 합니다. 향후 finov2 쪽에 정기 자동 갱신용
   런타임 job scheduler를 추가하는 경우에도, 그 스케줄러는 OpenDART를 직접
   호출하지 않고 이 저장소의 `dart temis-topic-cases` 명령(또는
   `export_temis_topic_cases` MCP 도구)을 호출해 JSON 파일을 재생성하는
   방식이어야 합니다 — OpenDART 호출/파싱 로직 자체는 계속 이 저장소에만
   있어야 합니다.

### 보안 참고사항

- API 키는 `DART_API_KEY` 환경 변수(또는 커밋하지 않는 `.env`)로만 전달합니다.
- 이 README와 저장소의 모든 문서·evidence 파일은 실제 키 값 대신
  `<redacted>` 같은 placeholder만 사용합니다 — 실제 키나 실제 키가 포함된
  요청 URL을 문서화하지 마세요.
- OpenDART 요청의 `crtfc_key` 쿼리 파라미터는 로그, 예외 메시지, `dart
  diagnostics` 출력 어디에서도 평문으로 노출되지 않습니다
  (`dart_search_mcp/redact.py`).

## 개발 및 검증

문서를 현재 CLI/MCP 표면에서 다시 생성합니다.

```bash
uv run python scripts/generate_docs.py
```

문서 drift를 포함한 로컬 QA를 실행합니다.

```bash
uv run python scripts/qa.py
```

개별 테스트만 실행하려면 다음 명령을 사용합니다.

```bash
uv run python -m unittest discover -v
```
