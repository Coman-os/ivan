#!/usr/bin/env python3
"""
SessionEnd hook: страховочный авто-коммит по итогам сессии — ТОЛЬКО файлы этой сессии.

Логика:
- Смысловой коммит «что сделано» пишет /close-session (Phase 3, End of Day) —
  там Claude в цикле и формулирует содержательное описание.
- Этот хук — только сеть безопасности. Если сессия закрылась БЕЗ /close-session
  (значит Phase 3 не отработала и изменения не закоммичены) — сохраняем их, чтобы
  работа не потерялась.
- Репозиторий мульти-сессионный (правило git-commits: git add -A запрещён), поэтому
  хук коммитит НЕ всё грязное дерево, а пересечение: (файлы, тронутые этой сессией
  через Write/Edit — извлекаются из transcript_path) ∩ (грязные файлы по git status).
  Работа параллельных сессий не захватывается.

Ограничения (осознанные):
- Файлы, созданные Bash-командами (генерация HTML, скрипты), в транскрипте как
  Write/Edit не видны — их хук не коммитит; они остаются в рабочем дереве.
- Если transcript_path недоступен — хук молча выходит (ничего не коммитить
  безопаснее, чем сгрести чужое; изменения не теряются, остаются в дереве).
- Текст коммита — болванка (дата + причина + список файлов), не смысловая сводка.

Секреты: коммит проходит через pre-commit secret-scan (правило «секрет не попадает в tracked-артефакт»); --no-verify
не используется. Если scan блокирует — коммит не создаётся, изменения остаются.
"""

import json
import os
import subprocess
import sys
from datetime import datetime

EDIT_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def git(args, cwd):
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True
    )


def extract_session_files(transcript_path):
    """Абсолютные пути всех файлов, записанных этой сессией (tool_use Write/Edit)."""
    paths = set()
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"file_path"' not in line and '"notebook_path"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                message = obj.get("message") or {}
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if (
                        isinstance(block, dict)
                        and block.get("type") == "tool_use"
                        and block.get("name") in EDIT_TOOLS
                    ):
                        tool_input = block.get("input") or {}
                        fp = tool_input.get("file_path") or tool_input.get("notebook_path")
                        if fp:
                            paths.add(fp)
    except OSError:
        return set()
    return paths


def dirty_files(project):
    """Грязные файлы по git status --porcelain -z (repo-relative, включая untracked)."""
    res = git(["status", "--porcelain", "-z"], project)
    if res.returncode != 0:
        return set()
    dirty = set()
    entries = res.stdout.split("\0")
    i = 0
    while i < len(entries):
        entry = entries[i]
        if not entry:
            i += 1
            continue
        xy, path = entry[:2], entry[3:]
        dirty.add(path)
        if "R" in xy or "C" in xy:
            i += 1  # для rename/copy следующее поле — старый путь, пропускаем
        i += 1
    return dirty


def main():
    reason = "unknown"
    transcript_path = None
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        reason = payload.get("reason", "unknown")
        transcript_path = payload.get("transcript_path")
    except Exception:
        pass

    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()

    inside = git(["rev-parse", "--is-inside-work-tree"], project)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return

    if not transcript_path or not os.path.isfile(transcript_path):
        # Без транскрипта не отличить своё от чужого — в мульти-сессионном репо
        # безопаснее не коммитить ничего (изменения остаются в дереве).
        print(
            "[session_end_autocommit] transcript_path недоступен — авто-коммит "
            "пропущен (мульти-сессионный репо: не коммитим чужое).",
            file=sys.stderr,
        )
        return

    session_abs = extract_session_files(transcript_path)
    if not session_abs:
        return

    # Абсолютные пути сессии → repo-relative; вне репозитория — отбрасываем.
    session_rel = set()
    for p in session_abs:
        rel = os.path.relpath(p, project)
        if not rel.startswith(".."):
            session_rel.add(rel)

    to_commit = sorted(session_rel & dirty_files(project))
    if not to_commit:
        # Всё своё уже закоммичено (/close-session отработал) либо грязное — чужое.
        return

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    body_files = "\n".join(f"- {f}" for f in to_commit[:50])
    if len(to_commit) > 50:
        body_files += f"\n- … и ещё {len(to_commit) - 50} файл(ов)"

    message = (
        f"chore(session): авто-коммит незакрытой сессии ({stamp})\n\n"
        f"Сессия завершена (reason: {reason}) без /close-session — страховочное "
        f"сохранение. Закоммичены ТОЛЬКО файлы этой сессии (по транскрипту); "
        f"изменения параллельных сессий не тронуты. Для содержательного коммита "
        f"закрывайте сессию через /close-session.\n\n"
        f"Файлы сессии:\n{body_files}"
    )

    git(["add", "--"] + to_commit, project)
    # Коммит с проверками (secret-scan pre-commit). Если заблокирован —
    # изменения остаются staged, ничего не теряется.
    git(["commit", "-m", message], project)


if __name__ == "__main__":
    main()
