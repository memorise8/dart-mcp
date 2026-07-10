# dart-company-audit-collection - Work Plan

## TL;DR (For humans)
**What you'll get:** dart-search-mcp will be able to export the full OpenDART company list, find a company's annual report, download the source ZIP, identify/extract audit and consolidated-audit XML files, and run a resumable bulk collection that produces TEMIS-importable artifacts.

**Why this approach:** OpenDART access already belongs in dart-search-mcp, while finov2/TEMIS should consume exported files or DB-import inputs. The plan keeps the current one-company TEMIS exporter intact and adds bulk orchestration around safer per-company artifacts and manifests.

**What it will NOT do:** It will not write directly into finov2's production database, scrape DART viewer HTML as the primary source, or require PDF conversion in the first pass.

**Effort:** Medium
**Risk:** Medium - OpenDART ZIP contents vary by filing, and all-company collection needs resumability, API pacing, and clear failure reporting.
**Decisions to sanity-check:** Default collection target should be all corpCode.xml records, with a `--listed-only` switch for listed companies; XML is the first-class artifact; bulk runs must require an explicit output directory.

Your next move: start implementation from this plan, or ask for a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): Medium effort / medium risk plan to add corp list export, audit/consolidated-audit XML extraction, and checkpointed bulk collection to dart-search-mcp without changing finov2 runtime behavior.

## Scope
### Must have
- Add a public MCP tool and CLI command to export `corpCode.xml` company records.
- Export company records with `corp_code`, `corp_name`, `stock_code`, `modify_date`, and derived `is_listed`.
- Support at least JSON output for company export; CSV is allowed for inspection if it does not add dependencies.
- Add a reusable report-document resolver that can accept direct `rcept_no` or resolve `corp_code + bsns_year + reprt_code`.
- Add a ZIP inspection/extraction layer that identifies:
  - `감사보고서`
  - `연결감사보고서`
  - the main report XML
- Add single-company CLI/MCP flow to download and extract audit/consolidated-audit XML into an output directory.
- Add bulk CLI/MCP flow over an explicit company list or corpCode export.
- Bulk flow must write a manifest/checkpoint with attempted, succeeded, failed, skipped, and output path records.
- Preserve current `search_corp_code`, `download_document`, `download_xbrl`, `get_periodic_report`, and `export_temis_topic_cases` behavior.
- Update generated docs and README examples.

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No direct finov2/TEMIS database writes from dart-search-mcp.
- No automatic all-company crawl without explicit user-provided output path and year/report inputs.
- No change to existing command names or existing MCP tool names.
- No required live DART calls in the default test suite.
- No new dependency unless it is justified by a concrete parser/CSV need and lockfile changes are verified.
- No PDF rendering/conversion in this plan.
- No treating missing consolidated-audit XML as a hard failure; it is a per-company `not_found`/`skipped` outcome unless the user explicitly requested `--require-consolidated`.

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD with existing Python unittest style.
- Default tests use fixture corpCode ZIPs, fixture document ZIPs, and mocked DART HTTP transport.
- Evidence: `.omo/evidence/task-<N>-dart-company-audit-collection.<ext>`.
- Common gates:
  - `uv run python -m unittest discover -v`
  - `uv run python -m compileall cli.py server.py dart_search_mcp tests`
  - `uv run dart --help`
  - `uv run dart diagnostics`
- Manual QA gates:
  - Company export happy path with a live API key if available: `uv run dart companies -o /tmp/dart-companies.json --format json --listed-only`.
  - Single-company extraction happy path: direct `rcept_no=20260323001689` into a temp dir and assert `감사보고서` and `연결감사보고서` metadata/files are present.
  - Bulk dry-run/failure path using fixture company list with one valid mocked record and one no-data/missing consolidated-audit record.

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

Wave 1:
- Todo 1: Company list export core, MCP tool, CLI command.
- Todo 2: Document ZIP metadata parser and audit/consolidated-audit classifier.
- Todo 3: Report-document resolver reuse/refactor.

Wave 2:
- Todo 4: Single-company audit document extraction command/tool.
- Todo 5: Bulk collection manifest/checkpoint and resume behavior.
- Todo 6: TEMIS topic-case bulk export adapter using existing one-company core.

