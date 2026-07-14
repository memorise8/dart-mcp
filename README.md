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
제외하고 있습니다. API 키는 로그·에러 메시지·요청 URL에서 리댁션(`<redacted>`)
처리되며(`dart_search_mcp/redact.py`), 커밋에는 `.env`를 포함하지 않습니다.

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

현재 MCP 서버는 18개 도구를 제공합니다.

- 공시/기업: `search_disclosures`, `search_corp_code`, `get_company_info`
- 재무: `get_financial_statements`, `get_financial_statements_full`, `get_multi_company_financials`
- 지표: `get_financial_indicators`, `get_multi_company_indicators`
- 지분공시: `get_major_shareholders_report`, `get_executive_stock_report`
- 보고서 주요정보: `get_periodic_report`, `get_major_event_report`, `get_securities_report`
- 다운로드/XBRL: `download_document`, `download_xbrl`, `get_xbrl_taxonomy`
- 감사서류 추출: `extract_audit_documents` (아래 "대량 공시 수집과 감사서류 추출" 참고)
- TEMIS 내보내기: `export_temis_topic_cases` (opt-in, 아래 "TEMIS(finov2) 연동" 참고)

전체 MCP 도구 목록은 `docs/tools.md`에 생성되어 있습니다.

## 대량 공시 수집과 감사서류 추출

전자공시 원문(ZIP)에서 감사보고서/연결감사보고서 XML을 대량으로 뽑아내는 3단계
CLI 흐름입니다. 전체 흐름은 OpenDART `list.json`(공시목록)과
`document.xml`(원문 ZIP) API만 사용합니다 — DART 뷰어 HTML을 스크래핑하지
않습니다.

### Step A: 공시 목록 수집 (`dart collect-disclosures`)

```bash
uv run dart collect-disclosures --from 20240101 --to 20241231 -o manifest.json
```

- 기본 대상(`--targets` 생략 시)은 공시유형 `F`(외부감사) 중 `report_name`에
  "감사보고서"가 포함된 건(부분일치이므로 "연결감사보고서"도 함께 포함) +
  공시유형 `A`(정기공시) 중 `report_name`에 "사업보고서"가 포함된 건입니다.
  다른 조합은 `--targets "F:감사보고서,A:사업보고서"` 형식(`PBLNTF_TY:키워드`를
  쉼표로 구분)으로 지정합니다.
- OpenDART는 `corp_code` 없는 `list.json` 조회에 검색기간 3개월 제한(status
  100)을 강제합니다. `--from`/`--to` 구간은 자동으로 3 **달력월(calendar
  month)** 이하의 연속된 창으로 나눠 순회하므로(고정 일수가 아니라 달력월
  단위라, 11~2월에 시작하는 창에서도 실제 3개월을 넘지 않습니다), 몇 년치
  구간을 한 번에 넘겨도 안전합니다.
- 각 (대상, 창) 조합마다 전체 페이지를 순회합니다. 페이지 조회가 예외로
  실패하거나 예외 없이 오류가 "반환"되는 경우 모두 `--max-retries`(기본
  4)까지 재시도하며, 그래도 실패한 페이지는 매니페스트의 `failed_pages`에
  남고 전체 실행은 중단되지 않습니다.
- `rcept_no` 기준으로 전체 창/대상에 걸쳐 중복을 제거하고, `report_name`으로
  연결감사보고서/감사보고서/사업보고서/기타로 분류합니다(`counts_by_category`).
- `--exclude-corrections`를 주면 `[기재정정]`/`[첨부추가]` 정정 공시를
  제외합니다.
- `--resume`을 주면 체크포인트(`<output>.checkpoint.json`, output 파일과 같은
  디렉터리)를 이어서 사용해 이미 완료된 (대상, 창, 페이지)는 다시 조회하지
  않습니다. `--resume` 없이 실행하면 기존 체크포인트를 버리고 새로
  시작합니다. 체크포인트에 기록된 이전 실행의 `bgn_de`/`end_de`/`targets`가
  이번 호출과 다르면 오류로 중단합니다(다른 기간의 진행 상황이 섞이는 것을
  방지).

출력 매니페스트(`manifest.json`)에는 `records[]`(레코드별
`category`/`report_name`/`rcept_no`/`rcept_dt`/`corp_code`/`corp_name` 등),
`counts_by_category`, `total_records`, `windows`, `targets`, `failed_pages`,
`generated_at`이 담깁니다. 이 매니페스트가 Step C의 입력입니다.

