---
slug: temis-dart-mcp-hardening
status: approved-for-plan-file
intent: clear
pending-action: none
approach: Harden dart-search-mcp as the OpenDART source adapter for TEMIS without changing finov2 in this repo-local plan.
---

# Draft: temis-dart-mcp-hardening

## Components (topology ledger)
| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 | Current MCP/CLI behavior remains usable while structured internals are added. | active | `.omo/evidence/task-1-temis-dart-mcp-hardening.txt` |
| C2 | OpenDART HTTP layer returns typed data/errors and never leaks `crtfc_key` in logs or persisted artifacts. | active | `.omo/evidence/task-2-temis-dart-mcp-hardening.txt` |
| C3 | Company lookup and disclosure search use `corp_code` as the canonical key. | active | `.omo/evidence/task-3-temis-dart-mcp-hardening.txt` |
| C4 | TEMIS-compatible DART topic case JSON can be generated from OpenDART records so finov2 does not need a separate DART adapter. | active | `.omo/evidence/task-6-temis-dart-mcp-hardening.json` |
| C5 | Docs, generated tool docs, and QA scripts explain the TEMIS ingestion path without requiring a live key in default tests. | active | `.omo/evidence/task-9-temis-dart-mcp-hardening.txt` |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| Public compatibility | Keep all existing MCP tool and CLI command names working; add opt-in structured/TEMIS commands instead of replacing string output. | Existing tests pin the public surface and other clients may rely on human-readable strings. | Yes |
| Default test posture | No live OpenDART call in default tests; live smoke is opt-in when `DART_API_KEY` is present. | CI/local QA must be deterministic and secret-free. | Yes |
| TEMIS schema target | Mirror `../../finov2/fino-backend/app/schemas/dart_topic_search.py` for generated JSON. | Current TEMIS backend already validates this shape. | Yes |
| First useful source | Use periodic report audit/auditor fields and downloaded documents before attempting NICE or valuation work. | It maps to Layer 1 DART case search and avoids Layer 3 scope creep. | Yes |
| finov2 integration boundary | Make `dart-search-mcp` own the OpenDART adapter role and export `dart_topic_cases.json`; finov2 only points `DART_TOPIC_CASES_PATH` at that file. | The current finov2 backend already consumes a JSON file, so duplicating OpenDART adapter logic inside finov2 would increase coupling and maintenance. | Yes |

## Findings (cited - path:lines)
- `README.md:1` describes this repo as a DART Open API MCP/CLI wrapper.
- `README.md:88` documents 16 MCP tools covering disclosure search, company info, financials, indicators, ownership, reports, downloads, XBRL, and taxonomy.
- `pyproject.toml:1` declares package `dart-search-mcp` with dependencies `fastmcp`, `httpx`, `python-dotenv`, and `click`.
- `dart_search_mcp/client.py:8` centralizes JSON OpenDART calls but returns either `dict` or error `str`.
- `dart_search_mcp/client.py:13` appends `crtfc_key` into query params; live CLI output showed httpx request logs include the query string unless logging is suppressed/redacted.
- `dart_search_mcp/corp.py:19` downloads and caches `corpCode.xml`, then `search_corp_code` formats matches as strings.
- `dart_search_mcp/tools/disclosures.py:8` exposes `search_disclosures`; live evidence showed `corp_name` should not be trusted as a DART list filter, while `corp_code` works.
- `dart_search_mcp/tools/reports.py:8` exposes periodic report APIs including `report_type="회계감사인"`; live evidence returned auditor and `core_adt_matter` data for Samsung Electronics.
- `dart_search_mcp/tools/downloads.py:8` supports original disclosure ZIP and XBRL ZIP downloads.
- `tests/test_public_surface.py:7` already pins expected MCP tool names and CLI command names.
- `../../finov2/fino-backend/app/schemas/dart_topic_search.py:10` defines the TEMIS `DartTopicCase` fields this repo should export toward.

## Decisions (with rationale)
- Add structured internals first, not a breaking public rewrite. Existing MCP/CLI string outputs stay as compatibility wrappers.
- Treat `corp_code` as the canonical disclosure-search key. Company-name lookup becomes a separate resolution step.
- Redact or suppress OpenDART request logging before any live-smoke or ingestion workflow is considered acceptable.
- Generate a TEMIS-compatible JSON artifact instead of planning a finov2 OpenDART adapter. This keeps repo boundaries clean: this repo owns collection/transformation, while finov2 consumes the generated file through `DART_TOPIC_CASES_PATH`.
- If later runtime orchestration is needed, keep it to a thin job/config layer that invokes this MCP/export command; do not duplicate OpenDART request/parsing logic in finov2.
- Keep Layer 3/NICE out of this plan. This is a Layer 1 DART source hardening plan.

## Scope IN
- `dart-search-mcp` code, tests, docs, QA scripts, generated docs, and `.omo/evidence`.
- Structured wrappers around existing OpenDART calls.
- TEMIS-compatible topic-case export contract and fixture generation.
- Explicit adapter boundary documentation: `dart-search-mcp` is the DART adapter/exporter; finov2 is a JSON consumer.
- Optional live smoke commands that require `DART_API_KEY` but never expose the key.
- Compatibility validation against the current finov2 `DartTopicCase` schema by mirroring the required fields.

## Scope OUT (Must NOT have)
- No production finov2 code changes in this repo.
- No finov2-side OpenDART adapter implementation in this plan.
- No NICE ingestion, valuation, or Layer 3 report implementation.
- No default tests that call `opendart.fss.or.kr`.
- No committed secrets, `.env`, `.omc`, `.venv`, downloaded ZIPs, or generated disclosure payloads outside `.omo/evidence` examples.
- No public MCP/CLI command removal.
- No API key exposure in logs, errors, docs, evidence, or test output.

## Open questions
- None blocking. The user explicitly asked for a plan file in this folder; this draft records the adopted defaults for the next session to review.

## Approval gate
status: approved-for-plan-file
approval source: User asked "해당 폴더에 플랜 파일을 하나 만들어줘"; this authorizes writing the plan artifact only, not implementation.
