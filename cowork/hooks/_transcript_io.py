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

    # Codex кладёт последний ответ прямо в payload события Stop
    # (`last_assistant_message`, документация хуков). Хвост ответа — всё, что
    # нужно проверкам конца ответа; брать его отсюда надёжнее, чем разбирать
    # файл сессии, формат которого у Codex другой и документацией назван
    # неустойчивым. Ветка добавлена 08.09.2026; на живом Codex не прогнана.
    last = hook_input.get("last_assistant_message")
    if isinstance(last, str) and last.strip():
        return [{"role": "assistant", "content": last}]

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
                # Claude Code: {"type":"assistant","message":{"role":…,"content":…}}
                msg = entry.get("message")
                if isinstance(msg, dict) and msg.get("role"):
                    messages.append(msg)
                    continue
                # Codex (rollout): {"type":"response_item","payload":{"type":"message",
                # "role":…,"content":[{"type":"output_text","text":…}]}}.
                # Форма из исходников codex-rs; на живой записи не проверена.
                payload = entry.get("payload")
                if isinstance(payload, dict) and payload.get("role") \
                        and payload.get("type", "message") == "message":
                    content = payload.get("content")
                    if isinstance(content, list):
                        content = [{"type": "text", "text": c.get("text", "")}
                                   for c in content if isinstance(c, dict)
                                   and c.get("type") in ("output_text", "input_text", "text")]
                    messages.append({"role": payload["role"], "content": content})
                    continue
                if entry.get("role"):
                    messages.append(entry)
    except OSError:
        return []
    return messages
