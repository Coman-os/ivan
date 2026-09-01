#!/usr/bin/env python3
"""
Stop hook: catches when Claude defers actions to "next time".
Since each session starts from scratch, "next time" never happens.
When detected, blocks the response and tells Claude to propose a concrete fix.
"""

import json
import re
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transcript_io import load_messages  # noqa: E402


DEFERRED_PATTERNS = [
    # Russian patterns
    r"в следующий раз буду",
    r"в следующий раз я",
    r"учту на будущее",
    r"запомню на будущее",
    r"в будущем буду",
    r"в будущем постараюсь",
    r"в дальнейшем буду",
    r"в дальнейшем постараюсь",
    r"буду иметь в виду",
    r"приму к сведению",
    r"учту это в следующ",
    r"запомню это",
    r"буду помнить",
    r"не забуду в следующ",
    r"исправлюсь в следующ",
    r"в следующей сессии",
    r"при следующем запуске",
    r"когда в следующий раз",
    # English patterns
    r"i'll remember this",
    r"i will remember",
    r"i'll keep this in mind",
    r"i will keep .* in mind",
    r"next time i will",
    r"next time i'll",
    r"in future sessions",
    r"in the next session",
    r"i'll note this for",
    r"i will note this",
    r"going forward i'll",
    r"going forward i will",
    r"i'll make sure to .* next time",
    r"i'll do better next time",
]

COMPILED = [re.compile(p, re.IGNORECASE) for p in DEFERRED_PATTERNS]


def main():
    hook_input = json.loads(sys.stdin.read())
    transcript = load_messages(hook_input)

    if not transcript:
        print(json.dumps({"decision": "allow"}))
        return

    last_message = transcript[-1]
    if last_message.get("role") != "assistant":
        print(json.dumps({"decision": "allow"}))
        return

    # Extract text from assistant message
    text_parts = []
    content = last_message.get("content", [])
    if isinstance(content, str):
        text_parts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif isinstance(block, str):
                text_parts.append(block)

    full_text = " ".join(text_parts).lower()

    for pattern in COMPILED:
        match = pattern.search(full_text)
        if match:
            print(json.dumps({
                "decision": "block",
                "reason": (
                    f"[check_deferred_actions] Обнаружена отложенная "
                    f"формулировка: «{match.group()}». "
                    f"Каждая сессия начинается с нуля — 'в следующий раз' "
                    f"не наступит. Вместо обещания предложи конкретный фикс: "
                    f"какой файл отредактировать, какую строку добавить в "
                    f"memory или CLAUDE.md, чтобы это знание сохранилось."
                )
            }))
            return

    print(json.dumps({"decision": "allow"}))


if __name__ == "__main__":
    import _host_adapter
    sys.exit(_host_adapter.run_main(main))
