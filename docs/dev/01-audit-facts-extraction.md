# 01 감사 구조화 추출 (Phase ①)

**저장소:** dart-search-mcp · **상태:** ✅ 완성·병합 (main)

## 무엇인지
수집한 감사보고서 XML(로컬)에서 **감사인·감사의견·계속기업 불확실성·핵심감사사항(KAM) 원문**을 결정론적으로
파싱해 질의 가능한 JSONL로 만든다. LLM 없이, 비용 0, 재현 가능.

## 핵심 결정 (근거)
- 데이터의 **92%가 corp_cls E(외감 비상장)** → 정기보고서 `회계감사인` API는 **8%(상장)만 커버**. 나머지 92%는
  **로컬 감사 XML이 유일 소스**. 그래서 API가 아닌 **XML 파서**가 본체.
- 감사의견 문구는 회계기준에 따라 `공정하게 표시`(일반기업회계기준)/`적정하게 표시`(K-IFRS) 둘 다 처리.
- **계속기업 오탐 금지**: `계속기업전제`(경영진 책임 보일러플레이트)는 모든 보고서에 있음 → 강조섹션
  landmark(`계속기업 관련 중요한/중대한 불확실성`, `…의문을 제기…`)로만 true.
- 감사인명은 manifest `flr_nm`에 이미 존재(XML은 검증용).

## 코드
- `dart_search_mcp/audit_xml_parser.py` — 순수 파서 `parse_audit_xml(xml_bytes, meta) -> ParsedAuditReport`.
  텍스트 landmark로 구간분할, `classify_opinion`(적정/한정/부적정/의견거절/unknown), `detect_going_concern`,
  `extract_kam`/`extract_emphasis`, `derive_fiscal_year`. `parse_flags`로 필드별 성공 정직 표기.
- `dart_search_mcp/audit_facts_adapter.py` — `ParsedAuditReport → AuditReportRecord`(기존 temis_export 재사용용).
- `dart_search_mcp/tools/extract_facts.py` + `cli.py` — 대량 CLI.

## 명령
```bash
dart extract-audit-facts --manifest dart_collected/manifest.json --docs-dir dart_collected/docs \
  -o dart_collected/audit_facts.jsonl \
  --emit-topic-cases dart_collected/dart_topic_cases.json [--resume] [--limit N] [--corp-cls E,Y,K,N]
```
- 대량이라 CLI 전용. 체크포인트/resume, 크래시 자가복구(`_scan_and_repair_output`: torn 라인 폐기·rcept 중복 병합·원자적 재작성), rcept별 예외격리.
- `--emit-topic-cases`는 완결된 JSONL을 다시 읽어 finov2/**TEMIS** `DartTopicCase` JSON 생성(기존 어댑터+변환기 재사용).

## 산출물 스키마 (`audit_facts.jsonl`, 1행/1공시)
`rcept_no · corp_code/corp_name/corp_cls/stock_code · report_name/rcept_dt/category · fiscal_year · settlement_month ·
auditor · audit_opinion · opinion_snippet · going_concern · going_concern_snippet · kam_present · kam_raw ·
emphasis_raw · kam_tags(②가 채움, ①에선 []) · parse_flags · source_url · doc_path`

## 실제 결과 (1년치 47,477행)
감사의견: 적정 42,284 / 의견거절 2,514 / 한정 1,920 / 부적정 38 / unknown 721 · 계속기업 true 2,540 · KAM 보유 3,061.
(데이터셋 상세: [05](05-datasets.md))

## 이어서 개발 / 확장
- **비상장 재무 추출**: 감사 XML에는 재무제표(첨부)도 있음 → 같은 파서 구조로 재무테이블 파서를 만들면 전수 재무 가능. [03 문서](03-nice-financial-db.md) 참고.
- 알려진 비블로킹 Minor: going_concern RAISE 정규식이 '제기' vs '초래' 단어구분 의존(헤더없는 2차 경로 라이브 미검증, precision-biased 의도).
- 테스트: `uv run python -m unittest tests.test_audit_xml_parser tests.test_extract_facts tests.test_audit_facts_adapter`
