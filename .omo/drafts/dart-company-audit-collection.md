---
slug: dart-company-audit-collection
status: plan-written
intent: clear
pending-action: start implementation or run high-accuracy review
approach: Add MCP/CLI surfaces in dart-search-mcp for exporting the full corpCode.xml company list, resolving annual report receipt numbers, extracting audit/consolidated-audit XML documents from OpenDART document ZIPs, and running checkpointed bulk collection that emits importable artifacts for TEMIS/finov2.
---

# Draft: dart-company-audit-collection

## Components (topology ledger)
<!-- Lock the SHAPE before depth. One row per top-level component that can succeed or fail independently. -->
<!-- id | outcome (one line) | status: active|deferred | evidence path -->
1 | Company universe export from corpCode.xml with filters and JSON/CSV output | active | dart_search_mcp/corp.py:21
2 | Annual report receipt/document ZIP resolution for a corp/year/report | active | dart_search_mcp/tools/downloads.py:14
3 | Audit and consolidated-audit XML identification/extraction from ZIP contents | active | observed /tmp/dart_20260323001689/20260323001689.zip with 00760/00761 XML files
4 | Bulk company audit collection with checkpoint/retry/rate-limit and TEMIS import artifacts | active | dart_search_mcp/tools/temis.py:151

## Open assumptions (announced defaults)
<!-- Record any default you adopt instead of asking, so the user can veto it at the gate. -->
<!-- assumption | adopted default | rationale | reversible? -->
Target universe | Default to all corpCode.xml records, with `--listed-only` filtering to `stock_code != ""` | "전체 회사 리스트" should mean the OpenDART company-code universe, but listed-only is the practical first audit backfill filter | yes
Output formats | JSON first, CSV optional for company list only | Downstream TEMIS import and existing topic-case flow are JSON-based; CSV is useful for manual inspection | yes
Bulk persistence | Write per-company artifacts plus a manifest/checkpoint, not one huge overwritten file | Existing TEMIS exporter overwrites one company file; bulk needs resumability and failure isolation | yes
Consolidated audit detection | Identify by XML `DOCUMENT-NAME` containing `연결감사보고서` or document ACODE `00761` | Verified in the downloaded sample ZIP; can be tested offline with fixture ZIPs | yes
Execution safety | No production finov2 writes from MCP bulk commands | Existing boundary says MCP exports source artifacts and finov2 imports them separately | yes

## Findings (cited - path:lines)
- `dart_search_mcp/corp.py:21` downloads and parses OpenDART `corpCode.xml`; `dart_search_mcp/corp.py:37` builds records with `corp_code`, `corp_name`, `stock_code`, and `modify_date`.
- `dart_search_mcp/corp.py:202` exposes only `search_corp_code`; `dart_search_mcp/corp.py:234` limits displayed matches to the first 20, so there is no full-list export surface yet.
- `cli.py:74` has only the company-name search command for corp codes; no `companies`/`export-companies` command exists.
- `dart_search_mcp/tools/downloads.py:14` already downloads a document ZIP by `rcept_no` through OpenDART `document.xml`.
- `dart_search_mcp/tools/downloads.py:53` already resolves a report `rcept_no` from `corp_code`, `bsns_year`, and `reprt_code` for XBRL convenience; this can be promoted/reused for document ZIP workflows.
- `dart_search_mcp/tools/temis.py:151` exports TEMIS topic cases for exactly one company/year/report and overwrites `output_path`; `dart_search_mcp/tools/temis.py:232` documents the one-company boundary.
- Manual probe of `uv run dart download 20260323001689 -o /tmp/dart_20260323001689` succeeded and produced a ZIP containing `20260323001689_00760.xml` (`감사보고서`) and `20260323001689_00761.xml` (`연결감사보고서`).

## Decisions (with rationale)
- Add the new features to `dart-search-mcp`, not finov2, because OpenDART access and corpCode parsing already live in this repo and finov2 should consume exported artifacts.
- Keep existing tool/command names compatible; add new names instead of changing `search`, `download`, or `temis-topic-cases`.
- Split pure parsing/extraction helpers from network/file-writing adapters so most tests use fixture ZIPs and do not require a live DART API key.
- Treat bulk collection as an opt-in CLI/MCP operation with checkpointing; do not automatically crawl every company during normal commands or tests.
- Emit a manifest for bulk runs that records attempted/succeeded/failed/skipped companies, output paths, and failure messages.

## Scope IN
- Full company list export from `corpCode.xml` to JSON and optionally CSV.
- Filters for all records vs listed-only records, and optional name/stock-code filters if low-risk.
- Single-company annual report ZIP acquisition by `corp_code + bsns_year + reprt_code` or direct `rcept_no`.
- Audit XML extraction from downloaded ZIPs into separate files and/or structured metadata.
- Consolidated-audit XML extraction when present.
- Bulk collection over an explicit input company list or corpCode export with checkpoint/resume.
- Documentation updates for README, generated CLI docs, and MCP tool docs.

## Scope OUT (Must NOT have)
- No finov2 database writes from this repo.
- No automatic production deployment.
- No browser scraping of DART viewer pages as the primary path; use OpenDART APIs and downloaded ZIPs first.
- No PDF conversion requirement in the first pass; XML is the source artifact.
- No unbounded all-company run without an explicit output directory and checkpoint.

## Open questions
None blocking. User can later choose whether the first bulk run should target all corpCode records or listed-only companies; the implementation should support both.

## Approval gate
status: approved-by-initial-request
<!-- When exploration is exhausted and unknowns are answered, set status: awaiting-approval. -->
<!-- That durable record is the loop guard: on a later turn read it and resume at the gate instead of re-running exploration. -->
