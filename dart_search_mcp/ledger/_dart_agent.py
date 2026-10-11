"""dart-agent 파서 import 어댑터 — dart-agent 경로를 sys.path에 넣는 유일한 지점."""
import importlib
import os
import sys
from functools import lru_cache

DEFAULT_ROOT = "/mnt/raid/ruci_workspace/repo/dart-agent"


def configure(root: str | None = None) -> None:
    path = root or os.environ.get("DART_AGENT_ROOT") or DEFAULT_ROOT
    if path not in sys.path:
        sys.path.insert(0, path)


@lru_cache(None)
def _mod(name: str):
    configure()
    return importlib.import_module(f"search.{name}")


def fs_table():
    return _mod("fs_table")


def note_heading():
    return _mod("note_heading")


def note_section():
    return _mod("note_section")
