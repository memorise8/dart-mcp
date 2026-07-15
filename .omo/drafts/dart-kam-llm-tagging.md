# 설계: KAM 원문 LLM 태깅 (Phase ②)

작성일: 2026-07-14
상태: 설계안 (사용자 리뷰 대기)
선행: `.omo/drafts/dart-audit-structured-extraction.md` (Phase ①, `audit_facts.jsonl`·`kam_raw` 산출)

## 1. 목표와 범위

Phase ①이 보존한 **KAM 원문(`kam_raw`)**을 LLM으로 읽어 **고정 태소노미**의 주제 태그를 부여,
"수익인식이 핵심감사사항이었던 회사" 같은 **주제별 질의**를 가능하게 한다. ①의 산출물은 **불변**;
태그는 **사이드카 `kam_tags.jsonl`(rcept_no → tags)**로 분리 산출 후 join한다.

- 대상: `audit_facts.jsonl`에서 `kam_present=true`인 행만 (실측 3,061건, 상장 위주).
- ①의 결정론 산출물 불변, `kam_tags`는 rcept_no로 join(append-only enrichment).

범위 밖: ① 재파싱, KAM 없는 92% 처리, 재무수치 분석, temis DB 직접 쓰기(기존 제약 유지).

## 2. 확정된 결정 (2026-07-14 사용자 승인)

1. **LLM 운영 = 자립 명령 `dart tag-kam`, OpenAI 호환 엔드포인트 직접 호출**: 사용자가 로컬/LAN에
   OpenAI 호환 엔드포인트를 띄워 둠(키 불필요). 저장소는 이 엔드포인트를 직접 호출한다.
   **API 키 불필요** — 앞서 우려한 "저장소에 키 유입" 문제가 없다. Claude Code 등 하네스 무의존,
   터미널/cron 단독 실행. **조회에는 LLM 불필요**(태그는 1회성 생성).
   - 기본 `--base-url http://192.168.0.4:10532/v1`, `--model gpt-5.4-mini` (실측 동작 확인:
     temperature 0에 고정 태소노미 JSON 배열 정확 반환). env `KAM_LLM_BASE_URL`/`KAM_LLM_MODEL`로도.
   - 엔드포인트는 사용자 소유·변경 가능 → **하드코딩 강제 금지**, 플래그/env로 오버라이드. 미응답 시
     명확한 에러(연결 실패 안내).
2. **태그 스키마 = 고정 태소노미**: 통제된 태그 목록에서만 부여. LLM은 KAM 텍스트를 읽고
   그 목록 중 **적용되는 태그의 부분집합**을 고른다(LLM 보조 분류). 목록 밖 태그 금지 →
   일관·집계 용이, 동의어 문제 없음. 기존 `temis_export.TOPIC_KEYWORDS`(14개)를 기반으로 확장.

## 3. 저장소 결합도 격리 (중요)

자립 명령을 택했으므로 저장소에 LLM 클라이언트가 들어온다. 기존 "코어는 외부호출 없음·결정론"
원칙을 지키기 위해 **LLM 의존을 `tag-kam` 경로에만 격리**한다:
- `openai` SDK(또는 순수 httpx)는 **선택 의존성**(optional dependency). `tag-kam` 모듈이
  **함수 실행 시점에 lazy import**(모듈 top-level import 금지 — Phase ① 파서의 import 순수성 계승).
- 파서/어댑터/extract-facts 등 기존 코어는 이 모듈을 import하지 않는다.
- **API 키 없음**(엔드포인트가 요구 안 함). 그래도 base_url·응답 원문을 로그/산출물에 부주의하게
  덤프하지 않는다(위생 유지).

## 4. 아키텍처

```
audit_facts.jsonl (① 산출, kam_present=true 필터)
        │
        ▼
   dart tag-kam  (자립 CLI, OpenAI 호환 엔드포인트 httpx 직접호출, lazy import)
        │  - 고정 태소노미로 제약된 프롬프트
        │  - content-hash 캐시(재호출 방지) + 체크포인트/resume
        │  - temperature 0, JSON 강제, 목록밖 태그 폐기
        ▼
   kam_tags.jsonl (rcept_no -> {tags[], model, kam_hash, tagged_at})  ← ① 불변, 사이드카
        │
        ▼ (선택) dart merge-kam-tags
   audit_facts.enriched.jsonl (facts + kam_tags 컬럼)  또는 조회 시 join
```

## 5. 고정 태소노미 (통제 어휘)