### Step B: 단일 필링 감사서류 추출 (`dart audit-documents`)

```bash
uv run dart audit-documents --rcept-no <14자리 접수번호> -o outdir --include both
```

- 지정한 필링의 원문 ZIP(`document.xml`)을 내려받아 감사보고서(ACODE
  `00760`)/연결감사보고서(ACODE `00761`) XML 엔트리를 분류한 뒤
  (`dart_search_mcp/document_zip.py`) `outdir/<rcept_no>/`에 추출하고, 같은
  디렉터리에 `manifest.json`을 씁니다.
- `--rcept-no` 대신 `--code <corp_code> --year <bsns_year>` 또는
  `--corp <회사명> --year <bsns_year>`로 접수번호를 자동 조회할 수도
  있습니다(`--corp`는 정확히 하나의 회사로 해석되어야 하며, 모호하거나
  매치가 없으면 OpenDART를 호출하지 않고 오류를 반환합니다).
- `--include`는 `audit`(감사보고서만), `consolidated`(연결감사보고서만),
  `both`(기본값) 중 하나입니다.
- `--require-consolidated`를 주면 연결감사보고서가 없을 때 오류로 처리하고
  아무 파일도 쓰지 않습니다. 옵션이 없는 기본값에서는 없음을 매니페스트에만
  기록하는 정상 결과입니다.
- 실패 시(입력 검증, 회사명 해석 실패, 접수번호 미해결, 다운로드 오류, 손상된
  ZIP, `--require-consolidated`인데 연결감사보고서가 없음) 0이 아닌 종료
  코드를 반환하며 출력 디렉터리는 전혀 만들지 않습니다(부분 파일 없음).
- 동일한 기능이 MCP 도구 `extract_audit_documents`로도 제공됩니다.

### Step C: 매니페스트 전체 일괄 추출 (`dart bulk-audit-documents`)

```bash
uv run dart bulk-audit-documents --manifest manifest.json -o outdir
```

- Step A의 매니페스트(`records[].rcept_no` 사용) 또는 접수번호 문자열 배열
  JSON(`--rcept-json`) 중 정확히 하나를 입력으로 받아, 필링마다 Step B와
  동일한 추출을 반복합니다. 대량(bulk) 처리는 이 CLI 명령을 통해서만
  제공합니다 — 필링 한 건만 추출할 때는 계속 `audit-documents`를
  사용하세요.
- 필링 한 건에서 발생하는 어떤 오류/예외든(손상·암호화된 ZIP 포함) 그
  필링만 실패로 기록하고 전체 실행을 중단시키지 않습니다.
- 필링별 결과는 다음 4가지 상태 중 하나로 기록됩니다.
  - `succeeded`: 요청한 문서(`--include`)를 모두 찾아 추출함
  - `skipped_no_audit`: 감사보고서를 요청했지만 이 필링의 ZIP에 별도 감사
    보고서 엔트리가 없음 (오류 아님 — 아래 "ZIP 구조 현실" 참고)
  - `skipped_no_consolidated`: 연결감사보고서를 요청(`--include
    consolidated`/`both` 또는 `--require-consolidated`)했지만 없음
  - `failed`: 그 밖의 모든 오류(다운로드 실패, 손상된 ZIP 등)
- `--limit N`으로 처리 대상을 앞에서부터 N건으로 제한합니다. `--resume`을
  주면 체크포인트(`<output>/bulk-manifest.json.checkpoint.json`)를 이어서
  사용해 이미 `succeeded`인 필링은 재처리하지 않습니다(`failed`/`skipped_*`/
  미처리 필링은 재시도). `--manifest` 입력에는 `--exclude-corrections`도
  적용됩니다(`--rcept-json` 입력에는 적용되지 않습니다).
- 결과는 `<output>/bulk-manifest.json`에 `results[]`(필링별
  `rcept_no`/`status`/`output_path`/`message`)와 `counts_by_status`로
  남습니다.

### ZIP 구조 현실

- 감사보고서/연결감사보고서 단독(공시유형 F) 필링의 ZIP은 엔트리 하나
  (`<rcept_no>_00760.xml` 또는 `_00761.xml`)만 있습니다.
