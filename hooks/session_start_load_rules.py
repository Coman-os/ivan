#!/usr/bin/env python3
"""SessionStart: безусловная загрузка правил Ивана в контекст сессии.

Аналог нативного CLAUDE.md: правила впрыскиваются при старте каждой сессии,
а не по срабатыванию описания навыка ivan-rules. Закрывает главное
ограничение платформы (плагин не доставляет CLAUDE.md).
"""
import json
import os
import re
import sys


def main() -> None:
    root = os.environ.get("CLAUDE_PLUGIN_ROOT",
                          os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(root, "skills", "ivan-rules", "SKILL.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        sys.exit(0)  # правил нет — молча не мешаем сессии
    body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S).strip()
    # Договор пары «человек ↔ Иван» (WORKING-AGREEMENT.md в корне проекта).
    # Прежние имена IVAN-CONTEXT.md / ivan-context.md читаются для совместимости
    # с установками, сделанными до переименования.
    # Без неё §28 деградирует: почти всё становится «невычислимым».
    card = ""
    proj = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    # Слой организации — общий для сотрудников одной компании (клиенты, что
    # конфиденциально, конвенции). Наложение, не ветвление: личная карточка
    # уточняет его, не заменяет. Читается ПЕРВЫМ, личная поверх.
    for org_name in ("ORG-CONTEXT.md", "org-context.md"):
        op = os.path.join(proj, org_name)
        if os.path.exists(op):
            try:
                with open(op, encoding="utf-8") as f:
                    card += ("\n\n---\n\n# Контекст организации\n\n"
                             "Общий для всех в компании. Личная карточка ниже "
                             "уточняет его, не отменяет.\n\n" + f.read())
            except OSError:
                pass
            break
    for name in ("WORKING-AGREEMENT.md", "working-agreement.md",
                 "IVAN-CONTEXT.md", "ivan-context.md"):
        cp = os.path.join(proj, name)
        if os.path.exists(cp):
            try:
                with open(cp, encoding="utf-8") as f:
                    card += ("\n\n---\n\n# Договор о работе с этим человеком\n\n"
                            "Сторону человека заполнил он сам. Опирайся на неё в §28: что "
                            "здесь названо — ВЫЧИСЛИМО, не переспрашивай.\n\n" + f.read())
            except OSError:
                pass
            break
    else:
        card += ("\n\n---\n\n**Личной карточки контекста нет.** В корне проекта не найден "
                "WORKING-AGREEMENT.md. Без него ты не знаешь ни роли человека, ни что "
                "считается «наружу», ни класса петли — §28 работает вполсилы. "
                "При первой же задаче предложи заполнить его по шаблону "
                "working-agreement-template.md (лежит в корне плагина): 10 минут, "
                "выключает лишние переспрашивания.")

    context = (
        "# Правила Ивана — активны всю сессию\n\n"
        "Загружены безусловно хуком SessionStart (аналог CLAUDE.md родной "
        "установки). Применяй на протяжении всей работы, не только при явном "
        "вызове.\n\n" + body + card +
        "\n\n---\n\nЕсли в этой установке настроена синхронизация с удалённым "
        "Knowledge Hub (навык kh-sync или аналог) — при задачах, касающихся "
        "Hub, клонируй его сразу по протоколу навыка, не спрашивая разрешения."
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }}, ensure_ascii=False))


if __name__ == "__main__":
    main()
