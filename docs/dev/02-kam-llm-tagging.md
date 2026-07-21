# 02 KAM(핵심감사사항) LLM 태깅 (Phase ②)

**저장소:** dart-search-mcp · **상태:** ✅ 완성·병합 (main)

## 무엇인지
Phase ①이 보존한 **KAM 원문(`kam_raw`)**을 LLM으로 읽어 **고정 태소노미** 주제 태그를 부여한다.
"수익인식이 핵심감사사항이었던 회사" 같은 주제별 질의를 가능하게 함. ①의 산출물은 불변; 태그는
사이드카 `kam_tags.jsonl`로 분리 후 join.

## 핵심 결정
- **자립 CLI + OpenAI 호환 엔드포인트 직접호출(키 불필요)**: 사용자가 로컬/LAN에 OpenAI 호환 엔드포인트를
  띄워 둠. 저장소는 httpx로 직접 호출(anthropic/openai SDK 아님). Claude Code 등 하네스 무의존, 터미널/cron 단독.
  - 기본 `--base-url http://192.168.0.4:10532/v1`, `--model gpt-5.4-mini`. env `KAM_LLM_BASE_URL`/`KAM_LLM_MODEL`.
  - **엔드포인트는 사용자 소유·가변** → 쓰기 전 `curl {base_url}/models`로 생존 확인. 미응답 시 명확한 연결에러.
- **고정 태소노미**(19태그): LLM은 목록에서만 선택(보조 분류). 목록 밖은 폐기+`dropped` 집계. → 일관·집계 용이.
- LLM 의존은 tag-kam 경로에만 **lazy import**로 격리(코어 순수성 유지).

## 코드
- `dart_search_mcp/kam_taxonomy.py` — 순수: `KAM_TAXONOMY`(19), `build_tagging_prompt`, `parse_tag_response`(목록밖 폐기, 3단 방어파싱, 예외 안 던짐).
- `dart_search_mcp/tools/kam_tagger.py` — httpx `call_llm`(키 불필요, response.text 미덤프) + content-hash 캐시(키에 model+base_url 포함) + `tag_one_kam`.
- `dart_search_mcp/tools/tag_kam_cli.py` — `tag-kam`(배치·동시성·resume·dry-run) + `merge-kam-tags`(join).

## 명령
```bash
dart tag-kam --facts audit_facts.jsonl -o kam_tags.jsonl [--base-url ...] [--model ...] [--concurrency 4] [--resume] [--dry-run]
dart merge-kam-tags --facts audit_facts.jsonl --tags kam_tags.jsonl -o audit_facts.enriched.jsonl
```
- 대상은 `kam_present=true` 행만(상장 위주). content-hash 캐시로 재실행 증분. resume 크래시 자가복구, 정직 카운터(source_skipped/failed_batches 등).
- `kam_tags.jsonl` 행: `{rcept_no, tags[], dropped[], kam_hash, model, base_url, tagged_at}`.

## 실제 결과 (전량 태깅)
대상 3,061건 **100% 태깅**(dropped=1 → 태소노미 커버 우수). 최다: 수익인식 1,356 / 손상 630 / 건설계약·진행률 283 / 매출채권 227 / 재고자산평가 215.

## KAM이 3,061건뿐인 이유
KAM은 주로 상장사 의무. 데이터의 94%가 corp_cls E(외감 비상장, KAM 0.8%). 상장 Y/K는 96~99% 보유. 유실 아님, 제도상 분포.

## 이어서 개발 — Phase ③ (미착수)
**kam_tags를 TEMIS DartTopicCase에 반영**: 현재 DartTopicCase의 `topic_tags`는 `temis_export.TOPIC_KEYWORDS`(14) 결정론
키워드 매칭. LLM `kam_tags`가 더 정확하므로 `emit-topic-cases`(또는 별도 명령)가 kam_tags를 우선 쓰게 잇는 것.
- 결정 필요: 태소노미(19) ↔ TOPIC_KEYWORDS(14) ↔ topic_slug **어휘 정렬**(case_id=TEMIS upsert 키라 slug 바뀌면 키 변함), LLM태그 **교체 vs 병합**, KAM 없는 44k(비상장) 정책.
- 단순 배선 아님 → 브레인스토밍→스펙 먼저.
- 테스트: `uv run python -m unittest tests.test_kam_taxonomy tests.test_kam_tagger tests.test_tag_kam_cli`