- 사업보고서(공시유형 A) 필링의 ZIP은 본문 엔트리(`<rcept_no>.xml`)만 있을
  수도 있고, 별도의 `_00760.xml`/`_00761.xml` 감사보고서 엔트리가 함께
  포함될 수도 있습니다. 별도 엔트리가 없으면 감사보고서 내용은 본문 XML에
  **인라인**으로 포함되어 있으며, 이 저장소는 그 인라인 내용을 추출하지
  않습니다 — 이 경우는 오류가 아니라 `skipped_no_audit`(Step C) 또는
  "없음"(Step B 매니페스트)으로 정상 기록됩니다.

### 정직성 노트 (이 저장소가 하지 않는 것)

- DART 뷰어 HTML을 스크래핑하지 않습니다 — OpenDART `list.json`/
  `document.xml` API만 사용합니다.
- 대량(bulk) 수집/추출(`collect-disclosures`, `bulk-audit-documents`)은
  **CLI 전용**입니다 — 장시간 실행되는 블로킹 MCP 도구는 두지 않습니다.
  단일 항목 조회/추출은 계속 `search_disclosures`/`extract_audit_documents`
  MCP 도구를 사용합니다.
- 이 저장소는 finov2 DB에 아무것도 쓰지 않습니다. `manifest.json`/
  `bulk-manifest.json`은 로컬 파일일 뿐이며, finov2로의 import는(있다면)
  finov2 쪽에서 별도로 진행하는 작업입니다(이 저장소는 구현하지 않으며
  완료를 주장하지 않습니다).

## 감사 구조화 추출 + KAM(핵심감사사항) LLM 태깅

로컬 감사보고서 XML에서 감사의견/계속기업 불확실성/핵심감사사항(KAM) 원문 등
구조화 사실을 대량으로 뽑아내고(①), 그 KAM 원문을 LLM으로 고정 태소노미에
따라 배치 태깅한 뒤(②), 두 산출물을 join하는 3단계 CLI 흐름입니다. 입력은
위 "대량 공시 수집과 감사서류 추출"의 Step C(`dart bulk-audit-documents`)
산출물(`docs-dir`)입니다.

### ① `dart extract-audit-facts`: 구조화 감사 사실 추출

```bash
uv run dart extract-audit-facts --manifest manifest.json --docs-dir outdir \
  -o audit_facts.jsonl
```

- Step 1(`dart collect-disclosures`) 매니페스트의 각 필링에 대해
  `docs-dir/<rcept_no>/`의 로컬 감사 XML(연결감사 `_00761` > 감사 `_00760` >
  사업보고서 임베드 순으로 하나 선택)을 순수 파서로 파싱해, **1행/1공시**
  JSONL(`audit_facts.jsonl`)과 요약 JSON을 만듭니다. 각 행에는 `corp_code`,
  `audit_opinion`, `going_concern`, `kam_present`, `kam_raw`(핵심감사사항
  원문), `kam_tags`(이 단계에서는 항상 `[]`) 등이 담깁니다.
- rcept 한 건에서 발생하는 어떤 오류(XML 없음 포함)도 그 rcept만 실패로
  기록하고 전체 실행을 중단시키지 않습니다. `--resume`으로 이어서
  처리할 수 있고, 크래시 후 resume해도 JSONL 자가복구로 중복행이 생기지
  않습니다.
- `--corp-cls`, `--limit`으로 대상 범위를 좁힐 수 있고, `--emit-topic-cases
  PATH`를 주면 같은 실행 안에서 finov2 `DartTopicCase` JSON도 함께
  생성합니다(아래 "finov2 연계 주의" 참고).

### ② `dart tag-kam`: KAM 원문 LLM 배치 태깅

```bash
uv run dart tag-kam --facts audit_facts.jsonl -o kam_tags.jsonl
```

`audit_facts.jsonl`에서 `kam_present=true`이고 `kam_raw`가 있는 행만 골라,
OpenAI 호환 엔드포인트로 핵심감사사항 원문을 고정 태소노미(통제 어휘)에
따라 배치 태깅하고, 사이드카 `kam_tags.jsonl`(`{rcept_no, tags, dropped,
kam_hash, model, base_url, tagged_at}`)을 만듭니다. `audit_facts.jsonl`
자체는 이 단계에서 전혀 수정하지 않습니다.