기존 `TOPIC_KEYWORDS` 14개 + 실무 빈출 KAM 주제 보강(예시, 구현 시 확정):
`수익인식, 손상(영업권/유형/무형), 공정가치평가, 계속기업, 우발부채·소송, 재고자산평가,
대손충당금, 특수관계자거래, 리스, 금융부채·풋옵션, 계약부채, 보증·충당부채, 이연법인세,
사업결합·PPA, 매출채권, 개발비 자산화, 종속·관계기업 투자평가, 건설계약·진행률, 기타`.
- 각 태그에 **간단 정의 1줄**을 프롬프트에 포함(LLM 판정 일관성).
- LLM 출력은 이 목록의 **정확한 문자열 부분집합**만 허용. 목록 밖·오탈자는 코드가 **폐기**하고
  `dropped_tags`로 집계(정직성). 해당 없으면 `[]`.
- `기타`는 "목록의 다른 어디에도 안 맞지만 KAM은 존재" 표시용(과다남발 방지 프롬프트 지시).

## 6. 결정론·비용·멱등

- **temperature 0** + 출력 JSON 스키마 강제(도구/JSON 모드). 완전 결정론은 아니나 재현성 최대화.
- **content-hash 캐시**: `sha256(kam_raw)` → tags 매핑을 캐시 파일에 저장. 같은 KAM은 재호출 안 함
  (정정공시 등 동일 원문 중복 흡수). resume 시 캐시+체크포인트로 이미 처리분 skip.
- **체크포인트/resume/예외격리**: 기존 `extract_facts`/`bulk_audit` 패턴 재사용. rcept별 격리.
- **동시성·레이트 제한** 옵션(`--concurrency`), `--limit`, `--model`(기본 `gpt-5.4-mini`),
  `--base-url`(기본 `http://192.168.0.4:10532/v1`). **대상 건수를 dry-run으로 출력**(로컬 엔드포인트라
  과금은 없지만 실행 규모·시간 확인용).
- **모델 기록**: 각 태그 행에 `model`·`base_url`·`kam_hash`·`tagged_at` 저장 → 재현·감사 가능.

## 7. 인터페이스 (CLI)

```
# 태깅(자립 실행)
dart tag-kam --facts dart_collected/audit_facts.jsonl \
  -o dart_collected/kam_tags.jsonl \
  [--base-url http://192.168.0.4:10532/v1] [--model gpt-5.4-mini] \
  [--concurrency 4] [--limit N] [--resume] [--dry-run]

# (선택) 병합 — ① 불변 유지, 별도 산출
dart merge-kam-tags --facts audit_facts.jsonl --tags kam_tags.jsonl -o audit_facts.enriched.jsonl
```
- `--dry-run`: 엔드포인트 미호출, 대상 건수·태소노미만 출력.
- 대량·장기 작업이라 **CLI 전용**(MCP tool 아님).

## 8. temis/DartTopicCase 연계 (선택, 후속)

현재 DartTopicCase의 `topic_tags`는 결정론 키워드 매칭. B의 `kam_tags`(LLM)가 더 정확하므로,
`--emit-topic-cases`가 kam_tags를 우선 사용하도록 잇는 것은 **후속 옵션**으로 남긴다(이번 범위 밖).
v1은 사이드카 `kam_tags.jsonl` 산출까지.

## 9. 테스트 계획

- LLM은 비결정적 → **테스트는 LLM 클라이언트(httpx/call_fn)를 mock**. 실제 호출은 라이브 스모크에서만.
- 단위: 프롬프트 빌더(태소노미 주입), 응답 파서(JSON→tags), **목록밖 태그 폐기**+dropped 집계,
  content-hash 캐시 히트 시 미호출, resume/예외격리, dry-run 비용추정.
- 라이브 스모크(소량, 예 20건): 실제 엔드포인트(gpt-5.4-mini)로 태깅해 태그가 KAM 내용과 상식적으로 맞는지 육안 +
  목록밖 태그 0 확인. 산출·키 미커밋.
- 회귀: 기존 전체 테스트 그대로 통과(코어 무영향 확인).

## 10. 리스크

- **엔드포인트 가용성**: 로컬/LAN 엔드포인트라 미기동 시 실패 → 명확한 연결에러 + `--base-url` 안내.
  base_url/model을 하드코딩하지 않고 플래그/env로.
- **레이트/규모**: 대상 3,061건(실측). 캐시·concurrency·resume으로 관리, dry-run으로 규모 선확인.
- **비결정성**: temp0+content-hash 캐시로 완화하나 완전 결정론 아님 → `model`/`base_url`/`kam_hash`
  기록으로 감사·재현.
- **태소노미 적합성**: 실제 KAM 분포를 스모크로 보고 태소노미를 1회 조정(구현 초기).
- **위생**: OpenAI SDK/httpx는 lazy import로 코어 격리. 키는 없지만 응답 원문 부주의 덤프 금지.
