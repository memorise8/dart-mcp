# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cli as cli_module
from server import mcp


async def _tool_rows() -> list[tuple[str, str]]:
    tools = await mcp.list_tools()
    rows: list[tuple[str, str]] = []
    for tool in tools:
        description = (tool.description or "").strip().splitlines()
        rows.append((tool.name, description[0] if description else ""))
    return rows


def _render_tools(rows: list[tuple[str, str]]) -> str:
    lines = [
        "# MCP Tools",
        "",
        "이 문서는 `scripts/generate_docs.py`로 생성됩니다.",
        "",
        "## 도구 목록",
        "",
    ]
    for name, description in rows:
        lines.append(f"- `{name}`: {description}")

    lines.extend(
        [
            "",
            "## 주요 사용 흐름",
            "",
            "1. `search_corp_code`로 회사명을 DART 고유번호로 변환합니다.",
            "2. `search_disclosures`로 기간, 공시유형, 접수번호를 확인합니다.",
            "3. 재무 데이터는 `get_financial_statements`, `get_financial_statements_full`, `get_financial_indicators`를 사용합니다.",
            "4. 원문 ZIP은 `download_document`, XBRL ZIP은 `download_xbrl`로 내려받습니다.",
            "",
            "## XBRL",
            "",
            "- `download_xbrl`은 `rcept_no`를 직접 받거나 `corp_code` + `bsns_year` + `reprt_code`로 접수번호를 자동 조회합니다.",
            "- `get_xbrl_taxonomy`는 `BS1`, `BS2`, `BS3`, `IS1`, `IS2`, `IS3`, `IS4`, `CIS1`, `CIS2`, `CIS3`, `CIS4`, `DC1`, `DC2` 양식을 조회합니다.",
            "",
        ]
    )
    return "\n".join(lines)


def _render_cli() -> str:
    lines = [
        "# CLI Commands",
        "",
        "이 문서는 `scripts/generate_docs.py`로 생성됩니다.",
        "",
        "## 명령 목록",
        "",
    ]
    for name, command in cli_module.cli.commands.items():
        lines.append(f"- `dart {name}`: {command.get_short_help_str()}")

    lines.extend(
        [
            "",
            "## 예시",
            "",
            "```bash",
            "uv run dart diagnostics",
            "uv run dart search 삼성전자",
            "uv run dart disclosures --corp 삼성전자 --from 20240101 --to 20241231 --type A",
            "uv run dart financial 00126380 2024",
            "uv run dart financial-full 00126380 2024 --fs CFS",
            "uv run dart indicators 00126380 2024 --class M210000",
            "uv run dart download-xbrl 00126380 2024 --report 11011 -o ./downloads",
            "uv run dart taxonomy BS1",
            "uv run dart serve",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def _write_or_check(path: Path, content: str, check: bool) -> bool:
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            print(f"out of date: {path.relative_to(ROOT)}")
            return False
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")
    return True


async def _main() -> int:
    parser = argparse.ArgumentParser(description="Generate CLI and MCP tool documentation.")
    parser.add_argument("--check", action="store_true", help="fail if generated docs are out of date")
    args = parser.parse_args()

    tool_doc = _render_tools(await _tool_rows())
    cli_doc = _render_cli()

    ok = True
    ok = _write_or_check(ROOT / "docs" / "tools.md", tool_doc, args.check) and ok
    ok = _write_or_check(ROOT / "docs" / "cli.md", cli_doc, args.check) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