Wave 3:
- Todo 7: Documentation generation/README update.
- Todo 8: Live smoke/manual QA evidence and final cleanup.

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 5, 6, 7, 8 | 2, 3 |
| 2 | none | 4, 5, 7, 8 | 1, 3 |
| 3 | none | 4, 5, 8 | 1, 2 |
| 4 | 2, 3 | 5, 7, 8 | 6 after 1 |
| 5 | 1, 2, 3, 4 | 7, 8 | none |
| 6 | 1 | 7, 8 | 4 |
| 7 | 1, 4, 5, 6 | 8 | none |
| 8 | all | final | none |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [ ] 1. Add full company list export
  What to do / Must NOT do:
  - Add pure helper(s) that return all `CorpRecord` values from `corpCode.xml` without requiring a search query.
  - Add MCP tool, recommended name: `export_corp_codes` or `list_companies`.
  - Add CLI command, recommended name: `dart companies`.
  - Options: `--output/-o`, `--format json|csv`, `--listed-only`, optional `--query`.
  - JSON records must include `corp_code`, `corp_name`, `stock_code`, `modify_date`, `is_listed`.
  - Must NOT change `search_corp_code` output or its 20-result display limit.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 5, 6, 7, 8
  References (executor has NO interview context - be exhaustive):
  - `dart_search_mcp/corp.py:21` existing corpCode.xml loader
  - `dart_search_mcp/corp.py:37` existing record shape
  - `dart_search_mcp/corp.py:202` existing search-only MCP tool
  - `cli.py:74` existing search CLI command
  Acceptance criteria (agent-executable):
  - Fixture-based test proves all fixture companies are exported and listed filtering keeps only non-empty `stock_code`.
  - `uv run dart companies --help` exits 0.
  - `uv run python -m unittest tests.test_company_export -v` exits 0.
  QA scenarios (name the exact tool + invocation):
  - Happy: `uv run dart companies -o /tmp/dart-companies.json --format json --listed-only > .omo/evidence/task-1-dart-company-audit-collection.txt`; PASS if output reports a file path and JSON validates.
  - Failure: `uv run dart companies --format xml -o /tmp/bad.xml`; PASS if command exits nonzero and does not create the output file.
  Commit: Y | `feat(corp): export OpenDART company list`

- [ ] 2. Add document ZIP inspection and audit XML classification
  What to do / Must NOT do:
  - Add a pure parser module, suggested `dart_search_mcp/document_zip.py`, that accepts ZIP bytes/path and returns document entries.
  - Extract each XML document's filename, `DOCUMENT-NAME`, ACODE, byte size, and booleans for main report/audit/consolidated audit.
  - Classify `감사보고서` and `연결감사보고서`; prefer `DOCUMENT-NAME`, fall back to ACODE `00760`/`00761` only as a secondary heuristic.
  - Must NOT parse arbitrary DART viewer HTML as the source of truth.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 4, 5, 7, 8
  References:
  - `dart_search_mcp/tools/downloads.py:38` currently opens downloaded ZIP only to list file names
  - Observed fixture candidate: `/tmp/dart_20260323001689/20260323001689.zip` contains `20260323001689_00760.xml` and `20260323001689_00761.xml`
  Acceptance criteria:
  - Fixture ZIP test returns exactly one audit document and one consolidated-audit document for the sample shape.
  - Corrupt ZIP and no-audit ZIP paths return typed errors/results without traceback.
  QA scenarios:
  - Happy: run a small Python driver against a fixture ZIP and write JSON metadata to `.omo/evidence/task-2-dart-company-audit-collection.json`.
  - Failure: run the driver against a text file named `.zip`; PASS if error is controlled and no files are extracted.
  Commit: Y | `feat(downloads): classify audit documents in DART ZIPs`

