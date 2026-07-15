# 구현 계획: KAM 원문 LLM 태깅 (Phase ②)

스펙: `.omo/drafts/dart-kam-llm-tagging.md` (사용자 결정 반영: 자립 명령 + 고정 태소노미)
실행 방식: subagent-driven-development (태스크별 implementer → task reviewer)
브랜치: `feat/kam-llm-tagging` (main에서 분기)
대상 규모(실측): `audit_facts.jsonl`의 `kam_present=true` = **3,061건**.

## Global Constraints (모든 태스크 공통, 리뷰어 렌즈)

- **LLM 클라이언트 격리**: `openai` SDK(또는 순수 httpx)는 선택 의존성. tag-kam 경로에서만
  **lazy import**(함수 실행 시점). 파서/어댑터/extract-facts 등 코어는 이 모듈을 import하지 않는다.
  (Phase ① import 순수성 계승.)
- **OpenAI 호환 엔드포인트, API 키 불필요**: 기본 `--base-url http://192.168.0.4:10532/v1`,
  `--model gpt-5.4-mini`(실측 동작 확인). env `KAM_LLM_BASE_URL`/`KAM_LLM_MODEL`로도.
  **하드코딩 강제 금지** — 플래그/env 오버라이드. 미응답 시 명확한 연결에러(안내 포함). base_url·응답
  원문을 로그/산출물에 부주의하게 덤프하지 않는다.
- **① 산출물 불변**: `audit_facts.jsonl` 수정 금지. 태그는 사이드카 `kam_tags.jsonl`로만.
- **고정 태소노미 강제**: LLM 출력 중 목록 밖·오탈자 태그는 코드가 폐기 + `dropped_tags` 집계.
  해당 없으면 `[]`. 성공 과장 금지(정직한 집계).
- **멱등·규모통제**: content-hash 캐시(재호출 방지), 체크포인트/resume, temperature 0, dry-run 제공.
- **LLM은 테스트에서 mock**: 실제 호출은 라이브 스모크에서만. 기존 전체 테스트 회귀 금지.
- `dart_collected/`·산출물·캐시 커밋 금지.

## Task 1 — 태소노미 + 프롬프트 빌더 + 응답 파서 (순수, LLM 미호출)

`dart_search_mcp/kam_taxonomy.py` 신규:
- `KAM_TAXONOMY: tuple[(tag, 정의1줄), ...]` — 스펙 §5 기반 통제 어휘(기존 TOPIC_KEYWORDS 확장).
- `build_tagging_prompt(kam_raw) -> str|messages` — 태소노미+정의+"목록에서만, JSON 배열로,
  해당없으면 빈배열, 과다남발 금지" 지시 포함. 순수 함수.
- `parse_tag_response(text) -> tuple[list[str] tags, list[str] dropped]` — LLM 응답(JSON) 파싱,
  **목록 밖/오탈자 폐기**하고 dropped로 분리. 견고(코드펜스/잡음 방어).
- 단위 테스트: 태소노미 유효성, 프롬프트에 전 태그 포함, 파서가 목록밖 폐기/빈배열/잡음 방어.
검증: `uv run python -m unittest tests.test_kam_taxonomy`

## Task 2 — OpenAI 호환 클라이언트 래퍼 + content-hash 캐시 + 단건 태깅

`dart_search_mcp/tools/kam_tagger.py` 신규 (LLM 경로, lazy import):
- `_client(base_url)` — `openai`(또는 httpx)를 **함수 내 import**, `base_url`로 OpenAI 호환
  엔드포인트에 연결(키 불필요; SDK가 요구하면 더미 키). 연결/응답 실패 시 명확한 에러(안내).
- `tag_one_kam(kam_raw, *, model, base_url, client, cache) -> {tags, dropped, kam_hash, model, base_url}` —
  `sha256(kam_raw)` 캐시 조회 → 히트면 미호출, 미스면 build_prompt→chat.completions(temp0)→parse→캐시 저장.
- 캐시는 파일 기반(예 `kam_tags.cache.json`), 원자적 쓰기. 캐시 키에 model도 포함(모델 바뀌면 재태깅).
- 테스트: **클라이언트 mock**(네트워크 없음)으로 캐시 히트 시 미호출, 응답 파싱 통합, base_url/응답
  원문이 로그에 안 새는지 확인.
검증: `uv run python -m unittest tests.test_kam_tagger`

## Task 3 — 자립 CLI `dart tag-kam`

`dart_search_mcp/tools/tag_kam_cli.py`(또는 kam_tagger 내) + `cli.py` 등록:
```
dart tag-kam --facts audit_facts.jsonl -o kam_tags.jsonl
  [--base-url http://192.168.0.4:10532/v1] [--model gpt-5.4-mini]
  [--concurrency 4] [--limit N] [--resume] [--dry-run] [--cache PATH]
```
- facts.jsonl 읽어 `kam_present=true`만 대상. `--dry-run`: 대상 건수·태소노미 출력, 엔드포인트 미호출.
- 각 대상 `tag_one_kam` → `kam_tags.jsonl` append: `{rcept_no, tags[], dropped[], kam_hash, model, base_url, tagged_at}`.
  (`tagged_at`은 CLI가 주입 — 코어는 시계 미접근.)
- 체크포인트/resume/rcept별 예외격리/원자적 쓰기: 기존 `extract_facts` 패턴 재사용. 크래시 자가복구 동형.
- 동시성 제한, 진행률 로그(원문/키 미기재), summary(태깅 성공/실패/dropped 총계/태그 분포).
- 테스트: mock LLM으로 dry-run 비용추정, resume 무중복, 예외격리, kam_present 필터, summary 집계.
검증: `uv run python -m unittest tests.test_tag_kam_cli`

## Task 4 — (선택) `dart merge-kam-tags` + 문서

- `dart merge-kam-tags --facts audit_facts.jsonl --tags kam_tags.jsonl -o audit_facts.enriched.jsonl`:
  rcept_no join으로 `kam_tags` 컬럼 채운 **새 파일** 산출(① 불변). 매칭 없으면 []. 순수·결정론.
- README/스펙에 tag-kam 사용법·비용·태소노미·finov2 연계(후속) 문서화.
- 테스트: join 정확성, 미매칭 처리, 결정론.
검증: `uv run python -m unittest` (전체)

## 최종 검토

- 전체 test suite 통과(코어 무영향 회귀 확인).
- **라이브 스모크(소량, 예 --limit 20)**: 실제 엔드포인트(gpt-5.4-mini)로 태깅 → 태그가 KAM과
  상식적으로 맞는지 육안, 목록밖 태그 0(전부 dropped 처리), 캐시 재실행 시 미호출 확인. 산출 미커밋.
  실제 KAM 분포를 보고 **태소노미 1회 조정** 여지.
- dry-run으로 규모 확인 후 전량(3,061건) 태깅은 사용자 승인 하에.
- whole-branch 리뷰(opus) → finishing-a-development-branch.
