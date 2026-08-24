#!/usr/bin/env python3
"""
PostToolUse hook: ловит product/pipeline-инженерные артефакты, ошибочно
положенные в клиентскую папку (`{client}-knowledge-hub/**`).

Класс ошибки: файл про инженерию продукта (dry-run модели ME, отчёт о drift'е
экстракции, spec, review, audit) кладётся рядом со *входом* (клиентские данные —
фикстура), а не по *предмету*. Предмет — CoMan OS / pipeline, дом — `drafts/`
(например `drafts/meeting-sync/specs/` для ME, `drafts/type-drift/` для
экстракции) или `coman-os/shared/`. Пример: dry-run модели и extraction-drift
ошибочно легли в клиентскую папку вместо предметной.

Сигнал (перегрузка `audience` осознана): триггерим по `type ∈ ENG_TYPES` ЛИБО по
product-eng-маркеру в имени файла — не по `audience: internal-engineering`, т.к.
это поле клиент-доменные доки (knowledge-core, cross-meeting-map, glossary) ставят
ради опт-аута anglicism-хука, и триггер по нему давал бы ложные срабатывания.

Мягкий warn (не block): риск ложного на граничном client-domain реален; человек
подтверждает. Escalate в block — только если warn начнут обходить.

Три критерия (Принцип 11): точка — Write/Edit под clients/**; каденция — каждый
такой Write; сигнал деградации — `grep` product-eng type/маркеров под clients/ > 0.
"""

import json
import os
import re
import sys


# Инженерные типы документа (frontmatter `type:` или таблица метаданных).
ENG_TYPE_RE = re.compile(
    r"^\s*\|?\s*\**type\**\s*[:|]\s*(spec|review|analysis|audit|dry-?run|benchmark|evaluation)\b",
    re.MULTILINE | re.IGNORECASE,
)
# Product-eng-маркеры в ИМЕНИ файла (ловит type-less случаи вроде extraction-drift).
ENG_NAME_RE = re.compile(
    r"(drift|dry-?run|extraction-|type-drift|matching-engine|-audit\b|-benchmark\b|l1-|l2-|pipeline-)",
    re.IGNORECASE,
)

# Клиентская зона, где ошибка живёт: instance-overlay клиента.
# Anchor — суффикс "-knowledge-hub" в любом компоненте пути (clean-конвенция).
CLIENT_ANCHOR_SUFFIX = "-knowledge-hub"
# Внутри клиентской зоны — инфраструктура/конфиг/архив: не предмет правила.
ALLOWLIST_PARTS = {"_sync-schema", ".sync-config", ".sync-state", "templates", "_archive", "_changes"}

SKIP_FILENAMES = {"README.md", "CHANGELOG.md", "changelog.md", "CLAUDE.md"}


def main():
    hook_input = json.loads(sys.stdin.read())
    file_path = hook_input.get("tool_input", {}).get("file_path", "")

    if not file_path.endswith(".md"):
        return

    basename = os.path.basename(file_path)
    if basename in SKIP_FILENAMES:
        return

    parts = set(file_path.split(os.sep))
    # Только внутри {client}-knowledge-hub/**
    if not any(p.endswith(CLIENT_ANCHOR_SUFFIX) for p in parts):
        return
    if parts & ALLOWLIST_PARTS:
        return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            head = ""
            for i, line in enumerate(f):
                if i >= 15:
                    break
                head += line
    except (FileNotFoundError, PermissionError):
        return

    by_type = bool(ENG_TYPE_RE.search(head))
    by_name = bool(ENG_NAME_RE.search(basename))
    if not (by_type or by_name):
        return

    trigger = "type" if by_type else "имя файла"
    print(json.dumps({
        "decision": "warn",
        "reason": (
            f"[client_folder_placement] {basename} лежит в клиентской папке, но выглядит как "
            f"product/pipeline-инженерия (сработал: {trigger}).\n"
            f"Правило: размещение по ПРЕДМЕТУ выхода, не по расположению входа. Клиентские данные "
            f"часто лишь фикстура — предмет остаётся CoMan OS / pipeline.\n"
            f"Кандидаты-дома: `drafts/meeting-sync/specs/` (Matching Engine), `drafts/type-drift/` "
            f"(экстракция), `coman-os/shared/`. Класс: данные клиента как фикстура → предмет остаётся CoMan OS.\n"
            f"Если это client-domain контент (знания/термины/бизнес клиента) — игнорируй warn. "
            f"См. context-distribution-rules.md §4 «Product-инженерия на клиентской фикстуре»."
        ),
    }), file=sys.stderr)


if __name__ == "__main__":
    main()