- [ ] 3. Promote report receipt-number resolution for document workflows
  What to do / Must NOT do:
  - Move or expose `_resolve_rcept_no` as a reusable internal helper with tests.
  - Keep existing `download_xbrl` behavior stable.
  - Add support for resolving the annual report receipt number for document ZIP extraction by `corp_code + bsns_year + reprt_code`.
  - Must NOT rely on company name for final OpenDART calls; resolve to `corp_code` first.
  Parallelization: Wave 1 | Blocked by: none | Blocks: 4, 5, 8
  References:
  - `dart_search_mcp/tools/downloads.py:53` existing `_resolve_rcept_no`
  - `dart_search_mcp/tools/downloads.py:84` current `list.json` request params
  - `dart_search_mcp/tools/downloads.py:135` `download_xbrl` already consumes the resolver
  Acceptance criteria:
  - Tests cover direct `rcept_no`, successful annual-report resolution, no matching report, and invalid year.
  - Existing `download_xbrl` tests still pass.
  QA scenarios:
  - Happy: mocked `list.json` returns a 사업보고서 record; resolver returns the expected `rcept_no`.
  - Failure: mocked empty list returns a clear not-found result.
  Commit: Y | `refactor(downloads): reuse report receipt resolution`

- [ ] 4. Add single-company audit document extraction
  What to do / Must NOT do:
  - Add MCP tool, recommended name: `extract_audit_documents`.
  - Add CLI command, recommended name: `dart audit-documents`.
  - Inputs: direct `--rcept-no`, or `--code` + `--year` + `--report`; optional `--corp` only if it resolves uniquely.
  - Outputs: write a metadata JSON manifest and extracted XML files under an explicit output directory.
  - Options: `--include audit|consolidated|both`, `--require-consolidated`.
  - Must NOT overwrite unrelated files; write under a per-receipt subdirectory or deterministic file names.
  Parallelization: Wave 2 | Blocked by: 2, 3 | Blocks: 5, 7, 8
  References:
  - `dart_search_mcp/tools/downloads.py:14` existing `download_document`
  - `dart_search_mcp/tools/downloads.py:33` current output directory behavior
  - `cli.py:224` existing `dart download` command
  Acceptance criteria:
  - Fixture test extracts audit/consolidated XML files and writes manifest with document names.
  - Missing consolidated audit returns skipped/not-found unless `--require-consolidated` is set.
  - `uv run dart audit-documents --help` exits 0.
  QA scenarios:
  - Happy: `uv run dart audit-documents --rcept-no 20260323001689 -o /tmp/dart-audit-docs --include both > .omo/evidence/task-4-dart-company-audit-collection.txt`; PASS if manifest lists both `감사보고서` and `연결감사보고서`.
  - Failure: run with a fixture ZIP containing only a main report and `--require-consolidated`; PASS if command exits nonzero with a clear error and leaves no partial consolidated file.
  Commit: Y | `feat(downloads): extract audit and consolidated-audit documents`

- [ ] 5. Add checkpointed bulk audit document collection
  What to do / Must NOT do:
  - Add bulk CLI command, recommended name: `dart bulk-audit-documents`.
  - Add MCP tool for the same operation only if long-running MCP calls are acceptable in this repo's pattern; otherwise document CLI-only for bulk and keep MCP single-company.
  - Inputs: `--companies-json`, or `--listed-only` to load corpCode.xml directly; required `--year`, `--report`, `--output`.
  - Add `--limit`, `--resume`, `--sleep-seconds`, and `--require-consolidated`.
  - Manifest/checkpoint must record each company status: `pending`, `succeeded`, `failed`, `skipped_no_report`, `skipped_no_consolidated`.
  - Must NOT restart successful companies when `--resume` is used.
  Parallelization: Wave 2 | Blocked by: 1, 2, 3, 4 | Blocks: 7, 8
  References:
  - `dart_search_mcp/corp.py:21` company source
  - `dart_search_mcp/tools/downloads.py:53` receipt resolver
  - `dart_search_mcp/tools/downloads.py:14` document download source
  - `dart_search_mcp/tools/temis.py:232` existing one-company export limitation to avoid repeating
  Acceptance criteria:
  - Offline test with three fixture companies proves success, no-report skip, and retryable failure are all represented in manifest.
  - Resume test proves already succeeded companies are not reprocessed.
  - `uv run dart bulk-audit-documents --help` exits 0.
  QA scenarios:
  - Happy: fixture-backed dry run writes manifest and two extracted XML files under `.omo/evidence/task-5-dart-company-audit-collection/`.
  - Failure: interrupted/partial checkpoint fixture followed by `--resume`; PASS if only pending/failed companies are attempted.
  Commit: Y | `feat(audit): collect audit documents in bulk`

