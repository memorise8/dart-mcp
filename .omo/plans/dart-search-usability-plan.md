# Make dart-search-mcp Practical To Use

## TL;DR
> Summary:      Make the existing DART MCP server and CLI discoverable, testable, installable, and maintainable without changing the current public MCP tool names or replacing the human-readable string output contract.
> Deliverables:
> - Contract tests for the current MCP tool list, CLI help surface, formatter behavior, and no-network validation paths
> - Reliable local CLI execution path for `dart`
> - Offline-safe diagnostics command for API key/package/tool visibility
> - Responsibility-based module split of the 1,966-line `server.py`
> - README updated to match all implemented MCP tools and CLI commands
> - Generated or script-verifiable tool/command documentation checks
> Effort:       Medium
> Risk:         Medium - current behavior is broad, string-formatted, and untested; refactor must be characterization-first.

## Scope
### Must Have
- Preserve all 16 existing MCP tool names:
  `search_disclosures`, `get_company_info`, `search_corp_code`, `get_financial_statements`, `get_financial_statements_full`, `get_multi_company_financials`, `get_financial_indicators`, `get_multi_company_indicators`, `get_major_shareholders_report`, `get_executive_stock_report`, `get_periodic_report`, `get_major_event_report`, `download_document`, `download_xbrl`, `get_xbrl_taxonomy`, `get_securities_report`.
- Preserve existing CLI command names shown by `uv run python cli.py --help`.
- Use offline tests for default verification. Do not require a real DART API key for CI or local smoke tests.
- Keep current human-readable string outputs as the default public contract.
- Keep `.env` / `.env.*` ignored and never commit secrets.
- Make `README.md` reflect actual capabilities, not aspirational capabilities.
- Keep every implementation todo paired with a test written before the production change.

### Must NOT Have
- No web UI.
- No database or persistent cache.
- No required live DART API calls in the normal test suite.
- No dependency expansion unless a todo names the reason and validates the lockfile.
- No broad behavior rewrite while modularizing.
- No structured JSON output in this first Practical pass unless explicitly added as opt-in and covered by contract tests.
- No renaming of MCP tools or CLI commands unless the plan is amended.

## Verification Strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD with Python `unittest` unless the executor explicitly chooses to add `pytest` and updates `pyproject.toml` plus `uv.lock` with a justification.
- QA policy: every todo includes a CLI, module, or MCP-introspection scenario that the agent must actually run.
- Evidence: write command transcripts under `.omo/evidence/task-<N>-<slug>.txt`.
- Common local gate after each wave:
  - `uv run python -m unittest discover -v`
  - `uv run python -m compileall server.py cli.py dart_search_mcp tests`
  - `uv run python cli.py --help`
  - `uv run python - <<'PY'\nimport asyncio\nfrom server import mcp\nasync def main():\n    tools = await mcp.list_tools()\n    print(len(tools))\n    print('\\n'.join(t.name for t in tools))\nasyncio.run(main())\nPY`
- Manual QA surfaces:
  - CLI channel: `uv run python cli.py --help`, `uv run python cli.py diagnostics`, invalid-command and invalid-input paths.
  - MCP/library channel: Python driver imports `server.mcp` and calls `list_tools()`.
  - Packaging channel: `uv run dart --help` or the final selected equivalent documented command.

## Execution Strategy
### Parallel Execution Waves
> Target 5-8 todos per wave. This repo is small, but dependencies are sequential because tests must pin behavior before refactor.

Wave 1 (no deps):
- Todo 1: Add characterization test harness and current surface contract tests.
- Todo 2: Fix packaging and local command execution path.
- Todo 3: Add diagnostics command and tests.

Wave 2 (after Wave 1):
- Todo 4: Extract configuration and DART HTTP client boundary.
- Todo 5: Extract formatting helpers and registry constants.
- Todo 6: Extract download/file-output behavior.

Wave 3 (after Wave 2):
- Todo 7: Split MCP tool registration into package modules while preserving import compatibility.
- Todo 8: Align CLI with package modules and reduce repetitive wrappers.
- Todo 9: Generate or verify tool/command docs from runtime metadata.

