# 구현 계획: 감사보고서 XML 구조화 추출 (Phase ①)

스펙: `.omo/drafts/dart-audit-structured-extraction.md` (사용자 승인 완료)
실행 방식: subagent-driven-development (태스크별 implementer → task reviewer)
브랜치: `feat/audit-structured-extraction` (main에서 분기)

## Global Constraints (모든 태스크 공통, 리뷰어 렌즈)

- **파서는 순수·결정론 함수.** 네트워크 호출 금지, `datetime.now()`/`random` 등 부수효과 금지.
  타임스탬프 등 "지금" 값은 호출자가 주입 (기존 `temis_export`와 동일 원칙).
- **API 키·비밀 노출 금지.** (이 작업은 로컬 XML만 다루므로 네트워크 없음 — 그래도 로그/에러에
  경로 외 민감정보 남기지 않음.)
- **`fiscal_year` = 보고기간 종료일의 연도**, `settlement_month` 병기. report_name 말미
  `(YYYY.MM)` 우선, 실패 시 rcept_dt/XML 종료일 폴백, 그래도 실패면 `parse_flags.fiscal_year=false`.
- **감사의견 문구는 `공정하게`/`적정하게` 둘 다 처리.** 분류: 부적정(`표시하고 있지 않습니다`)
  → 의견거절(`표명하지 않습니다`) → 한정(`제외하고는`+`표시하고 있습니다`) → 적정 → else `unknown`.
- **계속기업 오탐 금지.** `계속기업전제`/`계속기업으로서의 존속능력`(경영진책임 보일러플레이트)로
  true 판정 금지. 강조/불확실성 섹션 landmark(`계속기업 관련 중요한/중대한 불확실성`, 또는
  "…존속능력에 …의문을 제기할 수 있는 중요한 불확실성이 존재")로만 true.
- **KAM 원문(`kam_raw`)·강조사항 원문 보존 필수** (Phase ②의 입력). `kam_tags`는 ①에서 항상 빈 값.
- **파싱 실패는 정직하게.** 필드별 실패는 그 필드만 빈 값 + `parse_flags`, 전체 실패는 summary 집계.
  성공을 과장하지 않는다.
- 테스트: `uv run python -m unittest` 전부 통과. 14GB 원문 미커밋 — 소형 픽스처만 커밋.

## Task 1 — `audit_xml_parser.py` (순수 파서 + 픽스처 + 단위테스트)

`dart_search_mcp/audit_xml_parser.py` 신규.

- `@dataclass(frozen=True, slots=True) ParsedAuditReport` — 스펙 §4 필드 전부.
  `parse_flags`는 frozen dataclass 또는 `dict[str,bool]`(불변 취급).
- `parse_audit_xml(xml_bytes: bytes, meta: dict) -> ParsedAuditReport`:
  - 태그 제거 → 평문 정규화(기존 reports.py `_normalize_text` 재사용 가능하면 재사용).
  - landmark 구간 분할(스펙 §5 순서). 각 구간 헬퍼 함수 분리(테스트 용이).
  - `classify_opinion(opinion_para) -> str`, `detect_going_concern(text) -> (bool, snippet)`,
    `extract_kam(text) -> str`, `extract_emphasis(text) -> str`,
    `derive_fiscal_year(meta) -> (int|None, int|None)`(year, month).
  - meta는 manifest record dict(rcept_no/corp_*/report_name/rcept_dt/flr_nm/source_url/category).
- 픽스처: `tests/fixtures/audit_xml/`에 발췌 XML 5종 커밋 —
  (1) 비상장 적정·`공정하게`·KAM없음, (2) 상장 적정·`공정하게`·KAM있음,
  (3) `적정하게`(K-IFRS) 문구, (4) 계속기업 불확실성 강조 섹션 있음,
  (5) 한정/의견거절 문구(합성 가능, 실제 없으면 표준문구 기반 합성 픽스처 + 주석).
  각 발췌는 원문 일부만(수백 KB 아님) — 감사의견~책임 단락 + 있으면 KAM/강조.