- [ ] 6. Add bulk TEMIS topic-case export without weakening one-company semantics
  What to do / Must NOT do:
  - Add a bulk wrapper around `export_temis_topic_cases_core` that writes one JSON file per company or a combined JSON plus manifest.
  - Prefer one file per company plus an aggregate manifest to avoid the current overwrite semantics.
  - Use `case_id` stability from existing converter; do not invent new identifiers.
  - Must NOT modify existing `temis-topic-cases` overwrite contract.
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 7, 8
  References:
  - `dart_search_mcp/tools/temis.py:151` one-company core
  - `dart_search_mcp/tools/temis.py:202` overwrite write mode
  - `dart_search_mcp/tools/temis.py:232` documented one-company boundary
  - `cli.py:267` existing `temis-topic-cases` command
  Acceptance criteria:
  - Tests prove per-company outputs are isolated and a failure for one company does not delete successful outputs for previous companies.
  - Existing `temis-topic-cases` tests remain green.
  QA scenarios:
  - Happy: run bulk with a two-company mocked fixture and verify manifest has two output JSON paths.
  - Failure: run with one ambiguous company name and one valid corp code; PASS if valid corp code output remains and ambiguous company is recorded as failed.
  Commit: Y | `feat(temis): add bulk topic-case export wrapper`

- [ ] 7. Update generated docs and README usage
  What to do / Must NOT do:
  - Update `scripts/generate_docs.py` if it derives docs from runtime command/tool lists.
  - Regenerate `docs/cli.md` and `docs/tools.md`.
  - Update README with the new recommended flows:
    - export companies
    - extract one company's audit/consolidated-audit XML
    - run checkpointed bulk collection
    - produce TEMIS bulk topic-case artifacts
  - Must NOT describe unsupported DB import or production automation as complete in this repo.
  Parallelization: Wave 3 | Blocked by: 1, 4, 5, 6 | Blocks: 8
  References:
  - `docs/cli.md:1` generated CLI docs
  - `docs/tools.md:1` generated MCP docs
  - `README.md:114` current tool overview
  - `README.md:154` current TEMIS audit workflow
  Acceptance criteria:
  - `uv run python scripts/generate_docs.py` exits 0 if that is the established docs path.
  - Docs mention the new commands/tools and accurately state bulk boundaries.
  QA scenarios:
  - Happy: `uv run dart --help > .omo/evidence/task-7-dart-company-audit-collection-help.txt`; PASS if new commands appear.
  - Failure/docs guard: search docs for "production DB" or "자동 운영 반영" claims; PASS if no unsupported production-write claim exists.
  Commit: Y | `docs(audit): document company and audit collection workflows`

- [ ] 8. Final verification and live smoke
  What to do / Must NOT do:
  - Run full tests, compileall, CLI help, diagnostics.
  - Run live smoke only if `DART_API_KEY`/`dart_api` is configured; otherwise record as skipped with reason.
  - Live smoke target:
    - `uv run dart companies --listed-only --limit 5 -o /tmp/dart-companies-smoke.json` if `--limit` exists for export; otherwise use a temp JSON and inspect first 5 with a script.
    - `uv run dart audit-documents --rcept-no 20260323001689 -o /tmp/dart-audit-smoke --include both`.
  - Must NOT commit `/tmp` or generated bulk output artifacts.
  Parallelization: Wave 3 | Blocked by: all | Blocks: final
  References:
  - all changed files
  Acceptance criteria:
  - `uv run python -m unittest discover -v` exits 0.
  - `uv run python -m compileall cli.py server.py dart_search_mcp tests` exits 0.
  - Manual QA evidence files exist and show pass/skip status.
  QA scenarios:
  - Happy: real or fixture-backed single-company extraction proves both XML types can be found.
  - Failure: invalid `rcept_no` or corrupt fixture produces a controlled error.
  Commit: Y | `chore(audit): verify company audit collection workflow`

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit: verify every Must Have has a completed todo, every Must NOT Have remains true, and new tools/commands are documented.
- [ ] F2. Code quality review: review parser boundaries, file writes, overwrite safety, API key redaction, and long-running bulk behavior.
- [ ] F3. Real manual QA: run the CLI surfaces with a fixture or live API key and attach evidence under `.omo/evidence/`.
- [ ] F4. Scope fidelity: confirm finov2 DB import/deploy was not added to dart-search-mcp and remains a downstream step.