Wave 4 (after Wave 3):
- Todo 10: Rewrite README to match the actual product surface.
- Todo 11: Add end-to-end offline QA scripts or documented command recipes.
- Todo 12: Final verification, review, and cleanup.

Critical path:
Todo 1 -> Todo 4/5/6 -> Todo 7 -> Todo 8 -> Todo 9 -> Todo 10 -> Todo 12

### Dependency Matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 4, 5, 6, 7, 8, 9, 10, 12 | 2, 3 |
| 2 | none | 10, 11, 12 | 1, 3 |
| 3 | none | 10, 11, 12 | 1, 2 |
| 4 | 1 | 7, 8, 12 | 5, 6 |
| 5 | 1 | 7, 8, 9, 12 | 4, 6 |
| 6 | 1 | 7, 8, 12 | 4, 5 |
| 7 | 4, 5, 6 | 8, 9, 10, 12 | none |
| 8 | 7 | 9, 10, 11, 12 | none |
| 9 | 7, 8 | 10, 12 | 11 |
| 10 | 2, 3, 9 | 12 | 11 |
| 11 | 2, 3, 8 | 12 | 9, 10 |
| 12 | all | final | none |

## Todos
> Implementation + Test = ONE todo. Never separate implementation and tests.

- [x] 1. Add characterization tests for the current public surface
  What to do / Must NOT do:
  - Add `tests/` using `unittest`.
  - Test that `server.mcp.list_tools()` returns exactly the 16 current tool names.
  - Test `uv run python cli.py --help` equivalent through Click `CliRunner` and assert all 17 commands are listed.
  - Test `_format_date`, `_format_amount`, and `_format_generic_response` for current formatting behavior.
  - Test missing required arguments for a few no-network tool functions, such as `get_financial_statements("", "2024")`, `get_periodic_report("00126380", "2024", report_type="")`, and `download_xbrl()`.
  - Must NOT contact `opendart.fss.or.kr` in these tests.
  Parallelization: Can parallel Y | Wave 1 | Blocks refactor todos
  References:
  - `server.py:22` FastMCP instance
  - `server.py:188` `_format_date`
  - `server.py:208` `_format_amount`
  - `server.py:225` `_format_generic_response`
  - `server.py:291` first MCP tool registration
  - `server.py:1848` final MCP tool registration
  - `cli.py:40` Click group
  - `cli.py:47` first CLI command
  - `cli.py:240` `serve` command
  Acceptance criteria:
  - `uv run python -m unittest tests.test_public_surface -v` exits 0.
  - `uv run python -m unittest discover -v` exits 0.
  QA scenarios:
  - CLI: `uv run python cli.py --help > .omo/evidence/task-1-cli-help.txt`; PASS if output includes `download-xbrl`, `securities`, and `serve`.
  - MCP/library: run the `server.mcp.list_tools()` Python driver above into `.omo/evidence/task-1-mcp-tools.txt`; PASS if first line is `16`.
  Commit: Y | `test(surface): characterize current MCP and CLI contracts` | Files: `tests/test_public_surface.py`

- [x] 2. Make the declared `dart` command reliably executable
  What to do / Must NOT do:
  - Reproduce `uv run dart --help` failure first and capture it.
  - Decide the smallest packaging fix. Likely options: add package metadata for `py_modules = ["cli", "server"]`, restructure into a package, or document `uv run python cli.py` if console script is intentionally unavailable before install.
  - Preferred outcome: `uv run dart --help` works from a fresh `uv sync` workspace because `pyproject.toml:14` already declares it.
  - Must NOT remove `uv run python cli.py` compatibility.
  Parallelization: Can parallel Y | Wave 1 | Blocks docs
  References:
  - `pyproject.toml:1` project metadata
  - `pyproject.toml:14` script declaration
  - `cli.py:40` CLI entrypoint
  Acceptance criteria:
  - A test or scripted check fails before the packaging fix and passes after it.
  - `uv run dart --help` exits 0 and lists all commands.
  - `uv run python cli.py --help` still exits 0.
  QA scenarios:
  - CLI: `uv run dart --help > .omo/evidence/task-2-dart-help.txt`; PASS if output starts with `Usage:` and includes `Commands:`.
  - CLI regression: `uv run python cli.py --help > .omo/evidence/task-2-python-cli-help.txt`; PASS if command list matches Task 1.
  Commit: Y | `fix(cli): make dart console command executable locally` | Files: `pyproject.toml`, `uv.lock` if changed, optional package files

