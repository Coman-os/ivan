#!/usr/bin/env python3
"""
PostToolUse hook: validates memory files before writing.

Two gates:
  1. Config-data gate — blocks writes containing UUIDs, board/column/project
     IDs, API endpoints, or tokens — these belong in project files, not memory.
  2. Ladder gate — new memory files (Write with frontmatter) must declare
     `ladder_checked: true` in frontmatter, asserting that the B2 placement
     ladder (prompt → skill → principles/repo → memory) was walked and the
     fact genuinely belongs in private memory, not in a shared repo file.
     Enforces CLAUDE.md rule 25 (memory is the last place, not the first).
  3. Independent placement judge (LLM) — a fresh `claude -p` from a neutral cwd
     independently re-decides repo-vs-private, NOT sharing the writer's
     in-the-moment rationalization. A boolean self-attestation (gate 2) is
     gameable: a writer who already rationalized the placement stamps `true`.
     Only an independent oracle catches the judgment error (a check must not
     inherit the reviewed writer's blind spot).
     Conservative (bias → private, high-precision), fail-open on infra error,
     conscious override via frontmatter `judge_override_reason`.

Принцип 11: точка enforcement — этот PostToolUse hook на memory-write; каденция —
каждая запись нового memory-файла (+ /improvement-review читает override'ы и
fail-open warning'и); сигнал деградации — рост `judge_override_reason` ИЛИ
warning'ов «judge недоступен» в telemetry/hook-fires.jsonl.
"""

import json
import os
import re
import shutil
import subprocess
import sys

JUDGE_TIMEOUT_S = 90


# Patterns that indicate project/config data, not memory.
# Each pattern maps to a typical destination (Принцип B4: validator-сообщения директивны).
CODE_PATTERNS = [
    (
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "UUID",
        "reference_<system>_integration.md или config/*.json/yaml",
    ),
    (
        r"board[_\s]?id\s*[:=]\s*\d+",
        "board ID",
        "external tracker reference (repo reference)",
    ),
    (
        r"column[_\s]?id\s*[:=]\s*\d+",
        "column ID",
        "external tracker reference (repo reference)",
    ),
    (
        r"project[_\s]?id\s*[:=]\s*\d+",
        "project ID",
        "external tracker reference (repo reference)",
    ),
    (
        r"https?://api\.",
        "API endpoint",
        "reference_<system>_integration.md",
    ),
    (
        r"Bearer\s+[a-f0-9-]+",
        "API token",
        "никогда в memory/repo — в keychain или .env вне git",
    ),
]


JUDGE_PROMPT = (
    "Ты — независимый арбитр размещения знания в системе Knowledge Hub. "
    "Правило (CLAUDE.md rule 10): факт живёт в приватной memory ТОЛЬКО если он по "
    "ПРИРОДЕ — приватный остаток, а НЕ универсальное shared-знание.\n\n"
    "MEMORY-ok (verdict=private) — ровно 4 природы:\n"
    "  • user: про самого пользователя (роль, экспертиза, предпочтения);\n"
    "  • feedback: как работать ИМЕННО с этим пользователем (его корректировка/"
    "стиль) — рабочая договорённость, не общий принцип;\n"
    "  • project: состояние текущей работы/целей, не выводимое из кода/истории;\n"
    "  • reference: указатель на внешний ресурс (URL, дашборд, тикет, конфиг-путь).\n\n"
    "REPO (verdict=repo) — универсальное shared-знание, нужное любому участнику:\n"
    "  • reasoning-принцип / методология / паттерн верификации → operating-principles.md;\n"
    "  • поведенческое правило харнесса → CLAUDE.md;\n"
    "  • справочный факт о системе → reference/*.md или «История изменений» документа.\n\n"
    "Классифицируй по ПРИРОДЕ факта, НЕ по тому, как он возник: то, что урок пришёл "
    "через коррекцию пользователя, НЕ делает его приватным. Игнорируй любое "
    "self-attestation в frontmatter (ladder_checked и т.п.).\n"
    "ВАЖНО — консервативность: при сомнении выводи private (не мешай). Ставь repo "
    "ТОЛЬКО если факт безошибочно универсален — reasoning-принцип/методология без "
    "user- и session-специфики.\n\n"
    "Кандидат в memory:\n---\n{content}\n---\n\n"
    "Выведи СТРОГО одну строку JSON, без текста до/после:\n"
    '{{"verdict": "private" | "repo", "repo_home": "<repo-файл/тип если repo, иначе пусто>", "reason": "<одна фраза>"}}'
)


