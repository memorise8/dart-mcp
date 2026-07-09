# temis-dart-mcp-hardening - Work Plan

## TL;DR (For humans)
**What you'll get:** DART MCP가 TEMIS의 DART 주제별 사례검색 데이터 원천이자 OpenDART adapter 역할을 맡도록 보강됩니다. 기존 사람이 읽는 MCP/CLI 출력은 유지하면서, finov2가 그대로 소비할 수 있는 `dart_topic_cases.json` export 경로를 추가합니다.

**Why this approach:** 현재 finov2는 이미 `DART_TOPIC_CASES_PATH` JSON을 읽는 구조라, OpenDART 호출/파싱 adapter를 finov2에 중복 구현할 필요가 없습니다. DART MCP가 수집과 변환을 끝내고 finov2는 생성 파일만 바라보게 하면 경계가 단순해집니다.

**What it will NOT do:** finov2 제품 코드를 이 repo에서 수정하지 않고, finov2-side OpenDART adapter도 만들지 않습니다. NICE/Layer 3 리포트나 가치평가는 다루지 않습니다. 기본 테스트에서 live DART API를 호출하지 않습니다.

**Effort:** Large
**Risk:** Medium - live OpenDART 호출은 되지만 현재 출력 계약이 문자열 중심이고 API key 로그 노출 가능성이 있어 보안/호환성 검증이 필요합니다.
**Decisions to sanity-check:** 기존 16개 MCP tool/CLI command는 유지하고, TEMIS용 structured/export surface는 opt-in으로 추가합니다. DART 공시 검색은 회사명 직접 조회가 아니라 `corp_code` 기반으로 고정합니다. finov2 연동은 새 adapter가 아니라 `DART_TOPIC_CASES_PATH=/path/to/dart_topic_cases.json` 설정/운영 wiring으로 제한합니다.

Your next move: another session can run this plan as implementation work. Full execution detail follows below.

---

> TL;DR (machine): Large / Medium risk / Make dart-search-mcp own the OpenDART adapter/exporter role, preserve existing string surface, add typed core, safe logging, corp_code-first search, TEMIS `DartTopicCase` JSON export consumable via finov2 `DART_TOPIC_CASES_PATH`, docs, and no-live-default QA.

## Scope
### Must have
- Preserve the existing public MCP tool names and CLI command names unless this plan explicitly adds opt-in names.
- Add structured internal return types for DART API calls while keeping current human-readable string outputs working.
- Add a safe OpenDART HTTP boundary that never leaks `crtfc_key` through logs, exceptions, docs, or evidence.
- Treat `corp_code` as the canonical disclosure-search key; company name search must be a separate resolver step.
- Add deterministic offline tests with mocked OpenDART responses.
- Add a TEMIS-compatible export path that emits JSON matching the current `DartTopicCase` contract in `../../finov2/fino-backend/app/schemas/dart_topic_search.py`.
- Make this repo the owner of the OpenDART adapter role: company resolution, disclosure/report retrieval, snippet/topic extraction, and TEMIS JSON generation happen here.
- Document the finov2 consumption contract as file-based: set `DART_TOPIC_CASES_PATH` to the generated `dart_topic_cases.json`; keep any future finov2 work to thin config/job orchestration.
- Add source provenance fields: a unique `case_id` (fixture pattern: `dart-<topic>-<year>-<seq>`), company identifier, company name, fiscal year, report id, auditor, topic tags, snippet, source URL, document id, extraction confidence, and freshness timestamp.
- Add docs and QA commands for default offline verification and opt-in live smoke verification.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- Must not modify finov2 product code in this repo-local work.
- Must not plan or implement a finov2-side OpenDART adapter that duplicates this repo's collection/parsing logic.
- Must not implement NICE ingestion, Layer 3 commercial reports, valuation scoring, or counterparty risk scoring.
- Must not remove or rename existing MCP tools or CLI commands.
- Must not make live OpenDART calls in the default unit test suite.
- Must not commit `.env`, `.omc`, `.venv`, downloaded ZIPs, raw disclosure archives, API keys, or unredacted request URLs.
- Must not rely on `corp_name` as an OpenDART `list.json` filter.
- Must not introduce a new dependency unless the todo names the reason and updates `uv.lock`.
- Must not claim production DART coverage from sample/export data alone.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD with Python `unittest`; use `httpx.MockTransport` or monkeypatchable client boundaries for all OpenDART responses.
- Evidence: `.omo/evidence/task-<N>-temis-dart-mcp-hardening.<ext>`.
- Common offline gate after each wave:
  - `uv run python -m unittest discover -v`
  - `uv run python -m compileall cli.py server.py dart_search_mcp tests scripts`
  - `uv run dart diagnostics`
  - `uv run dart --help`
  - `uv run python scripts/qa.py`
