# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

TOOL_LIST_SCRIPT = """
import asyncio
from server import mcp

async def main():
    tools = await mcp.list_tools()
    names = [tool.name for tool in tools]
    print("\\n".join(names))
    assert len(names) == 16, len(names)

asyncio.run(main())
"""


type Check = tuple[str, list[str], dict[str, str] | None, str | None]


def _run(name: str, command: list[str], env: dict[str, str] | None = None, expected: str | None = None) -> bool:
    print(f"== {name}")
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        print(f"failed: {name} ({result.returncode})")
        return False
    output = result.stdout + result.stderr
    if expected is not None and expected not in output:
        print(f"failed: {name} did not include expected output: {expected}")
        return False
    return True


def main() -> int:
    missing_key_env = dict(os.environ)
    missing_key_env["DART_API_KEY"] = ""
    missing_key_env["dart_api"] = ""

    present_key_env = dict(missing_key_env)
    present_key_env["DART_API_KEY"] = "redacted-test-key"

    checks: list[Check] = [
        ("unit tests", [sys.executable, "-m", "unittest", "discover", "-v"], None, "OK"),
        ("compile", [sys.executable, "-m", "compileall", "server.py", "cli.py", "dart_search_mcp", "tests", "scripts"], None, None),
        ("cli help", ["uv", "run", "dart", "--help"], None, "download-xbrl"),
        ("diagnostics missing key", ["uv", "run", "dart", "diagnostics"], missing_key_env, "DART API key: missing"),
        ("diagnostics present key", ["uv", "run", "dart", "diagnostics"], present_key_env, "DART API key: configured"),
        ("mcp tool listing", [sys.executable, "-c", TOOL_LIST_SCRIPT], None, "download_xbrl"),
        ("docs check", [sys.executable, "scripts/generate_docs.py", "--check"], None, None),
    ]

    failed = 0
    for name, command, env, expected in checks:
        if not _run(name, command, env, expected):
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