- [x] 3. Add offline diagnostics for configuration and surface discovery
  What to do / Must NOT do:
  - Add a `diagnostics` CLI command that checks: Python version, package version, whether `DART_API_KEY` or `dart_api` is set, MCP tool count, CLI command count, and DART base URL.
  - Diagnostics must not print the API key value.
  - Diagnostics must not call the live DART API.
  - Add tests for env present/missing using isolated environment patching.
  Parallelization: Can parallel Y | Wave 1 | Blocks docs and QA scripts
  References:
  - `server.py:24` API key env lookup
  - `server.py:27` DART base URL
  - `cli.py:40` CLI group
  Acceptance criteria:
  - `uv run python -m unittest tests.test_diagnostics -v` exits 0.
  - `uv run python cli.py diagnostics` exits 0 without `DART_API_KEY` set and says the key is missing without exposing secrets.
  QA scenarios:
  - CLI missing-key: `env -u DART_API_KEY -u dart_api uv run python cli.py diagnostics > .omo/evidence/task-3-diagnostics-missing-key.txt`; PASS if output contains `missing` or Korean equivalent and does not contain any secret value.
  - CLI present-key: `DART_API_KEY=redacted-test-key uv run python cli.py diagnostics > .omo/evidence/task-3-diagnostics-present-key.txt`; PASS if output says configured and does not include `redacted-test-key`.
  Commit: Y | `feat(cli): add offline diagnostics command` | Files: `cli.py`, `tests/test_diagnostics.py`

- [x] 4. Extract configuration and DART HTTP client boundary
  What to do / Must NOT do:
  - Introduce a package module such as `dart_search_mcp/config.py` and `dart_search_mcp/dart_client.py`.
  - Move API key resolution, base URL, `_fetch_dart`, `_fetch_dart_binary`, and corp-code XML loading behind injectable functions/classes.
  - Existing public tool functions must still be importable from `server.py`.
  - Tests must fake HTTP at the boundary; do not call real DART.
  - Must NOT change user-facing error strings unless tests are updated intentionally.
  Parallelization: Can parallel Y | Wave 2 | Blocks tool modularization
  References:
  - `server.py:24` global API key
  - `server.py:27` base URL
  - `server.py:129` `_fetch_dart`
  - `server.py:162` `_fetch_dart_binary`
  - `server.py:512` `_load_corp_codes`
  Acceptance criteria:
  - Characterization tests from Todo 1 remain green.
  - New tests cover DART status `013`, non-`000` status, timeout, and binary JSON error response.
  QA scenarios:
  - Module: `uv run python - <<'PY'\nfrom server import API_KEY, BASE_URL\nprint(BASE_URL)\nprint(type(API_KEY).__name__)\nPY > .omo/evidence/task-4-compat-imports.txt`; PASS if import works and base URL is unchanged.
  Commit: Y | `refactor(client): isolate DART API boundary` | Files: `server.py`, `dart_search_mcp/config.py`, `dart_search_mcp/dart_client.py`, tests

- [x] 5. Extract registries and formatters with tests
  What to do / Must NOT do:
  - Move report registries to `dart_search_mcp/registries.py`.
  - Move `_format_date`, `_default_date_range`, `_format_amount`, and `_format_generic_response` to `dart_search_mcp/formatting.py`.
  - Preserve import compatibility from `server.py` for CLI imports if needed.
  - Add tests for registry counts and a representative invalid report type message.
  Parallelization: Can parallel Y | Wave 2 | Blocks docs generation
  References:
  - `server.py:30` periodic registry
  - `server.py:67` major event registry
  - `server.py:111` securities registry
  - `server.py:188` format helpers
  - `server.py:1396` periodic tool uses registry
  - `server.py:1497` major event tool uses registry
  - `server.py:1848` securities tool uses registry
  Acceptance criteria:
  - Registry count tests confirm 27 periodic types, 36 event types, and 6 securities types.
  - Public imports used by `cli.py:16` still work.
  QA scenarios:
  - Module: `uv run python - <<'PY'\nfrom server import PERIODIC_REPORT_REGISTRY, MAJOR_EVENT_REGISTRY, SECURITIES_REGISTRATION_REGISTRY\nprint(len(PERIODIC_REPORT_REGISTRY), len(MAJOR_EVENT_REGISTRY), len(SECURITIES_REGISTRATION_REGISTRY))\nPY > .omo/evidence/task-5-registry-counts.txt`; PASS if output is `27 36 6`.
  Commit: Y | `refactor(core): extract registries and formatting helpers` | Files: `server.py`, `dart_search_mcp/registries.py`, `dart_search_mcp/formatting.py`, tests