- Secret safety gate:
  - `rg -n "crtfc_key=[^ )\"']+" README.md docs dart_search_mcp cli.py server.py tests scripts .omo/evidence --glob '!*.json'` must not reveal a key-bearing URL.
  - `rg -n "secret-test-key" README.md docs .omo/evidence --glob '!*.json'` must return no matches.
- TEMIS schema gate:
  - Generate a sample export into `.omo/evidence/task-6-temis-dart-mcp-hardening.json`.
  - Validate required field names and JSON shape with a repo-local Python script, without importing finov2 production code.
  - Validate the generated file can be used as the value for finov2 `DART_TOPIC_CASES_PATH` by checking it is a JSON array of `DartTopicCase`-shaped records.
- Live smoke gate is opt-in only:
  - If `DART_API_KEY` is present, run one redacted Samsung Electronics smoke flow and save only sanitized evidence.
  - If `DART_API_KEY` is missing, record a skipped live-smoke evidence file and keep the plan passable.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.
- Wave 1: Foundation and safety. Typed core envelope, redacted HTTP boundary, structured corp-code resolver.
- Wave 2: DART data extraction. Corp-code-first disclosure search, audit/report structured extraction, document ZIP text/snippet extraction.
- Wave 3: TEMIS export. Topic tagging, `DartTopicCase` JSON generation, CLI/MCP export surface.
- Wave 4: Docs and readiness. Generated docs, README, QA script/live smoke, finov2 file-consumption contract, final scope audit.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2, 3, 4, 5, 6, 8, 10 | 2 |
| 2 | none | 3, 4, 8, 10 | 1 |
| 3 | 1, 2 | 4, 6, 8 | none |
| 4 | 1, 2, 3 | 5, 6, 8 | 7 after 2 |
| 5 | 1, 2, 4 | 6, 8 | 7 |
| 6 | 3, 4, 5 | 8, 9, 10 | 7 |
| 7 | 2 | 6, 8 | 4, 5 |
| 8 | 3, 4, 5, 6, 7 | 9, 10 | none |
| 9 | 8 | 10 | none |
| 10 | all | final | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
- [ ] 1. Add a structured result layer without breaking string outputs
  What to do / Must NOT do: Add structured dataclasses or typed dictionaries for DART API success, no-data, and error results under `dart_search_mcp/types.py` or a new focused module. Refactor only internals so existing MCP tool functions and CLI commands still return their current strings. Do not add Pydantic unless the executor justifies the dependency and updates `uv.lock`.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3, 4, 5, 6, 8, 10
  References (executor has NO interview context - be exhaustive): `dart_search_mcp/types.py:1`, `dart_search_mcp/client.py:8`, `dart_search_mcp/formatting.py:44`, `tests/test_public_surface.py:7`, `../../finov2/fino-backend/app/schemas/dart_topic_search.py:10`
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_structured_results -v` exits 0; `uv run python -m unittest tests.test_public_surface -v` still exits 0.
  QA scenarios (name the exact tool + invocation): happy: `uv run python -m unittest tests.test_structured_results -v | tee .omo/evidence/task-1-temis-dart-mcp-hardening.txt`; failure: a mocked non-`000` DART status returns a typed error object internally while the public formatter still emits the existing Korean error style, recorded in the same evidence file.
  Commit: Y | `feat(core): add structured DART result envelope`

- [ ] 2. Redact OpenDART API keys from logs and errors
  What to do / Must NOT do: Add a single redaction helper for URLs, params, and exception messages. Suppress or sanitize `httpx` request logging for CLI/MCP execution so `crtfc_key` never appears. Keep diagnostics reporting only configured/missing, never the value.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 3, 4, 8, 10
  References (executor has NO interview context - be exhaustive): `dart_search_mcp/client.py:13`, `dart_search_mcp/client.py:20`, `dart_search_mcp/client.py:40`, `dart_search_mcp/config.py:1`, `cli.py:50`, `tests/test_diagnostics.py:1`
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_redaction -v` exits 0; `DART_API_KEY=secret-test-key uv run dart diagnostics` exits 0 and output does not contain `secret-test-key`; the secret safety `rg` gate passes.
  QA scenarios (name the exact tool + invocation): happy: `DART_API_KEY=secret-test-key uv run dart diagnostics > .omo/evidence/task-2-temis-dart-mcp-hardening.txt`; failure: a forced timeout/error containing a query URL is formatted with `crtfc_key=<redacted>` and no raw key, appended to the evidence file.
  Commit: Y | `fix(security): redact OpenDART credentials from logs`

