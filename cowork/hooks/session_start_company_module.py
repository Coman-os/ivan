#!/usr/bin/env python3
"""Load explicitly connected company rules in addition to the base harness."""
import json
import os
from pathlib import Path
import sys

from _ivan_company import discover, rules_context


def main():
    payload = json.load(sys.stdin)
    start = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    root = discover(start)
    if root is None:
        return
    try:
        context = rules_context(root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        context = f"Модуль компании не загружен: {exc}. Не считай его проверки действующими."
    if context:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}, ensure_ascii=False))


if __name__ == "__main__":
    import _host_adapter
    sys.exit(_host_adapter.run_main(main))
