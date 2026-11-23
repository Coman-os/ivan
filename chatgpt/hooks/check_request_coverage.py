#!/usr/bin/env python3
"""Stop hook: one audit pass for compound and carried-over requests.

The hook deliberately does not decide whether the answer is complete. That
judgement needs the main model's task context. It only detects inputs where a
last-message check is predictably too narrow and returns the model to a rolling
coverage audit once. No dialogue is sent to a second model.
"""

import hashlib
import json
import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _transcript_io import load_messages  # noqa: E402
CLOSE_REQUEST = re.compile('(?:/close-session|(?:закроем|закрываем|закрывай(?:те)?|закрой(?:те)?|завершай|заверши)(?:\\s+(?:эту|рабочую))?\\s+сессию|^быстро закрываем|^вс[её] на сегодня)', re.I)


SHORT_CONTINUATIONS = re.compile(
    r"^\s*(ну\s+)?(?:ок(?:ей)?[!,]?\s*(?:давай|делай|продолжай|поехали|переделай)"
    r"|давай|делай|продолжай|поехали|переделай)"
    r"(?:\s+(?:дальше|тогда|это|его|её))?[.!\s]*$",
    re.IGNORECASE,
)

OMISSION_COMPLAINTS = re.compile(
    r"(?:почему\s+не|а\s+где\b|где\s+же\b|на\s+второй\s+вопрос|"
    r"не\s+ответил|не\s+взял|не\s+отработал|закрыл.{0,60}по\s+процедуре|"
    r"что\s+поменялось|и\s+что\s+дальше)",
    re.IGNORECASE,
)

COMPOUND_MARKERS = re.compile(
    r"(?:\bи\s+отдельно\b|\bа\s+также\b|\bпосле\s+этого\b|"
    r"\bсначала\b.{0,160}\b(?:потом|после)\b|\bпри\s+этом\b|"
    r"\bплюс\b|\bотдельно\s+ответь\b)",
    re.IGNORECASE | re.DOTALL,
)

ACTION_MARKERS = re.compile(
    r"\b(?:сделай|подготовь|собери|переделай|проверь|посмотри|найди|"
    r"добавь|исправь|ответь|запусти|внеси|посчитай|сравни)\b",
    re.IGNORECASE,
)

LIST_ITEM = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)])\s+\S")
EXECUTION_TOPIC = re.compile(
    r"доведени\w*\s+(?:задач\w*\s+)?до\s+результата|"
    r"качеств\w*\s+исполнения\s+задач|"
    r"\b(?:issue|decision)[-–\s]+(?:трекер|tracker)",
    re.IGNORECASE,
)

EXECUTION_REVIEW = (
    "Если в сессии подтверждён сбой доведения задачи до результата "
    "или согласовано решение о том, как предотвращать такой сбой, "
    "сверь и обнови журналы по инструкции "
    ".claude/logs/README.md#качество-исполнения-задач. "
    "Используй факты сессии, обновляй существующие записи и фиксируй "
    "проверку решения либо его повторное срабатывание. "
    "Само срабатывание аудита не является сбоем."
)


def message_text(message):
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            parts.append(block.get("text", ""))
    return " ".join(parts)


def is_compound(text):
    """The request has at least two independently closable obligations."""
    if text.count("?") >= 2:
        return True
    if len(LIST_ITEM.findall(text)) >= 2:
        return True
    if COMPOUND_MARKERS.search(text):
        return True
    # Action plus a separate question is the common one-question failure:
    # the artefact gets changed, while the causal question is omitted.
    if "?" in text and ACTION_MARKERS.search(text):
        return True
    return False


def audit_reason(messages):
    """Reason to ask for a rolling audit, or None for a simple request."""
    user_texts = [message_text(m).strip() for m in messages
                  if m.get("role") == "user" and message_text(m).strip()]
    if not user_texts:
        return None
    latest = user_texts[-1]
    if OMISSION_COMPLAINTS.search(latest):
        return "пользователь указал на пропущенный вопрос или результат"
    if is_compound(latest):
        return "в текущем сообщении несколько обязательств"
    if SHORT_CONTINUATIONS.match(latest):
        # A short approval inherits the work already specified. Looking only
        # at «давай» erases the source, design, and explanation obligations.
        for previous in reversed(user_texts[-6:-1]):
            if is_compound(previous) or ACTION_MARKERS.search(previous):
                return "короткое продолжение наследует обязательства прошлых ходов"
    return None