- [ ] 3. Add a structured company-code resolver
  What to do / Must NOT do: Split `search_corp_code` into a structured resolver that returns exact, prefix, and contains matches as records, then keep the current public string formatter as a wrapper. Add tests using an in-memory zipped `corpCode.xml`; no live OpenDART call in tests.
  Parallelization: Wave 1 | Blocked by: 1, 2 | Blocks: 4, 6, 8
  References (executor has NO interview context - be exhaustive): `dart_search_mcp/corp.py:19`, `dart_search_mcp/corp.py:49`, `cli.py:72`, `README.md:40`
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_corp_resolver -v` exits 0; exact Samsung-style match ranks before prefix/contains matches; current `uv run dart search <name>` output remains human-readable.
  QA scenarios (name the exact tool + invocation): happy: `uv run python -m unittest tests.test_corp_resolver -v | tee .omo/evidence/task-3-temis-dart-mcp-hardening.txt`; failure: empty company name returns a validation error and does not call the mocked network loader.
  Commit: Y | `feat(corp): expose structured company code resolver`

- [ ] 4. Make disclosure search corp-code-first and structured
  What to do / Must NOT do: Add a structured disclosure search function that accepts `corp_code` as the primary input and returns records with report name, receipt number, date, company code/name, stock code, filing type, and source URL. If a CLI user passes `--corp`, resolve it first via the resolver and then call `list.json` with `corp_code`; do not send `corp_name` to OpenDART `list.json`.
  Parallelization: Wave 2 | Blocked by: 1, 2, 3 | Blocks: 5, 6, 8
  References (executor has NO interview context - be exhaustive): `dart_search_mcp/tools/disclosures.py:8`, `dart_search_mcp/tools/disclosures.py:47`, `dart_search_mcp/tools/disclosures.py:53`, `dart_search_mcp/tools/disclosures.py:70`, `cli.py:88`, `README.md:40`
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_disclosure_search_structured -v` exits 0; a MockTransport assertion proves `corp_code=00126380` is present and `corp_name` is absent from the `list.json` request.
  QA scenarios (name the exact tool + invocation): happy: mocked `uv run python -m unittest tests.test_disclosure_search_structured -v | tee .omo/evidence/task-4-temis-dart-mcp-hardening.txt`; failure: ambiguous company name returns a deterministic "choose one corp_code" error instead of querying all companies.
  Commit: Y | `feat(disclosures): search OpenDART by canonical corp code`

- [ ] 5. Extract structured audit/report data for TEMIS source facts
  What to do / Must NOT do: Add structured parsing for periodic report type `회계감사인`, including auditor, audit opinion, special matters, emphasis matter, core audit matters, settlement date, business year label, receipt number, company code/name, and report code. Deduplicate repeated rows observed in live output by stable keys. Keep current string output for `get_periodic_report`.
  Parallelization: Wave 2 | Blocked by: 1, 2, 4 | Blocks: 6, 8
  References (executor has NO interview context - be exhaustive): `dart_search_mcp/tools/reports.py:8`, `dart_search_mcp/tools/reports.py:74`, `dart_search_mcp/tools/reports.py:89`, `dart_search_mcp/registries.py`, `README.md:43`
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_audit_report_structured -v` exits 0; duplicate mocked Samsung audit rows collapse to one source fact per stable source segment.
  QA scenarios (name the exact tool + invocation): happy: `uv run python -m unittest tests.test_audit_report_structured -v | tee .omo/evidence/task-5-temis-dart-mcp-hardening.txt`; failure: invalid `report_type` still returns the existing public error text while the structured layer raises/returns a typed validation error.
  Commit: Y | `feat(reports): expose structured audit report facts`

- [ ] 6. Generate TEMIS-compatible `DartTopicCase` JSON from structured facts
  What to do / Must NOT do: Add a converter that maps structured disclosure/audit facts into TEMIS `DartTopicCase` records and writes a JSON array suitable for finov2 `DART_TOPIC_CASES_PATH`. Include topic-tag extraction from a small deterministic Korean keyword dictionary, source URL `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=<rcept_no>`, document id, extraction confidence, and ISO freshness timestamp. Do not claim exhaustive tagging quality. Do not require finov2 to call OpenDART directly.
  Parallelization: Wave 3 | Blocked by: 3, 4, 5 | Blocks: 8, 9, 10
  References (executor has NO interview context - be exhaustive): `../../finov2/fino-backend/app/schemas/dart_topic_search.py:10`, `../../finov2/fino-backend/tests/fixtures/dart_topic_cases.json:1`, `dart_search_mcp/tools/reports.py:8`, `dart_search_mcp/tools/disclosures.py:8`
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_temis_topic_case_export -v` exits 0; generated JSON validates required fields with `python -m json.tool`; sample records include a unique `case_id`, `company_identifier`, `auditor`, `topic_tags`, `source_url`, `document_id`, `extraction_confidence`, and `freshness_timestamp`; tests assert strict types matching the finov2 pydantic schema (`fiscal_year` is an int, `extraction_confidence` is a float in [0, 1], `freshness_timestamp` is an ISO-8601 string, `topic_tags` is a list of strings) so a finov2 `TypeAdapter(list[DartTopicCase])` validation would pass; evidence notes the file can be used by finov2 via `DART_TOPIC_CASES_PATH`.
  QA scenarios (name the exact tool + invocation): happy: `uv run python -m unittest tests.test_temis_topic_case_export -v | tee .omo/evidence/task-6-temis-dart-mcp-hardening.txt` and write sample JSON to `.omo/evidence/task-6-temis-dart-mcp-hardening.json`; failure: a fact with empty source/receipt number is skipped or marked invalid and cannot produce a fake DART URL.
  Commit: Y | `feat(temis): generate DART topic case export`

