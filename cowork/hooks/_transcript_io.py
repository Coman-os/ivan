"""Чтение транскрипта сессии из payload хука — общий для Stop/SubagentStart.

Зачем отдельный модуль. Три хука (`check_deferred_actions`,
`check_closing_handoff`, `inject_session_context`) читали
`hook_input["transcript"]` — массив сообщений. Харнес такого ключа не
передаёт: в payload приходит **`transcript_path`**, путь к `.jsonl`.
`.get("transcript", [])` возвращал пустой список, все три уходили в ранний
выход и молчали. Аудит 2026-08-25: 2564 холостых запуска за 24 дня, ноль
нештатных исходов при физически присутствующих фразах-триггерах.

Контрпример, снимающий гипотезу «харнес не даёт транскрипт»:
`session_end_autocommit.py` в том же наборе читает `transcript_path` и
работает. То есть дефект — в контракте трёх хуков, не в платформе.

Класс: подмена контракта. Ловится только прогоном на реальной форме
payload; тест, кормящий хук синтетическим `transcript`, остаётся зелёным —
проверяет регулярки, а не контракт (`tests/test_closing_handoff.py`).
"""

import json
import os


def load_messages(hook_input):
    """Сообщения сессии как список dict с ключами role/content.

    Принимает обе формы: `transcript` (список — синтетический вход тестов) и
    `transcript_path` (реальная форма харнеса). Нет ни того ни другого либо
    файл нечитаем → пустой список: хук молча выходит, не мешая сессии.
    """
    inline = hook_input.get("transcript")
    if isinstance(inline, list) and inline:
        return inline

    path = hook_input.get("transcript_path")
    if not path or not os.path.exists(path):
        return []

    messages = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                # Формат .jsonl: {"type":"assistant","message":{"role":…,"content":…}}
                msg = entry.get("message")
                if isinstance(msg, dict) and msg.get("role"):
                    messages.append(msg)
                elif entry.get("role"):
                    messages.append(entry)
    except OSError:
        return []
    return messages
