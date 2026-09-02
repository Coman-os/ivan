#!/usr/bin/env python3
"""SessionStart: проверка, что обвязка качества действительно работает.

Говорит человеческим языком и один раз за сессию. Молчание проверок и их
исправная работа выглядят одинаково — этот хук их различает.
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Чем человек называет проверку. Имена файлов наружу не идут: они ничего не
# говорят тому, кто не разбирал устройство, и превращают сообщение в отчёт
# механика. Имена достаются только по прямому вопросу — правило 34, граница
# «витрина / инструкция».
CAPABILITY = {
    "post_write_anglicism_check": "проверка языка",
    "post_write_readme_sync": "сверка README-индексов",
    "post_write_doc_standard_check": "проверка стандарта оформления",
    "post_write_ssot_overlap_warn": "предупреждение о перекрытии с SSOT",
    "post_write_skill_map_check": "сверка карты навыков",
    "post_write_principles_map_sync": "сверка карты принципов",
    "post_write_client_folder_placement_check": "проверка размещения",
    "post_write_md_check": "проверка шапки документа",
    "post_write_memory_gate": "гейт записи в память",
    "check_deferred_actions": "проверка отложенного",
    "session_end_autocommit": "страховочный коммит (по умолчанию выключен)",
    "session_start_load_rules": "загрузка правил при старте",
    "session_start_selfcheck": "самопроверка при старте"
}

# Спутники: не хуки, а модули, которые хуки импортируют. Хук без спутника
# падает на первой строке — до всякой проверки того, что он сторожит.
COMPANIONS = ("_host_adapter.py", "_transcript_io.py")


def _root():
    """Корень пакета. Обе платформы дают переменную для хуков плагина."""
    return (os.environ.get("CLAUDE_PLUGIN_ROOT")
            or os.environ.get("PLUGIN_ROOT")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _registered(hooks_dir):
    """Имена, объявленные на событие в hooks.json.

    Файл на диске и объявленная регистрация — разные вещи: файл без
    регистрации не запустится никогда, регистрация без файла заставит
    платформу звать несуществующий скрипт. Расходятся молча оба раза.
    """
    import re
    path = os.path.join(hooks_dir, "hooks.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return None
    return set(re.findall(r"hooks/([A-Za-z0-9_-]+\.py)", raw))


def _runs_by_name(hooks_dir):
    """Команды зовут ИМЯ интерпретатора, а не путь.

    Имя разрешается через PATH, и на Windows приводит к заглушке магазина —
    проверки замолкают. Знакомство подставляет абсолютный путь, но
    обновление плагина перезаписывает каталог и откатывает подстановку.
    Откат обязан быть слышен: это ровно то состояние, в котором человек
    считает себя защищённым, а защиты нет.

    На Mac имя разрешается исправно, поэтому это не тревога, а повод
    переподставить путь — сообщение соответствующее.
    """
    import re
    path = os.path.join(hooks_dir, "hooks.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return 0
    return len(re.findall(r'"command":\s*"python3? ', raw))


def _broken_imports(hooks_dir, present):
    """Хуки, которые не запустятся: не компилируются либо просят чужой модуль.

    Разбором, а не запуском: хук на пустом входе законно выходит нулём, и
    запуск дефекта не покажет.

    Сверяется со ВСЕМ, что лежит рядом, включая спутники: они и есть то,
    что хуки импортируют. Первая редакция брала только сами хуки (`present`
    без ведущего подчёркивания) — и объявляла сломанными ровно те три, что
    работают, потому что `import _host_adapter` выглядел чужим модулем.
    Поймано первым же прогоном на исправном пакете: код выхода был нулевой,
    дефект виден только в тексте.
    """
    import ast
    stdlib = set(getattr(sys, "stdlib_module_names", ()))
    try:
        stems = {n[:-3] for n in os.listdir(hooks_dir) if n.endswith(".py")}
    except OSError:
        stems = {n[:-3] for n in present}
    broken = []
    for name in sorted(present):
        path = os.path.join(hooks_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), name)
        except (OSError, SyntaxError):
            broken.append(name)
            continue
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module.split(".")[0]]
            for m in mods:
                if m not in stdlib and m not in stems:
                    broken.append(name)
                    break
            if name in broken:
                break
    return broken


def main():
    root = _root()
    hooks_dir = os.path.join(root, "hooks")

    present = set()
    if os.path.isdir(hooks_dir):
        present = {n for n in os.listdir(hooks_dir)
                   if n.endswith(".py") and not n.startswith("_")}

    missing_companions = [c for c in COMPANIONS
                          if not os.path.exists(os.path.join(hooks_dir, c))]
    registered = _registered(hooks_dir)
    broken = _broken_imports(hooks_dir, present) if present else []

    # Три разных класса неисправности, которые нельзя валить в одну кучу:
    # «файла нет» лечится пересборкой, «не объявлен» — правкой конфига,
    # «падает на импорте» — довозом спутника. Человеку они выглядят
    # одинаково (тишина), значит различать обязаны мы.
    orphans = sorted(present - registered) if registered is not None else []
    ghosts = sorted(registered - present) if registered is not None else []
    working = sorted(present - set(broken) - set(orphans)) if registered is not None \
        else sorted(present - set(broken))

    # Сам себя из отчёта исключаю: «самопроверка работает» человеку ничего
    # не даёт (он читает её вывод — значит работает), а в выпотрошенном
    # пакете превращает отчёт в ложное утешение: всё пропало, зато я на
    # месте. Найдено прогоном сценария «проверок нет вовсе».
    working = [n for n in working if n != "session_start_selfcheck.py"]
    broken = [n for n in broken if n != "session_start_selfcheck.py"]

    lines = []
    if registered is None:
        # Без списка регистраций нельзя утверждать, что проверки работают:
        # файл на диске не есть объявленное событие. Прежняя редакция
        # говорила «не нахожу список» и следом бодро перечисляла работающее —
        # то самое ложное чувство защиты, против которого хук и заведён.
        lines.append("Не нахожу список проверок — похоже, поставка неполная. "
                     "Файлы проверок на месте, но включены ли они, отсюда не "
                     "видно, и считать их работающими нельзя. Сообщите тем, "
                     "кто передал поставку.")
        working = []
    elif not present:
        lines.append("Проверок качества в этой установке нет вовсе. "
                     "Документы никто не проверит — оформление держится "
                     "только на мне.")
    elif not working:
        lines.append("Проверки качества не работают — ни одна. Я по-прежнему "
                     "полезен, но автоматической страховки сейчас нет: "
                     "оформление документов держится только на мне.")

    if working:
        names = [CAPABILITY.get(n[:-3], n[:-3]) for n in working]
        lines.append("Проверки качества работают: " + ", ".join(names) + ".")

    if missing_companions:
        lines.append("Часть проверок не запустится: не хватает файлов, "
                     "которые они используют. Это чинится пересборкой "
                     "поставки, а не настройкой.")
    if broken:
        names = [CAPABILITY.get(n[:-3], n[:-3]) for n in broken]
        lines.append("Не запустятся из-за неполной поставки: "
                     + ", ".join(names) + ".")
    if ghosts:
        lines.append("Заявлены, но не приехали: "
                     + ", ".join(CAPABILITY.get(n[:-3], n[:-3]) for n in ghosts)
                     + ". Считать их работающими нельзя.")
    if orphans:
        lines.append("Приехали, но не включены: "
                     + ", ".join(CAPABILITY.get(n[:-3], n[:-3]) for n in orphans)
                     + ".")

    # Подтверждение проверок — только у Codex: он не запускает
    # неподтверждённые и делает это молча. У Claude Code подтверждения нет
    # вовсе, и говорить о нём значит пугать несуществующим.
    by_name = _runs_by_name(hooks_dir)
    if by_name and working:
        lines.append("Проверки запускаются по имени Python, а не по "
                     "найденному пути. Здесь они работают, но на другой "
                     "машине — особенно на Windows — могут молча не "
                     "запуститься. Лечится повторным знакомством: "
                     "скажите /ivan-setup. Так бывает после обновления.")

    if False and working:
        lines.append("Если вы ещё не подтвердили запуск проверок при первой "
                     "установке — они не работают, пока вы этого не сделали. "
                     "Подтверждение слетает после каждого обновления.")

    context = ("# Состояние проверок качества\n\n" + "\n\n".join(lines)
               + "\n\nЭто для тебя, не для пересказа человеку. Скажи о "
               "неисправности, только если она есть и мешает делу, — "
               "одной фразой, его словами, без имён файлов. Всё работает — "
               "молчи. Спросит подробности — назови поимённо, там имена "
               "уместны.\n\nЧего эта проверка не видит: она сама запущена "
               "тем же способом, что и остальные. Не запустился бы способ — "
               "не было бы и этого сообщения.")

    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Диагностика, роняющая сессию, хуже отсутствия диагностики:
        # человек получил бы поломку вместо сообщения о поломке.
        sys.exit(0)