- [x] 6. Extract download/file-output behavior safely
  What to do / Must NOT do:
  - Move `download_document`, `_resolve_rcept_no`, and `download_xbrl` file-writing internals to a module such as `dart_search_mcp/downloads.py`.
  - Use `tempfile.TemporaryDirectory` in tests.
  - Tests must fake binary responses and verify file names, bytes written, and returned message.
  - Must NOT write files outside the requested output directory.
  Parallelization: Can parallel Y | Wave 2 | Blocks tool modularization
  References:
  - `server.py:1602` `download_document`
  - `server.py:1647` `_resolve_rcept_no`
  - `server.py:1732` `download_xbrl`
  Acceptance criteria:
  - Tests cover direct `rcept_no`, missing `rcept_no` plus missing corp/year, and binary ZIP write.
  - No test artifact remains after test completion.
  QA scenarios:
  - CLI/library: run a test driver that fakes binary download into a temp dir and writes transcript to `.omo/evidence/task-6-download-tempdir.txt`; PASS if temp dir contains only expected file during the context and is removed afterward.
  Commit: Y | `refactor(downloads): isolate DART file download behavior` | Files: `server.py`, `dart_search_mcp/downloads.py`, tests

- [x] 7. Split MCP tool registration into package modules while preserving `server.py`
  What to do / Must NOT do:
  - Create a package layout such as:
    - `dart_search_mcp/server_app.py` for `mcp = FastMCP(...)`
    - `dart_search_mcp/tools/disclosures.py`
    - `dart_search_mcp/tools/financials.py`
    - `dart_search_mcp/tools/reports.py`
    - `dart_search_mcp/tools/downloads.py`
  - Keep `server.py` as a thin compatibility entrypoint that imports/re-exports public functions and runs `mcp.run()` under `if __name__ == "__main__"`.
  - The 16 tool names from Todo 1 must remain identical.
  - Must NOT introduce circular imports between CLI and server modules.
  Parallelization: Can parallel N | Wave 3 | Blocks CLI alignment and docs generation
  References:
  - `server.py:22` FastMCP app
  - `server.py:291` disclosures tool
  - `server.py:618` financial statements tools begin
  - `server.py:1221` ownership tools begin
  - `server.py:1396` report dispatch tools begin
  - `server.py:1602` download tools begin
  Acceptance criteria:
  - `uv run python server.py` still starts the MCP server when manually launched for a short smoke check and then terminated.
  - `server.mcp.list_tools()` still reports 16 tools.
  - `from server import search_disclosures, get_company_info, get_periodic_report` still works.
  QA scenarios:
  - MCP/library: `uv run python - <<'PY'\nimport asyncio\nimport server\nasync def main():\n    tools = await server.mcp.list_tools()\n    print(len(tools))\n    print(hasattr(server, 'search_disclosures'))\nasyncio.run(main())\nPY > .omo/evidence/task-7-server-compat.txt`; PASS if output is `16` and `True`.
  Commit: Y | `refactor(server): split MCP tools into package modules` | Files: `server.py`, `dart_search_mcp/**`, tests