**OpenAI 호환 엔드포인트 설정** — 기본값은 API 키가 필요 없는 사내
엔드포인트입니다.

| 설정 | 플래그 | 환경 변수 | 기본값 |
|------|--------|-----------|--------|
| base URL | `--base-url` | `KAM_LLM_BASE_URL` | `http://192.168.0.4:10532/v1` |
| 모델 | `--model` | `KAM_LLM_MODEL` | `gpt-5.4-mini` |

우선순위는 명시 플래그 > 환경 변수 > 위 기본값 순이며, **API 키를 요구하지
않습니다**(요청 헤더에 인증 토큰을 싣지 않습니다). 엔드포인트가 떠 있지
않으면(연결 거부/타임아웃) rcept 한 건만 실패로 기록하고 전체 실행은
계속되며, 응답 본문 전체나 실제 엔드포인트 URL을 로그/에러 메시지에
그대로 덤프하지 않습니다.

**고정 태소노미** — LLM은 `dart_search_mcp/kam_taxonomy.py`의 통제 어휘
목록에서만 태깅할 수 있습니다. 목록 밖 태그나 오탈자는 코드가 폐기하고
`dropped`로 따로 집계합니다(성공을 과장하지 않습니다). 해당하는 태그가
없으면 `tags: []`입니다.

**캐시·resume·dry-run**:

- content-hash 캐시(`model`+`base_url`+`kam_raw` 기준)로 같은 입력을 다시
  호출하지 않습니다. `--cache PATH`로 경로를 지정할 수 있습니다.
- `--resume`을 주면 체크포인트를 이어서 사용해 이미 태깅된 rcept를
  건너뜁니다(크래시 후 resume에도 중복행 0). `--resume` 없이 실행하면 항상
  새로 시작합니다.
- `--dry-run`은 엔드포인트를 전혀 호출하지 않고 대상 건수와 태소노미
  목록만 출력합니다(요금이 발생하지 않습니다).
- `--concurrency N`으로 동시 태깅 워커 수(캐시 미스만 해당)를 제한합니다.

### ③ `dart merge-kam-tags`: kam_tags를 audit_facts에 join

```bash
uv run dart merge-kam-tags --facts audit_facts.jsonl --tags kam_tags.jsonl \
  -o audit_facts.enriched.jsonl
```

②의 사이드카 `kam_tags.jsonl`(`rcept_no -> tags`)을 ①의 `audit_facts.jsonl`에
`rcept_no` 기준으로 join해, `kam_tags` 컬럼이 채워진 **새 파일**
(`audit_facts.enriched.jsonl`)을 만듭니다.

- 입력 `audit_facts.jsonl`은 **수정하지 않습니다**(읽기 전용으로만 열립니다
  — 원본은 항상 그대로 남습니다). `kam_tags.jsonl`에 매칭되는 `rcept_no`가
  없는 행은 ①의 `kam_tags: []`를 그대로 유지합니다. `kam_tags` 외 나머지
  필드는 전혀 바뀌지 않습니다.
- 시계/네트워크를 전혀 쓰지 않는 순수 함수라 **결정론적**입니다 — 같은 두
  입력 파일이면 항상 같은 출력 바이트를 만듭니다. 출력은 tmp+`Path.replace`로
  원자적으로 씁니다(부분 파일이 남지 않습니다).
- `kam_tags.jsonl`에 같은 `rcept_no`가 여러 줄 있으면(예: 여러 번의 `tag-kam`
  실행을 합친 파일) **가장 나중 줄의 값이 우선**합니다. 손상된(파싱 불가)
  줄은 그 줄만 방어적으로 건너뛰고 전체 실행을 중단하지 않습니다.
- 실행 후 stdout에 `facts` 행수, 매칭(`매칭`)/미매칭(`미매칭`) 건수를
  요약으로 출력합니다.

### finov2 연계 주의

`DartTopicCase`(위 "TEMIS(finov2) 연동" 섹션의 5단계 산출물)는 이 3단계
흐름과는 **별도의, 손실이 있는(lossy) 투영**입니다 — `going_concern`,
`kam_tags` 등 이 흐름이 만드는 풍부한 필드 다수를 `DartTopicCase`가
그대로 전파하지 않습니다. `kam_tags`의 **원천 데이터**는 어디까지나
`audit_facts.jsonl`/`kam_tags.jsonl`(그리고 이 둘을 합친
`audit_facts.enriched.jsonl`)이며, `dart temis-topic-cases`/
`export_temis_topic_cases`가 만드는 `DartTopicCase` JSON이 아닙니다.
`kam_tags`를 `DartTopicCase`에 반영하는 작업은 **후속 작업**입니다 — 이
저장소는 아직 구현하지 않으며 완료를 주장하지 않습니다.

