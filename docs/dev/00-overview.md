# 개발 개요 — DART 기반 기업정보 데이터 생태계

이 `docs/dev/`는 세션 종료·재개·다른 에이전트가 이어서 개발할 수 있도록 **기능별로 정리한 개발 문서**다.
각 문서는 "무엇인지 / 설계·결정 / 명령·사용법 / 산출물 / 상태 / 이어서 개발하는 법"을 담는다.

## 저장소 지도 (`/data_raid/ruci_workspace/repo/mcp/`)
| 저장소 | 역할 | 상태 | 원격 |
|---|---|---|---|
| **dart-search-mcp** | DART 전자공시 조회·수집·감사추출·KAM태깅·TEMIS export (허브) | ✅ 운영 | github.com/memorise8/dart-mcp |
| **nts-status-mcp** | 국세청 사업자 휴폐업 상태조회 | ✅ 완성(로컬) | 없음 |
| **nps-mcp** | 국민연금 가입 사업장 내역 | ✅ 완성(로컬) | 없음 |
| **kipris-mcp** | 특허(KIPRIS) | ⏳ 미착수(다음) | — |

**설계 원칙:** 각 공공데이터 원천 = **독립 MCP 저장소**(바운디드 컨텍스트·키·생명주기 분리). 저장소끼리
코드로 결합하지 않고, **사업자번호를 조인 키**로 소비자(temis)가 결합한다. dart-search-mcp는 "DART만" 전담(순수성).

## 기능 문서
- [01 감사 구조화 추출](01-audit-facts-extraction.md) — 감사 XML → 감사인·감사의견·계속기업·KAM (audit_facts.jsonl)
- [02 KAM LLM 태깅](02-kam-llm-tagging.md) — KAM 원문 → 고정 태소노미 태그 (kam_tags.jsonl)
- [03 NICE 약식재무 DB](03-nice-financial-db.md) — DART 재무 + 감사의견 → NICE 약식재무(ab08) 스키마
- [04 공공데이터 MCP 생태계](04-public-data-mcp-ecosystem.md) — 휴폐업/국민연금/특허 companion 서버
- [05 데이터셋](05-datasets.md) — dart_collected/ 수집물 목록
- [06 재개 Runbook](06-resume-runbook.md) — 새 클론·새 세션의 환경 복원, 비추적 자산, 외부 의존성 점검
- [07 로컬 아티팩트 인벤토리](07-local-artifact-inventory.md) — Git 제외 데이터의 스냅샷·행 수·SHA-256 복원 확인값

## 데이터 흐름 (큰 그림)
```
DART OpenAPI ─┬─ collect-disclosures → bulk-audit-documents → extract-audit-facts → audit_facts.jsonl
              │                                                      │
              │                                            tag-kam → kam_tags.jsonl → merge-kam-tags
              │                                                      │
              └─ financial API ──── nice_yaksik_export ── + 감사의견 → NICE 약식재무 DB

공공데이터(별도 원천, 사업자번호 조인):
  국세청 휴폐업(nts-status-mcp) · 국민연금(nps-mcp) · 특허(kipris-mcp) ── join by 사업자번호 ──▶ temis
```

## 다음 착수 로드맵 (우선순위)
1. **kipris-mcp** (특허) 신설 — nts/nps가 템플릿. API: [04 문서](04-public-data-mcp-ecosystem.md).
2. **비상장 재무 확장** — 39,658 외감 비상장사 재무를 감사 XML에서 파싱(Phase ①형). [03 문서](03-nice-financial-db.md).
3. **Phase ③** — KAM LLM 태그를 TEMIS DartTopicCase에 반영. [02 문서](02-kam-llm-tagging.md).
4. **라이브 검증·원격** — nts/nps는 data.go.kr 키 발급 후 실측, GitHub 원격 생성 필요(사용자 몫).

## 공통 규약 (모든 저장소)
- MCP 서버(`<name> serve`) + CLI, 대량작업은 CLI 전용. 코어는 순수·결정론(네트워크/시계 격리).
- API 키는 `.env`에서 로드, **로그·에러·산출물에서 redact**. `.env`·데이터 산출물 gitignore.
- 검증: `uv run python -m unittest` + (dart) `scripts/qa.py`. 테스트는 실네트워크 없이 mock.
- 개발 방식: 브레인스토밍→스펙→subagent(implementer→reviewer→fix)→최종 리뷰→병합. 정직성 우선(실패/미상/skip 정직 표기).