def _es_bound(*starts):
    for start in starts:
        if not start:
            continue
        directory = os.path.abspath(start)
        while True:
            if os.path.exists(os.path.join(directory, ".es-connection.json")):
                return True
            parent = os.path.dirname(directory)
            if parent == directory:
                break
            directory = parent
    return False


def seen_before(session_id, messages):
    """Warn once per user obligation set; any state failure is fail-open."""
    user_tail = "\n".join(message_text(m) for m in messages
                          if m.get("role") == "user")[-6000:]
    digest = hashlib.sha256(
        ((session_id or "unknown") + "\0" + user_tail).encode("utf-8")
    ).hexdigest()
    path = os.path.join(tempfile.gettempdir(),
                        "ivan-request-coverage-" + digest + ".seen")
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        return False
    except FileExistsError:
        return True
    except OSError:
        return True


def response(reason, *, before_answer=False):
    if before_answer:
        finish = "Дай один полный ответ на текущий запрос."
    else:
        finish = (
            "Уже отправленный ответ не повторяй. Если найден пропуск — выполни "
            "недостающее и сообщи только дополнение или исправление. Если "
            "пропусков нет — нового сообщения пользователю не добавляй, "
            "включая подтверждение самой проверки. Если итоговый ответ ещё "
            "не отправлен, дай один полный ответ."
        )
    return {
        "decision": "block",
        "reason": (
            "Перед завершением сделай сквозную сверку запроса: " + reason + ". "
            "Восстанови из последних ходов все ещё действующие обязательства: "
            "вопросы, действия, обещанные объяснения и результаты. Для каждого "
            "закрой один исход: ответ дан; действие выполнено и проверено; "
            "требуется одно действительно чужое решение; либо пункт явно заменён "
            "новой инструкцией. Всё вычислимое заверши сейчас. "
            f"{EXECUTION_REVIEW} "
            "Проведи сверку внутренне, служебный список человеку не показывай. "
            f"{finish}"
        ),
    }


def needs_execution_review(text):
    return bool(EXECUTION_TOPIC.search(text) or (
        CLOSE_REQUEST.search(text) and "?" not in text
    ))


def main():
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return
    if hook_input.get("stop_hook_active"):
        return
    if _es_bound(os.environ.get("CLAUDE_PROJECT_DIR"), hook_input.get("cwd")):
        return
    messages = load_messages(hook_input, prefer_history=True)
    if hook_input.get("hook_event_name") == "UserPromptSubmit":
        # The current prompt may not have been written to the rollout yet.
        prompt = hook_input.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            return
        if not messages or messages[-1].get("role") != "user" or message_text(messages[-1]).strip() != prompt.strip():
            messages = [*messages, {"role": "user", "content": prompt}]
        reason = audit_reason(messages)
        context = []
        if note := hook_input.get("request_coverage_context"):
            context.append(note)
        if reason:
            context.append(response(reason, before_answer=True)["reason"])
        elif needs_execution_review(prompt):
            context.append(EXECUTION_REVIEW)
        if context:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "\n".join(context),
            }}, ensure_ascii=False))
        return
    reason = audit_reason(messages)
    user_messages = [message_text(message) for message in messages if message.get("role") == "user"]
    latest = user_messages[-1] if user_messages else ""
    if not (reason or needs_execution_review(latest)) or seen_before(hook_input.get("session_id"), messages):
        return
    output = response(reason) if reason else {
        "decision": "block",
        "reason": EXECUTION_REVIEW + " Если журнал уже актуален, не повторяй ответ.",
    }
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    import _host_adapter
    sys.exit(_host_adapter.run_main(main))
