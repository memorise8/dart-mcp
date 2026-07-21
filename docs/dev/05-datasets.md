# 05 데이터셋 (dart_collected/)

**위치:** `dart-search-mcp/dart_collected/` · **gitignore됨**(저장소에 없음 — 이 문서가 유일 기록) · ~15GB.
수집 기간: **2025-07-01 ~ 2026-06-30** (감사보고서 필링 기준).

## 파일 목록
| 파일 | 내용 | 규모 |
|---|---|---|
| `manifest.json` | 공시 목록(감사/연결감사/사업보고서) | 48,537건 (감사 40,705 / 연결감사 3,362 / 사업보고서 4,470) |
| `docs/<rcept>/*.xml` | 감사서류 XML 원문(+rcept별 manifest) | 49,979 XML / 14GB |
| `bulk-manifest.json` | bulk-audit-documents 추출 상태 | — |
| **`audit_facts.jsonl`** | 구조화 감사 사실 (Phase ①) | **47,477행** |
| **`kam_tags.jsonl`** | KAM 주제 태그 (Phase ②) | **3,061건** (dropped=1) |
| `audit_facts.enriched.jsonl` | 위 둘 join | 47,477행 |
| `dart_topic_cases.json` | TEMIS DartTopicCase | 47,477건 |
| **`nice_yaksik_financials.{jsonl,csv}`** | NICE 약식재무 재무 DB (상장) | **2,983행** |

## 회사 구성 (audit_facts 기준)
- 고유 회사: **42,387**. 상장(Y/K/N) 고유 2,729(종목코드 보유 2,983). 외감 비상장(E) 39,658.
- **핵심 사실**: 92%가 비상장(E). 감사의견/KAM/재무의 "구조화 API"는 상장 8%만 커버, **로컬 XML이 92%의 유일 소스**.

## 주요 분포
- 감사의견: 적정 42,284 / 의견거절 2,514 / 한정 1,920 / 부적정 38 / unknown 721.
- 계속기업 불확실성: 2,540건. KAM 보유: 3,061(상장 위주).
- KAM 주제(태깅): 수익인식 1,356 / 손상 630 / 건설계약·진행률 283 / 매출채권 227 / 재고자산평가 215 …

## 재생성 (파이프라인)
```bash
dart collect-disclosures --from 20250701 --to 20260630 -o dart_collected/manifest.json   # (창분할·resume)
dart bulk-audit-documents --manifest dart_collected/manifest.json -o dart_collected/docs --include both --resume
dart extract-audit-facts --manifest ... --docs-dir dart_collected/docs -o dart_collected/audit_facts.jsonl --emit-topic-cases dart_collected/dart_topic_cases.json
dart tag-kam --facts dart_collected/audit_facts.jsonl -o dart_collected/kam_tags.jsonl     # (캐시 증분)
dart merge-kam-tags --facts ... --tags ... -o dart_collected/audit_facts.enriched.jsonl
uv run python <scratchpad>/nice_yaksik_export.py all dart_collected/nice_yaksik_financials.jsonl   # 재무 DB
```
- OpenDART 일일한도 ~4만/일이 유일 실제 제약. 대량 단계는 전부 resume 지원.
- 증분 운영: 신규 분기 공시가 나오면 같은 순서 재실행(cron 가능).

## 주의
- `dart_collected/`는 gitignore — **커밋 금지**(14GB). 백업/이전은 파일 복사로.
- API 키는 `.env`(gitignore). 산출물에 키 없음(redact).