- [ ] 7. Add safe document ZIP text extraction for snippets
  What to do / Must NOT do: Add a no-new-dependency document extraction helper that reads OpenDART document ZIP bytes, lists contained files, decodes XML/HTML-like text safely, strips tags with conservative stdlib logic, and returns bounded snippets around matched topic keywords. Do not persist downloaded ZIPs outside the caller-provided output or evidence path.
  Parallelization: Wave 2/3 | Blocked by: 2 | Blocks: 6, 8
  References (executor has NO interview context - be exhaustive): `dart_search_mcp/tools/downloads.py:8`, `dart_search_mcp/tools/downloads.py:26`, `dart_search_mcp/tools/downloads.py:160`, `dart_search_mcp/client.py:40`
  Acceptance criteria (agent-executable): `uv run python -m unittest tests.test_document_extraction -v` exits 0; tests cover ZIP with XML text, ZIP with multiple files, invalid ZIP, and no matching keyword.
  QA scenarios (name the exact tool + invocation): happy: `uv run python -m unittest tests.test_document_extraction -v | tee .omo/evidence/task-7-temis-dart-mcp-hardening.txt`; failure: invalid ZIP returns a typed extraction error without writing a partial file.
  Commit: Y | `feat(downloads): extract bounded disclosure snippets`

- [ ] 8. Add opt-in TEMIS export CLI and MCP surface
  What to do / Must NOT do: Add a CLI command such as `temis-topic-cases` and an MCP tool such as `export_temis_topic_cases` that generate TEMIS-compatible JSON from `corp_code`, `bsns_year`, `reprt_code`, optional topic keywords, and output path. The command should be the operational adapter boundary: finov2 consumes the written JSON file rather than implementing OpenDART calls. Preserve existing commands and tools; update public surface tests to include the new opt-in command/tool if added.
  Parallelization: Wave 3 | Blocked by: 3, 4, 5, 6, 7 | Blocks: 9, 10
  References (executor has NO interview context - be exhaustive): `cli.py:43`, `cli.py:265`, `server.py:1`, `dart_search_mcp/app.py:1`, `tests/test_public_surface.py:7`, `docs/tools.md:1`, `docs/cli.md:1`
  Acceptance criteria (agent-executable): `uv run dart temis-topic-cases --help` exits 0; MCP `list_tools()` includes the new tool while all previous 16 names remain; a mocked CLI run writes valid JSON to a temp file.
  QA scenarios (name the exact tool + invocation): happy: `uv run python -m unittest tests.test_temis_export_surface -v | tee .omo/evidence/task-8-temis-dart-mcp-hardening.txt`; failure: missing `corp_code` and unresolved ambiguous company name exit non-zero with no output file.
  Commit: Y | `feat(cli): add TEMIS DART topic case export`