- [x] 8. Align CLI wrappers with package modules and reduce repetitive command glue
  What to do / Must NOT do:
  - Update `cli.py` imports to use package modules or a single stable public API.
  - Keep command names and help text stable unless diagnostics is intentionally added.
  - Consider a small helper for `asyncio.run(...); click.echo(...)` only if it reduces real duplication without hiding command behavior.
  - Add CLI tests for representative success/error paths using fake tool functions where possible.
  Parallelization: Can parallel N | Wave 3 | Blocks docs and final QA
  References:
  - `cli.py:16` imports from `server`
  - `cli.py:47` simple wrapper pattern starts
  - `cli.py:63` option-heavy command
  - `cli.py:240` `serve`
  Acceptance criteria:
  - `uv run python cli.py --help` still lists previous commands plus `diagnostics`.
  - Representative command help pages for `periodic`, `event`, and `securities` still show valid type lists.
  QA scenarios:
  - CLI: `uv run python cli.py periodic --help > .omo/evidence/task-8-periodic-help.txt`; PASS if output includes `증자감자현황`.
  - CLI: `uv run python cli.py event --help > .omo/evidence/task-8-event-help.txt`; PASS if output includes `유상증자결정`.
  - CLI: `uv run python cli.py securities --help > .omo/evidence/task-8-securities-help.txt`; PASS if output includes `지분증권`.
  Commit: Y | `refactor(cli): align commands with package API` | Files: `cli.py`, tests

- [x] 9. Add script-verifiable generated documentation for tools and commands
  What to do / Must NOT do:
  - Add a docs helper script such as `scripts/generate_docs.py` or a package command that introspects MCP tools and Click commands.
  - Generate a markdown artifact such as `docs/tools.md` and `docs/cli.md`, or update README sections in-place if simpler.
  - Add a check mode that fails when generated docs are stale.
  - Must NOT require a DART API key.
  Parallelization: Can parallel Y | Wave 3 | Blocks README
  References:
  - `server.py:22` or package equivalent for MCP introspection
  - `cli.py:40` Click command tree
  - `README.md:30` currently incomplete tool section
  Acceptance criteria:
  - `uv run python scripts/generate_docs.py --check` exits 0 after docs are generated.
  - Generated docs list 16 MCP tools and all CLI commands.
  QA scenarios:
  - CLI/script: `uv run python scripts/generate_docs.py --check > .omo/evidence/task-9-docs-check.txt`; PASS if exit 0 and output reports current docs.
  Commit: Y | `docs(tools): add generated command and MCP reference` | Files: `scripts/generate_docs.py`, `docs/**` or README sections, tests

- [x] 10. Rewrite README around actual user workflows
  What to do / Must NOT do:
  - Update project description from “4 tools” level to actual MCP + CLI scope.
  - Document setup with `uv sync`, API key configuration, `dart diagnostics`, `dart --help`, `uv run python cli.py --help`, and Claude Desktop config.
  - Include quickstart flows:
    - Find corp code by company name
    - Search disclosures
    - Fetch company info
    - Fetch financial statements
    - Use report/event/securities dispatch types
    - Download document/XBRL into an output directory
  - Include offline diagnostics and troubleshooting section.
  - Must NOT include a real secret or imply tests require a real API key.
  Parallelization: Can parallel Y | Wave 4 | Blocks final verification
  References:
  - `README.md:1` title
  - `README.md:24` current server run section
  - `README.md:30` incomplete tool list
  - `README.md:64` Claude Desktop config
  - `cli.py:40` CLI root help
  - `pyproject.toml:14` console script
  Acceptance criteria:
  - README names all 16 MCP tools or links to generated docs that list all 16.
  - README names all CLI commands or links to generated docs that list all commands.
  - Every command shown in quickstart is syntactically runnable.
  QA scenarios:
  - Docs/script: `uv run python scripts/generate_docs.py --check > .omo/evidence/task-10-readme-docs-check.txt`; PASS if docs are current.
  - CLI: run every README command that does not require a live API key with `--help` or `diagnostics`, write transcript to `.omo/evidence/task-10-readme-commands.txt`; PASS if all exit 0.
  Commit: Y | `docs(readme): document practical MCP and CLI workflows` | Files: `README.md`, `docs/**`