## TEMIS(finov2) 연동: 안전한 내보내기 흐름

이 저장소는 OpenDART 호출·파싱·변환을 전담하는 adapter/exporter입니다. finov2는
OpenDART를 직접 호출하지 않고, 이 저장소가 만든 JSON 파일을 소비합니다. export는
항상 **회사 1건** 단위 산출물이며(5단계), finov2가 이를 소비하는 방식은 현재의
파일 직접 읽기 모드와 향후의 DB import 모드 두 단계로 나뉩니다(6단계). 전체
흐름은 다음과 같습니다.

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

6. **finov2에 연결** — 5단계의 산출물은 항상 **회사 1건** 단위입니다(실행할
   때마다 `output_path`를 통째로 덮어씀). finov2가 이 파일을 소비하는 방식은
   다음 두 단계로 나뉩니다.

   **현재(파일 기반, 지금 바로 동작):** finov2 쪽 환경 변수를 설정하면
   finov2가 `DART_TOPIC_CASES_PATH`가 가리키는 파일을 직접 읽습니다.

   ```bash
   DART_TOPIC_CASES_PATH=/absolute/path/to/dart_topic_cases.json
   DART_TOPIC_SEARCH_ENABLED=true
   ```

   ⚠️ 이 모드에서는 파일 하나에 **회사 1건만** 담깁니다 — finov2가 파일을
   그대로 읽기 때문에, 다른 회사의 export로 같은 경로를 재실행하면 이전 회사
   데이터는 사라집니다. 여러 회사를 동시에 다루려면 아래 "목표" 모드가
   필요합니다.

   **목표(DB import, finov2 쪽에서 별도로 진행 중):** finov2는 각 회사별
   export 산출물을 자신의 DB로 가져오는(import) `case_id` upsert 방식을
   준비하고 있습니다. `case_id`는 `corp_code`와 `rcept_no`를 포함해 전역
   고유·결정적으로 계산되므로(`dart_search_mcp/temis_export.py`), 회사가
   달라도 값이 절대 충돌하지 않아 DB upsert 키로 쓸 수 있습니다. 이 모드가
   갖춰지면 `DART_TOPIC_CASES_PATH`는 finov2가 매 요청마다 읽는 런타임 파일이
   아니라 import 배치의 **입력(ingestion input)** 파일이 되어, 여러 회사의
   export를 누적할 수 있습니다. 이 DB import 작업은 **finov2 쪽에서 별도로
   진행 중인 작업**입니다 — 이 저장소는 구현하지 않으며 완료를 주장하지
   않습니다. 여기서는 방향만 명시합니다.

   어느 모드든 **finov2는 별도의 OpenDART adapter가 필요 없습니다.** 이
   저장소가 OpenDART 수집·파싱·`DartTopicCase` 변환을 전담하며, finov2는 이
   저장소가 `dart temis-topic-cases` 명령(또는 `export_temis_topic_cases` MCP
   도구)으로 만든 JSON 파일을 읽거나 import하기만 합니다. 향후 finov2 쪽에
   정기 자동 갱신용 런타임 job scheduler를 추가하는 경우에도, 그 스케줄러는
   OpenDART를 직접 호출하지 않고 이 저장소의 export 명령/도구를 호출해 JSON
   파일을 재생성하는 방식이어야 합니다 — OpenDART 호출/파싱 로직 자체는
   계속 이 저장소에만 있어야 합니다.

### 보안 참고사항

- API 키는 `DART_API_KEY` 환경 변수(또는 커밋하지 않는 `.env`)로만 전달합니다.
- 이 README와 저장소의 모든 문서·evidence 파일은 실제 키 값 대신
  `<redacted>` 같은 placeholder만 사용합니다 — 실제 키나 실제 키가 포함된
  요청 URL을 문서화하지 마세요.
- API 키는 로그·에러 메시지·요청 URL에서 redact되며(`dart_search_mcp/redact.py`),
  커밋에 `.env`를 포함하지 않습니다.

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