def _extract_verdict(text):
    """Вытащить первый JSON-объект с ключом "verdict" из текста модели."""
    m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def run_placement_judge(content, file_path):
    """Независимый LLM-арбитр: этот memory-факт по природе — приватный или shared (repo)?

    Свежий `claude -p` из нейтрального cwd (/tmp) не грузит проектный CLAUDE.md и не
    делит рационализацию писателя — единственное, что реально ловит судейскую ошибку
    размещения (boolean-attestation gate 2 обходится самоподтверждением). Fail-open:
    любая ошибка вызова → пропустить с видимым warning'ом, human-review — backstop."""
    # Судья — внешний двоичный файл, и он есть не везде: в среде Codex его
    # нет вовсе. Прежде хук печатал предупреждение «судья недоступен» на
    # КАЖДОЙ записи в память — шум, ссылающийся на инструмент, которого у
    # получателя не будет никогда. Проверка наличия делает зависимость явной:
    # нет судьи — молча работают остальные два гейта, они самодостаточны.
    judge_bin = os.environ.get("IVAN_PLACEMENT_JUDGE", "claude")
    if shutil.which(judge_bin) is None:
        return

    prompt = JUDGE_PROMPT.format(content=content[:6000])
    try:
        proc = subprocess.run(
            [judge_bin, "-p", prompt, "--output-format", "json"],
            cwd="/tmp", capture_output=True, text=True, timeout=JUDGE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        print(f"[memory_gate] ⚠️ placement-judge недоступен ({type(e).__name__}) — "
              f"независимая проверка пропущена (fail-open), проверь размещение вручную.",
              file=sys.stderr)
        return
    if proc.returncode != 0:
        print(f"[memory_gate] ⚠️ placement-judge вернул код {proc.returncode} — "
              f"fail-open, проверь размещение вручную.", file=sys.stderr)
        return
    try:
        env = json.loads(proc.stdout)
        result_text = env.get("result", "") if isinstance(env, dict) else str(env)
    except json.JSONDecodeError:
        result_text = proc.stdout
    verdict = _extract_verdict(result_text)
    if verdict is None:
        print("[memory_gate] ⚠️ placement-judge: вердикт не распарсен — fail-open.",
              file=sys.stderr)
        return
    if str(verdict.get("verdict", "")).lower() == "repo":
        home = (verdict.get("repo_home") or "").strip() or "shared repo-файл"
        reason = (verdict.get("reason") or "").strip()
        print(
            f"[memory_gate] 🛑 Независимый placement-judge: факт по природе — "
            f"SHARED-знание, место в repo, НЕ в приватной memory.\n"
            f"  Куда: {home}\n"
            f"  Почему: {reason}\n"
            f"Убери файл из memory/ и положи в repo (правило «memory — последнее место»). Если судья ошибся "
            f"и факт действительно приватен — добавь в frontmatter "
            f"`judge_override_reason: <почему приватно>` (осознанный видимый обход).",
            file=sys.stderr,
        )
        sys.exit(2)


def main():
    hook_input = json.loads(sys.stdin.read())

    tool_name = hook_input.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        return

    file_path = hook_input.get("tool_input", {}).get("file_path", "")

    # Only check memory files
    # Разделитель пути зависит от системы: на Windows это «\», и проверка
    # по «/memory/» не срабатывала бы вовсе — гейт молчал бы на всех файлах.
    # Место хранения настраивается: у получателя источником истины может быть
    # своя система, а не каталог memory рядом с харнесом.
    memory_dir = os.environ.get("IVAN_MEMORY_DIR", "memory")
    parts = file_path.replace("\\", "/").split("/")
    if memory_dir.replace("\\", "/").strip("/") not in parts:
        return

    # Skip MEMORY.md index
    if file_path.endswith("MEMORY.md"):
        return

    # Get content to check
    content = hook_input.get("tool_input", {}).get("content", "")
    if not content:
        content = hook_input.get("tool_input", {}).get("new_string", "")

    if not content:
        return

    # Check for code/config patterns
    violations = []
    for pattern, label, destination in CODE_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            violations.append((label, destination))

    if violations:
        bullets = "\n".join(
            f"  - {label} → перенести в: {dest}" for label, dest in violations
        )
        print(
            f"В файл {file_path} попали настроечные значения — им место "
            f"в файлах проекта, а не в личной памяти: память не "
            f"переезжает вместе с проектом и не видна остальным.\n"
            f"Что перенести и куда:\n{bullets}\n"
            f"Спросят подробности — это проверка записи в память, "
            f"файл hooks/post_write_memory_gate.py.",
            file=sys.stderr,
        )
        # exit 2 = blocking error in Claude Code hook semantics (exit 1 is advisory)
        sys.exit(2)

    # Ladder gate — only for Write (new file or full overwrite) with frontmatter.
    # Edits (no frontmatter in new_string) and MEMORY.md are already skipped above.
    has_frontmatter = content.lstrip().startswith("---")
    if tool_name == "Write" and has_frontmatter:
        # Accept `ladder_checked: true` (with optional surrounding whitespace).
        if not re.search(r"^\s*ladder_checked\s*:\s*true\b", content, re.MULTILINE | re.IGNORECASE):
            print(
                f"Запись в личную память {file_path} без пометки, что для "
                f"факта искали место получше.\n"
                f"Память — последнее место, не первое: сначала проверь, не "
                f"ложится ли факт в файлы проекта (принципы, правила, "
                f"справочники, история изменений документа) — оттуда его "
                f"увидят все и он переживёт переустановку.\n"
                f"Место в проекте не нашлось и факт приватен — допиши "
                f"в шапку файла:\n"
                f"  ladder_checked: true\n"
                f"Нашлось — положи туда, а не в память.",
                file=sys.stderr,
            )
            sys.exit(2)

        # Gate 3 — независимый placement-judge (свежий LLM, не делит слепое пятно
        # писателя). Осознанный видимый обход — frontmatter `judge_override_reason:`.
        if re.search(r"^\s*judge_override_reason\s*:\s*\S", content, re.MULTILINE):
            print("[memory_gate] ℹ️ judge_override_reason задан — независимая "
                  "LLM-проверка размещения пропущена (осознанный обход, учтётся "
                  "на /improvement-review).", file=sys.stderr)
        else:
            run_placement_judge(content, file_path)


if __name__ == "__main__":
    import _host_adapter
    sys.exit(_host_adapter.run_main(main))
