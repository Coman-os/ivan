#!/usr/bin/env python3
"""Check opted-in business documents after a file edit; no remote execution."""
import json
import os
from pathlib import Path
import sys

from _host_adapter import read_inputs
from _ivan_company import check, discover


def main():
    inputs = read_inputs()
    findings = []
    for payload in inputs:
        fp = (payload.get("tool_input") or {}).get("file_path")
        if not fp:
            continue
        start = Path(payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
        file = Path(fp)
        if not file.is_absolute():
            file = start / file
        root = discover(file.parent)
        if root is None:
            continue
        try:
            findings.extend(check(root, [file]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            findings.append({"level": "error", "path": str(root), "message": f"Модуль компании не проверен: {exc}"})
    if not findings:
        return
    # One message per event, not one message for each file.
    messages = list(dict.fromkeys(f"{x['path']}: {x['message']}" for x in findings))
    text = "Проверка документов компании:\n" + "\n".join(messages)
    if any(x["level"] == "error" for x in findings):
        print(json.dumps({"decision": "block", "reason": text}, ensure_ascii=False))
    else:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}, ensure_ascii=False))


if __name__ == "__main__":
    import _host_adapter
    sys.exit(_host_adapter.run_main(main))