- [ ] 9. Update docs and generated tool references for TEMIS integration
  What to do / Must NOT do: Update README and generated docs to explain the safe TEMIS flow: configure API key, resolve company code, search disclosures by corp code, extract audit/report facts, export topic cases, then point finov2 at the generated JSON with `DART_TOPIC_CASES_PATH=/absolute/path/to/dart_topic_cases.json` and `DART_TOPIC_SEARCH_ENABLED=true`. Include security notes about key redaction and no committed `.env`. Explicitly state that finov2 does not need a separate OpenDART adapter unless a future runtime job scheduler is added. Do not document live commands with real API keys or real unredacted URLs.
  Parallelization: Wave 4 | Blocked by: 8 | Blocks: 10
  References (executor has NO interview context - be exhaustive): `README.md:1`, `README.md:13`, `README.md:38`, `README.md:88`, `docs/tools.md:1`, `docs/cli.md:1`, `scripts/generate_docs.py:1`
  Acceptance criteria (agent-executable): `uv run python scripts/generate_docs.py` exits 0; `git diff -- docs README.md` shows TEMIS export docs including `DART_TOPIC_CASES_PATH`; `uv run python scripts/qa.py` exits 0.
  QA scenarios (name the exact tool + invocation): happy: `uv run python scripts/qa.py | tee .omo/evidence/task-9-temis-dart-mcp-hardening.txt`; failure: `rg -n "crtfc_key=[^<]" README.md docs .omo/evidence --glob '!*.json'` and `rg -n "secret-test-key" README.md docs .omo/evidence --glob '!*.json'` show no key-bearing docs or evidence.
  Commit: Y | `docs(temis): document DART topic case export flow`

- [ ] 10. Final compatibility, live-smoke, and scope-fidelity verification
  What to do / Must NOT do: Run the full offline gate, secret gate, generated sample validation, and optional live smoke. Live smoke must sanitize evidence before writing. Confirm no finov2 files changed, no finov2 OpenDART adapter was created, and no runtime state is staged. Do not mark complete from self-report only.
  Parallelization: Wave 4 | Blocked by: 1-9 | Blocks: final delivery
  References (executor has NO interview context - be exhaustive): this plan; `.omo/drafts/temis-dart-mcp-hardening.md`; `scripts/qa.py:1`; `.gitignore:1`; `../../finov2/fino-backend/app/schemas/dart_topic_search.py:10`
  Acceptance criteria (agent-executable): `git status --short` shows only intended source/docs/test/plan/evidence files; `uv run python scripts/qa.py` exits 0; generated TEMIS JSON sample passes `python -m json.tool`; secret safety gate passes; optional live smoke either passes with redacted evidence or records a missing-key skip.
  QA scenarios (name the exact tool + invocation): happy: `uv run python scripts/qa.py | tee .omo/evidence/task-10-temis-dart-mcp-hardening.txt`; failure: `git diff --name-only ../../finov2` must be empty for this repo-local plan, `rg -n "OpenDART adapter|DART adapter" ../../finov2/fino-backend ../../finov2/fino-frontend ../../finov2/temis-ops` must not show a new finov2 adapter implementation from this work, and `rg -n "NICE|valuation|Layer 3 commercial" dart_search_mcp cli.py server.py README.md docs tests scripts` plus the secret safety gate must not show unsupported scope creep or leaked keys.
  Commit: Y | `test(temis): verify DART MCP export readiness`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit: every todo has references, exact acceptance criteria, happy/failure QA, evidence path, and commit line.
- [ ] F2. Code quality review: structured internals are small, typed, tested, and do not duplicate public formatting logic unnecessarily.
- [ ] F3. Real manual QA: run CLI help, diagnostics, mocked TEMIS export, and optional live Samsung flow when `DART_API_KEY` is available; evidence must be redacted.
- [ ] F4. Scope fidelity: no finov2 product edits, no finov2 OpenDART adapter, no NICE/Layer 3 implementation, no public command/tool removals, no committed runtime/secrets.

## Commit strategy
- Commit by wave/todo, not one broad change.
- Keep `.omc/`, `.venv/`, `.env`, downloaded raw ZIPs, and local runtime logs out of commits.
- Suggested sequence:
  - `feat(core): add structured DART result envelope`
  - `fix(security): redact OpenDART credentials from logs`
  - `feat(corp): expose structured company code resolver`
  - `feat(disclosures): search OpenDART by canonical corp code`
  - `feat(reports): expose structured audit report facts`
  - `feat(downloads): extract bounded disclosure snippets`
  - `feat(temis): generate DART topic case export`
  - `feat(cli): add TEMIS DART topic case export`
  - `docs(temis): document DART topic case export flow`
- `test(temis): verify DART MCP export readiness`

## Success criteria
- Existing DART MCP and CLI surfaces still work.
- Default QA is offline and green.
- No API key or key-bearing URL is printed or committed.
- Company-name input is resolved to `corp_code` before disclosure search.
- The repo can generate a TEMIS-compatible DART topic case JSON sample with source traceability.
- README/docs explain that finov2 can consume the generated JSON by setting `DART_TOPIC_CASES_PATH`, so a separate finov2 OpenDART adapter is not required for this layer.
- The final git status contains only intentional plan/implementation/evidence changes and no runtime state.