## Commit strategy
- Use small commits by feature boundary:
  - company export
  - document ZIP classification
  - single-company extraction
  - bulk collection
  - TEMIS bulk wrapper
  - docs/final verification
- Do not mix generated bulk output artifacts into commits.
- Before each commit, run the targeted test for that boundary; before final commit, run the full verification strategy.

## Refinements from live probe (2026-07, must fold into implementation)
> Confirmed by a live OpenDART run collecting 1 year of 감사보고서/연결감사보고서/사업보고서 (48,537 records).
- **3-month date cap:** without `corp_code`, OpenDART `list.json` rejects ranges >3 months (`status 100: "corp_code가 없는 경우 검색기간은 3개월만 가능합니다"`). Bulk/date-range collection MUST split into ≤3-month windows (e.g. quarterly) and paginate each; per-company (corp_code) queries are not subject to this cap.
- **Disclosure-type + name filter, not scraping:** `dsab007/main.do` website search == `list.json?pblntf_ty=...`. Audit reports = `pblntf_ty=F` (외부감사관련); 사업보고서 = `pblntf_ty=A` (정기공시). Narrow to targets by `report_name` substring: `"감사보고서"` catches 감사보고서 + 연결감사보고서 (+`[기재정정]` variants); `"사업보고서"` catches 사업보고서 (+`[첨부추가]`/`[기재정정]`). Distinguish 연결 by `"연결감사보고서" in report_name`. NO viewer HTML scraping.
- **Retry on returned errors, not just exceptions:** `search_disclosures_structured` can RETURN a `DisclosureSearchError` (transient DART status) without raising. The collector MUST retry these (the scratch prototype skipped 3 pages / ~200 records by only retrying exceptions). Retry N times with backoff; only then record the page as failed in the manifest.
- **Dedupe by `rcept_no`; pagination via `total_page`:** use `page_count=100`, loop `page_no` 1..`total_page` per window, dedupe across windows/types by `rcept_no`.
- **Volume/seasonality:** audit disclosures concentrate around the March annual-report deadline (Q1-2026 F=7,801, Q2-2026 F=34,878 vs Q3/Q4-2025 ~1,040/635). Expect tens of thousands of `list.json` records per year → hundreds of paginated calls; pace requests (~0.15s) and checkpoint so a run is resumable.
- **정정본 handling:** `[기재정정]`/`[첨부추가]` variants are distinct `rcept_no`s and are kept by default; add an opt-in filter to exclude corrections if the user wants originals only.
- **Record fields available at list level:** `report_name, rcept_no, rcept_dt, corp_code, corp_name, stock_code, corp_cls, source_url, flr_nm (감사인/제출인), remark` — the auditor is already present in the list response (no per-document fetch needed just to know the auditor).
- **Sequencing per user:** Step 1 = reusable disclosure collector (windowed+paginated+filtered+deduped+checkpointed manifest). Step 2 = ZIP/XML extraction over the manifest's `rcept_no`s (Todos 2/4, then bulk 5).

## Success criteria
- Users can export OpenDART company records from dart-search-mcp without using private helpers.
- Users can provide a `rcept_no` like `20260323001689` and extract both `감사보고서` and `연결감사보고서` XML when present.
- Users can resolve a company's annual report from `corp_code + year + report_code` and run the same extraction.
- Bulk runs are resumable and produce a manifest that distinguishes success, no report, no consolidated audit, and errors.
- Existing single-company TEMIS topic-case export behavior remains compatible.
- Tests pass, docs are updated, and manual QA evidence proves the workflow through the CLI surface.
