#!/usr/bin/env python3
"""
PostToolUse hook: после записи .md файла проверяет:
1. Наличие таблицы метаданных (| Параметр | Значение |)
2. Наличие поля «Назначение» — карточка файла для агентов (см. requirements.md §2.0)

Метаданные отсутствуют → block (как было).
Метаданные есть, «Назначение» отсутствует → warn (мягкое напоминание во время backfill «Назначение»).
После завершения backfill «Назначение» переключить «Назначение» в block.

Skips README/CHANGELOG, node_modules/.git/.claude/__pycache__.
"""

import json
import os
import re
import sys


# CLAUDE.md — управляющий файл харнесса Claude Code (иерархический, грузится
# автоматически по папкам), не KH-документ; таблица метаданных ему не нужна
# (корневой CLAUDE.md её тоже не имеет).
SKIP_FILENAMES = {"README.md", "CHANGELOG.md", "changelog.md", "CLAUDE.md", "SETUP.md", "INSTANCE.md"}
SKIP_PATH_PARTS = {"node_modules", ".git", ".claude", "__pycache__", "legacy", "archive", "versions", "Outbound", "CRM", ".sync-state"}

# Документы по шаблонам правила «стандарт оформления документов» (instructions/templates/) используют
# YAML frontmatter как единственный источник метаданных — без таблицы.
TEMPLATE_TYPES = {"decision", "scope", "concept", "spec"}
TEMPLATE_PATH_PARTS = {"decisions", "concepts", "specs"}

YAML_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
YAML_TYPE_RE = re.compile(r"^type:\s*([a-zA-Z_-]+)\s*$", re.MULTILINE)

# Skill-файлы (Claude Code skills / commands) используют YAML frontmatter
# name/description как единственный источник метаданных — таблица дублировала бы
# YAML (kh-check Проверка A / document-writing-standards §Формат шапки: skills → YAML). Обычно живут в .claude/
# (уже в SKIP), но hand-authored skill-ИСТОЧНИКИ дистрибутива лежат вне .claude.
YAML_NAME_RE = re.compile(r"^name:\s*\S", re.MULTILINE)
YAML_DESC_RE = re.compile(r"^description:\s*", re.MULTILINE)

# Meeting-views slice-v3 (SP-114): выжимки-view (например {date}-views/okr-master.md)
# используют YAML frontmatter (title/team/date/meeting_type/audience) как
# единственный источник метаданных — таблица создала бы запрещённый двойной
# заголовок (kh-check Проверка A / document-writing-standards §Формат шапки).
YAML_MEETING_VIEW_RE = re.compile(r"^meeting_type:\s*\S", re.MULTILINE)

METADATA_MARKERS = (
    "| Параметр | Значение |",
    "| Поле | Значение |",
    "| **Статус**",
    "| **Версия**",
    "| **Owner**",
    "**Версия:**",
    "**Статус:**",
)

NAZNACHENIE_PATTERN = re.compile(r"^\|\s*\*?\*?Назначение\*?\*?\s*\|", re.MULTILINE)


def is_template_based_document(file_path: str, content: str) -> bool:
    """Документ создан по шаблону правила «стандарт оформления документов» (decisions/concepts/specs/scope)?

    Эти шаблоны используют YAML frontmatter как единственный источник метаданных.
    Таблица дублирует YAML — нарушение kh-check Проверка A / document-writing-standards §Формат шапки.
    """
    path_parts = set(file_path.split(os.sep))
    if path_parts & TEMPLATE_PATH_PARTS:
        return True

    yaml_match = YAML_FRONTMATTER_RE.match(content)
    if yaml_match:
        type_match = YAML_TYPE_RE.search(yaml_match.group(1))
        if type_match and type_match.group(1) in TEMPLATE_TYPES:
            return True

    return False


