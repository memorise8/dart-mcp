# dart-search-mcp usability planning brief

## Request
Create a plan to make the current project more usable.

## Workflow state
- Skill: omo:ulw-plan
- Phase: approval gate before writing `.omo/plans/<slug>.md`
- Planner constraint: no product-code edits before explicit user approval

## Evidence collected
- Repository files: `server.py`, `cli.py`, `README.md`, `pyproject.toml`, `.python-version`, `.gitignore`, `uv.lock`.
- `server.py` is 1,966 lines and registers 16 FastMCP tools.
- `cli.py` is 247 lines and exposes 17 Click commands.
- `README.md` documents setup, server launch, Claude Desktop config, and only 4 MCP tools.
- `pyproject.toml` declares `dart = "cli:cli"` but `uv run dart --help` failed with "No such file or directory".
- `uv run python cli.py --help` succeeded and listed the CLI commands.
- `uv run python -m unittest discover -v` found 0 tests.
- Installed runtime versions observed through `uv run`: fastmcp 3.2.0, mcp 1.27.0, httpx 0.28.1, click 8.3.2, python-dotenv 1.2.2.

## Main usability gaps
1. Discovery gap: users cannot see the real tool/CLI surface from README.
2. Packaging gap: declared console script is not usable through `uv run dart --help` in the current workspace.
3. Safety gap: no tests pin request formatting, CLI behavior, no-key behavior, or download behavior.
4. Maintainability gap: one 1,966-line server module mixes transport, config, DART HTTP, formatting, registries, downloads, and tool handlers.
5. Configuration gap: API key handling is global and implicit; there is no clear health/diagnostic command.
6. Output gap: all tool functions return formatted strings only, which is convenient for humans but hard to test or integrate reliably.

## Recommended planning approach
Plan a staged improvement path:
1. Lock current behavior with tests and CLI/MCP introspection.
2. Fix packaging and command discoverability.
3. Add first-class diagnostics and offline-safe help flows.
4. Extract modules by responsibility without changing behavior.
5. Improve docs to match the implemented surface.
6. Add optional structured output only after behavior is pinned.

## Remaining user decision
Recommended scope: practical local usability first, without adding a web UI or changing the public API shape in the first pass.

Alternative scopes:
- Minimal: README + packaging + smoke tests only.
- Practical: minimal plus diagnostics, modularization, generated tool docs, and test harness.
- Broad: practical plus structured JSON outputs and richer integration patterns.

Recommended option: Practical.
