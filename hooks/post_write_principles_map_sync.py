#!/usr/bin/env python3
"""
PostToolUse hook: после Write/Edit одного из 3 SSOT-документов принципов
(operating-principles, agent-design-principles, pipeline-health-tracking-pattern)
ИЛИ самой карты (principles-architecture.md) — сверяет реестр принципов
«карта vs территория» по всем слоям.

Ловит класс ошибки из ревью Fable 5 (2026-06-10, находки C1-C3): принцип добавлен
в SSOT, но не отражён в карте (таблица слоя + Decision Tree) и gateway-правилах.
Аналог правила «создал skill → обнови карту» «создал skill → обнови capabilities-map», для принципов.

Does NOT auto-fix — блокирующий алерт с директивной инструкцией (Принцип B4).
"""

import json
import os
import re
import sys

MAP_REL = "coman-os/scl/intelligence/principles/principles-architecture.md"

# SSOT-файлы и правила извлечения номеров принципов из них.
# id_format — как номер принципа выглядит в карте.
SSOT_SPECS = [
    {
        "rel": "coman-os/knowledge-core/principles/operating-principles.md",
        "layer": "Слой 1",
        "heading_re": re.compile(r"^## Принцип (\d+)[:\s]", re.MULTILINE),
        "map_row_re": re.compile(r"^\| 1\.(\d+) \|", re.MULTILINE),
        "map_id": "1.{n}",
        "gateway": "правило «принципы при создании нового»",
    },
    {
        "rel": "coman-os/shared/agent-design-principles.md",
        "layer": "Слой 2 §A",
        "heading_re": re.compile(r"^## Принцип (\d+)[:\s]", re.MULTILINE),
        "map_row_re": re.compile(r"^\| 2\.A(\d+) \|", re.MULTILINE),
        "map_id": "2.A{n}",
        "gateway": "правило «принципы при создании агента»",
    },
    {
        "rel": "coman-os/shared/agent-design-principles.md",
        "layer": "Слой 2 §B",
        "heading_re": re.compile(r"^## Принцип B(\d+)[:\s]", re.MULTILINE),
        "map_row_re": re.compile(r"^\| 2\.B(\d+) \|", re.MULTILINE),
        "map_id": "2.B{n}",
        "gateway": "CLAUDE.md правило «создал skill → обнови карту»",
    },
    {
        "rel": "coman-os/shared/pipeline-health-tracking-pattern.md",
        "layer": "Слой 3",
        "heading_re": re.compile(r"^## Принцип (\d+)[:\s]", re.MULTILINE),
        "map_row_re": re.compile(r"^\| 3\.(\d+) \|", re.MULTILINE),
        "map_id": "3.{n}",
        "gateway": "правило «проектирование pipeline» (корень + coman-os/shared/CLAUDE.md)",
    },
]

WATCHED_BASENAMES = {os.path.basename(MAP_REL)} | {
    os.path.basename(s["rel"]) for s in SSOT_SPECS
}


def read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, PermissionError):
        return None


def main():
    hook_input = json.loads(sys.stdin.read())
    if hook_input.get("tool_name", "") not in ("Write", "Edit"):
        return
    file_path = hook_input.get("tool_input", {}).get("file_path", "")
    if not file_path or os.path.basename(file_path) not in WATCHED_BASENAMES:
        return

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    if not project_dir:
        return

    map_path = os.path.join(project_dir, MAP_REL)
    map_text = read_file(map_path)
    if map_text is None:
        return

    problems = []
    for spec in SSOT_SPECS:
        ssot_path = os.path.join(project_dir, spec["rel"])
        ssot_text = read_file(ssot_path)
        if ssot_text is None:
            continue
        in_ssot = {int(m) for m in spec["heading_re"].findall(ssot_text)}
        in_map = {int(m) for m in spec["map_row_re"].findall(map_text)}
        missing_in_map = sorted(in_ssot - in_map)
        orphaned_in_map = sorted(in_map - in_ssot)
        if missing_in_map:
            ids = ", ".join(spec["map_id"].format(n=n) for n in missing_in_map)
            problems.append(
                f"  - {spec['layer']}: принцип(ы) {ids} есть в {spec['rel']}, "
                f"но отсутствуют в таблице слоя карты. Fix: добавить строку в таблицу "
                f"«{spec['layer']}» карты + проверить Decision Tree (нужна ли строка-триггер) "
                f"+ сверить {spec['gateway']}."
            )
        if orphaned_in_map:
            ids = ", ".join(spec["map_id"].format(n=n) for n in orphaned_in_map)
            problems.append(
                f"  - {spec['layer']}: строка(и) {ids} есть в карте, но принципа нет в "
                f"{spec['rel']}. Fix: удалить строку из карты или восстановить принцип в SSOT."
            )

    if problems:
        print(json.dumps({
            "decision": "block",
            "reason": (
                "[principles_map_sync] Карта принципов разошлась с территорией "
                "(SSOT-документами слоёв).\nРасхождения:\n"
                + "\n".join(problems)
                + f"\nПосле правки карты — обновить поле «Обновлён» и «Историю изменений» "
                f"в {MAP_REL}.\n"
                "SSOT: ревью 2026-06-10-fable5-principles-review.md (находки C1-C3), "
                "operating-principles §11 (поддерживаемость)."
            ),
        }))


if __name__ == "__main__":
    main()