def is_meeting_view(file_path: str, content: str) -> bool:
    """Meeting-view slice-v3 (SP-114)? Каталог `*-views/` ИЛИ frontmatter с
    meeting_type. YAML — единственный источник метаданных, таблица не требуется."""
    parent = os.path.basename(os.path.dirname(file_path))
    if parent.endswith("-views"):
        return True
    yaml_match = YAML_FRONTMATTER_RE.match(content)
    return bool(yaml_match and YAML_MEETING_VIEW_RE.search(yaml_match.group(1)))


def is_skill_source(content: str) -> bool:
    """Skill-файл (YAML frontmatter с name + description)? YAML — единственный
    источник метаданных, таблица не требуется (kh-check Проверка A / document-writing-standards §Формат шапки)."""
    yaml_match = YAML_FRONTMATTER_RE.match(content)
    if not yaml_match:
        return False
    block = yaml_match.group(1)
    return bool(YAML_NAME_RE.search(block) and YAML_DESC_RE.search(block))


def main():
    hook_input = json.loads(sys.stdin.read())

    file_path = hook_input.get("tool_input", {}).get("file_path", "")

    if not file_path.endswith(".md"):
        return

    basename = os.path.basename(file_path)
    if basename in SKIP_FILENAMES:
        return

    for part in SKIP_PATH_PARTS:
        if part in file_path.split(os.sep):
            return

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            head = ""
            for i, line in enumerate(f):
                if i >= 25:
                    break
                head += line
    except (FileNotFoundError, PermissionError):
        return

    # Документы по шаблонам правила «стандарт оформления документов» имеют YAML frontmatter
    # как единственный источник метаданных — таблица не требуется.
    if is_template_based_document(file_path, head):
        return

    # Skill-источники (YAML frontmatter) — YAML как метаданные, без таблицы.
    if is_skill_source(head):
        return

    # Meeting-views slice-v3 (SP-114) — YAML frontmatter выжимки, без таблицы.
    if is_meeting_view(file_path, head):
        return

    has_metadata = any(marker in head for marker in METADATA_MARKERS)

    if not has_metadata:
        print(json.dumps({
            "decision": "block",
            "reason": (
                f"[post_write_md_check] Файл {basename} создан без таблицы метаданных.\n"
                f"Fix: вставить в начало файла (до H1) шаблон:\n"
                f"  | Параметр | Значение |\n"
                f"  |----------|----------|\n"
                f"  | Версия | 1.0.0 |\n"
                f"  | Статус | draft |\n"
                f"  | Owner | <автор> |\n"
                f"  | Создан | <YYYY-MM-DD> |\n"
                f"  | Обновлён | <YYYY-MM-DD> |\n"
                f"  | Назначение | <1–3 предложения, ≤100 слов: для кого, что забирают> |\n"
                f"Исключение: README.md / CHANGELOG.md; slice-v3 meeting-view "
                f"(YAML frontmatter с `meeting_type:`, напр. {{date}}-views/okr-master.md) — "
                f"освобождён (SP-114). ⚠️ Обычная выжимка встречи (product/ad-hoc, без "
                f"`meeting_type:` в YAML) под это исключение НЕ попадает — ей нужна "
                f"таблица метаданных ИЛИ явное добавление в SKIP_FILENAMES. Если тип "
                f"документа не требует метаданных — добавить файл в SKIP_FILENAMES этого hook'а."
            ),
        }))
        return

    has_naznachenie = bool(NAZNACHENIE_PATTERN.search(head))

    if not has_naznachenie:
        # Warn (не блокируем) пока идёт backfill «Назначение».
        # После завершения backfill — заменить на decision="block".
        print(json.dumps({
            "decision": "warn",
            "reason": (
                f"[post_write_md_check] {basename}: нет поля «Назначение» в таблице метаданных.\n"
                f"Fix: добавить строку в таблицу метаданных:\n"
                f"  | Назначение | <1–3 предложения, ≤100 слов: для кого документ, "
                f"что забирают, когда читать> |\n"
                f"Это карточка файла для агентов (kh-check Проверка A). "
                f"SSOT: requirements.md §2.0."
            ),
        }), file=sys.stderr)


if __name__ == "__main__":
    main()