- [x] 11. Add offline QA command recipes for future workers
  What to do / Must NOT do:
  - Add a small `scripts/qa.py` or `make`-free documented command list that runs the common gate:
    - unit tests
    - compileall
    - CLI help
    - MCP tool introspection
    - diagnostics missing/present key checks
  - Prefer Python script over shell if cross-platform matters.
  - Must NOT require network access.
  Parallelization: Can parallel Y | Wave 4 | Blocks final verification
  References:
  - `.gitignore:11` test/lint cache ignores
  - `pyproject.toml:6` Python version
  - `cli.py:40` CLI group
  - `server.py:22` MCP app
  Acceptance criteria:
  - `uv run python scripts/qa.py` exits 0 and prints each command it ran.
  - QA script fails nonzero if a subcommand fails.
  QA scenarios:
  - CLI/script: `uv run python scripts/qa.py > .omo/evidence/task-11-qa-script.txt`; PASS if exit 0 and transcript includes tests, compileall, CLI help, MCP tools, diagnostics.
  Commit: Y | `chore(qa): add offline verification script` | Files: `scripts/qa.py`, README/docs references

- [x] 12. Final integration verification and cleanup
  What to do / Must NOT do:
  - Run the full common gate.
  - Run `git status --short` and classify all remaining changes.
  - Confirm no QA temp files, tmux sessions, bound ports, or generated secret-bearing files remain.
  - Confirm `.omo/evidence/` has transcripts for each task.
  - Review the diff for accidental product-surface changes.
  - Must NOT commit unless the user explicitly asks for commits.
  Parallelization: Can parallel N | Wave 4 | Final
  References:
  - Whole repository
  Acceptance criteria:
  - `uv run python scripts/qa.py` exits 0.
  - `uv run python -m unittest discover -v` exits 0.
  - `uv run python -m compileall server.py cli.py dart_search_mcp tests` exits 0.
  - `uv run dart --help` exits 0.
  - MCP tool introspection prints 16 tools.
  - README/generated docs check exits 0.
  QA scenarios:
  - CLI/script: `uv run python scripts/qa.py > .omo/evidence/task-12-final-qa.txt`; PASS if exit 0.
  - Git: `git status --short > .omo/evidence/task-12-git-status.txt`; PASS if all files are expected.
  Commit: N unless explicitly requested | draft: `chore(usability): make DART MCP practical to run and verify` | Files: all touched files

## Final Verification Wave
> Runs after ALL todos. ALL must pass before declaring complete.
- [x] F1. Plan compliance audit
  - Check every todo has evidence under `.omo/evidence/`.
  - Check no todo skipped TDD without an explicit exemption.
- [x] F2. Code quality review
  - Review module boundaries for circular imports, duplicated command wrappers, and hidden behavior changes.
- [x] F3. Real manual QA
  - Run `uv run dart --help`, `uv run python cli.py diagnostics`, `uv run python scripts/qa.py`, and MCP `list_tools()` driver.
- [x] F4. Scope fidelity
  - Confirm no web UI, DB, live API dependency in tests, renamed public tools, or secret exposure was introduced.

## Commit Strategy
- Do not auto-commit unless the user requests it.
- If committing later, prefer one commit per wave or per logical todo group:
  - `test(surface): characterize current MCP and CLI contracts`
  - `fix(cli): make dart console command executable locally`
  - `feat(cli): add offline diagnostics command`
  - `refactor(core): split DART MCP internals by responsibility`
  - `docs(readme): document practical MCP and CLI workflows`
  - `chore(qa): add offline verification script`
- Commit messages should follow the repository Lore Commit Protocol if enforced by the active AGENTS.md.

## Success Criteria
- A new contributor can run one documented command to verify the project locally without a DART API key.
- A user can discover all MCP tools and CLI commands from README or linked generated docs.
- `dart --help` works through the declared package script or README clearly documents the supported local equivalent.
- `diagnostics` explains missing/present API key state without leaking secrets or making network calls.
- The implementation is split into package modules with `server.py` retained as a compatibility entrypoint.
- Tests cover public surface contracts and no-network behavior.
- Final QA evidence exists under `.omo/evidence/` and all common gates pass.

## Known Risks And Mitigations
- Risk: Refactor changes string output accidentally.
  - Mitigation: characterization tests before extraction and targeted assertions on representative outputs.
- Risk: Live DART behavior cannot be validated without credentials.
  - Mitigation: default suite uses fake HTTP; optional live smoke can be documented separately and skipped by default.
- Risk: Console script behavior depends on package layout.
  - Mitigation: reproduce `uv run dart --help` failure first, then choose the smallest packaging fix and verify in a fresh sync if feasible.
- Risk: Generated docs become stale.
  - Mitigation: add `--check` mode and include it in `scripts/qa.py`.