- 단위테스트: 4개 의견 유형, 계속기업 오탐 방지(경영진책임 보일러플레이트→false), KAM 유무,
  fiscal_year 12월/3월 결산, parse_flags 정확성.

검증: `uv run python -m unittest tests.test_audit_xml_parser`

## Task 2 — 어댑터 `ParsedAuditReport → AuditReportRecord`

`audit_xml_parser.py`(또는 별도 `audit_facts_adapter.py`)에
`to_audit_report_record(parsed: ParsedAuditReport) -> AuditReportRecord`.

- 매핑: auditor→auditor, audit_opinion(enum 한글)→audit_opinion, kam_raw→core_audit_matter,
  emphasis_raw→emphasis_matter, ""→special_matter(없으면), corp_*/rcept_no→동일,
  bsns_year=str(fiscal_year), settlement_date=종료일 문자열, source_url 동일.
  reprt_code/business_year_label은 F형 감사보고서엔 정기보고서코드 없음 → 빈 값 또는 "감사보고서"
  라벨(기존 temis 변환이 이 필드를 case_id에 쓰지 않는지 확인 후 안전값).
- 목적: 기존 `convert_audit_reports_to_topic_cases`가 무수정으로 동작.
- 테스트: 매핑 계약, 그리고 어댑팅한 레코드로 `convert_audit_reports_to_topic_cases` 호출 시
  기존 동작(스킵 규칙/케이스ID) 회귀 없음.

검증: `uv run python -m unittest tests.test_audit_facts_adapter tests.test_temis_export`

## Task 3 — 대량 CLI `dart extract-audit-facts` (facts.jsonl + summary)

`dart_search_mcp/tools/extract_facts.py` 신규 + `cli.py` 등록.
기존 `bulk_audit.py`의 체크포인트/resume/파라미터가드/rcept별 예외격리 패턴 재사용.

```
dart extract-audit-facts --manifest M --docs-dir D -o audit_facts.jsonl
  [--resume] [--limit N] [--corp-cls E,Y,K,N]
```

- manifest record 순회 → 해당 rcept 폴더의 감사 XML(우선순위: 연결 00761 > 감사 00760, 없으면 사업보고서 임베드) 선택 → `parse_audit_xml` → JSONL append.
- rcept별 try/except 격리: 실패는 summary에 `{rcept_no, error_kind}` 집계, 전체 중단 금지.
- checkpoint(처리된 rcept set) tmp+replace 원자적 쓰기, `--resume` 시 이어서. 실행 파라미터 가드.
- summary JSON: 총건/성공/파싱실패/의견분포(적정·한정·부적정·의견거절·unknown)/계속기업 true수/KAM 보유수.
- 로그: 진행률만, 원문/민감정보 미기재.
- 테스트: 임시 manifest+가짜 docs 폴더로 resume/격리/summary 집계/corp-cls 필터.

검증: `uv run python -m unittest tests.test_extract_facts`

## Task 4 — DartTopicCase 산출 연결 + (선택) 단건 MCP tool

- `dart extract-audit-facts`에 `--emit-topic-cases PATH` 옵션 추가:
  Task 2 어댑터 + 기존 `convert_audit_reports_to_topic_cases`/`topic_cases_to_json`로
  `dart_topic_cases.json` 생성(freshness_timestamp는 CLI가 `_utc_now_iso()` 주입 — 기존 temis.py 관례).
- (선택) 얇은 MCP tool `parse_audit_facts(rcept_no, docs_dir)`: 단건 파서 노출(순수함수라 저비용).
- 테스트: emit-topic-cases 산출 JSON이 기존 temis 스키마와 일치(필드/타입), freshness 주입.

검증: `uv run python -m unittest` (전체) + 소규모 실 XML 라이브 스모크(수집분 10건 파싱).

## 최종 검토

- 전체 test suite 통과.
- 실 데이터 스모크: `dart_collected`에서 `--limit 200`로 실행 → summary 의견분포가 상식적인지,
  계속기업 오탐 0에 가까운지, corp_cls E에서 KAM 대부분 빈지 육안 확인.
- 브랜치 whole-branch 리뷰(code-reviewer) 후 finishing-a-development-branch.
