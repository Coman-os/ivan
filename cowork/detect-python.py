#!/usr/bin/env python3
"""Диагностика Python для проверок качества — только чтением.

Зачем. Проверки написаны на Python, а команды их запуска зовут интерпретатор
по имени, и единого имени не существует ни в одну сторону:

  · на Mac есть только `python3`; `python` и `py` отсутствуют — проверено
    на машине разработки;
  · на типичной Windows наоборот: `python3` нет, `python` появляется при
    отмеченной галочке установщика (по умолчанию снята), `py` — при
    установке с сайта и отсутствует при установке из магазина или Anaconda.

Заглушка магазина приложений. Windows кладёт в PATH исполняемый файл
`python3.exe`, который на любой запуск печатает «Python не найден» и выходит
ненулевым кодом. Он проходит проверку «файл существует и исполняется», то
есть выглядит рабочим интерпретатором вплоть до момента запуска. Отличается
только делом: настоящий Python на `-c` печатает ответ, заглушка — нет.

Что скрипт ДЕЛАЕТ: перебирает кандидатов, заставляет каждого выполнить код и
вернуть путь и версию в JSON, сравнивает числовую пару версии с минимумом.
Печатает итог человеку либо JSON (`--json`).

Чего НЕ делает по умолчанию: не пишет в каталог пакета и не правит команды
запуска. Прежняя редакция клала `python-path.json` рядом с собой и
переписывала `hooks/hooks.json` — на живой установке Codex 08.09.2026 это
кончилось «Operation not permitted»: каталог установленного плагина там
только для чтения, а подтверждение проверок привязано к содержимому файлов.
Запись результата — только в каталог данных плагина, если среда его даёт
(`PLUGIN_DATA` / `CLAUDE_PLUGIN_DATA`). Подстановка пути в команды осталась
за явным флагом `--apply` для сред с записываемым каталогом (Claude Code);
цена — на Codex её нет, и на Windows команды остаются на имени.

Четыре исхода, и они не сводятся друг к другу:
  ok        — найден подходящий Python, код выполнен;
  too_old   — интерпретатор есть, версия ниже минимума (названа);
  not_found — ни один кандидат не ответил как Python;
  blocked   — запуск кандидатов запрещён средой либо ответ неоднозначен.
«Запрещён» не превращается в «отсутствует»: это разные советы человеку.

Запускается навыком знакомства `ivan-setup` и по просьбе «проверь Python».

Класс: будильник (говорит, не меняет). Точка — установка и просьба.
Сигнал деградации — проверки молчат у получателя, у которого Python есть.
"""

import json
import os
import shutil
import subprocess
import sys

# Порядок перебора — от самого надёжного к самому неоднозначному.
#
# `py -3` первым: лаунчер Windows сам выбирает установленную версию 3.x и не
# зависит от того, дописал ли установщик что-либо в PATH. Он же не бывает
# заглушкой магазина.
#
# `python` последним: на старых системах это может оказаться Python 2, а на
# Windows — та самая заглушка. Берётся, только если первых двух нет.
CANDIDATES = (
    ["py", "-3"],
    ["python3"],
    ["python"],
)

# Ниже этой версии проверки не запускаются: самодиагностика пользуется
# `sys.stdlib_module_names`, появившимся в 3.10. Найденный ранний 3.x хуже
# отсутствия — он выглядит годным и падает на первом же запуске. Прежний
# минимум (3, 9) противоречил этому и снят 08.09.2026.
MIN_VERSION = (3, 10)

# Что печатает проба — JSON одной строкой. Прежняя редакция печатала путь
# через пробел и разбирала `split()`: путь вида `C:\\Program Files\\...`
# распадался на части, версия не разбиралась, настоящий Python выглядел
# заглушкой. `sys.executable` даёт абсолютный путь к настоящему файлу даже
# при вызове через лаунчер `py`.
PROBE = ("import sys,json;"
         "print(json.dumps({'executable':sys.executable,"
         "'version':list(sys.version_info[:3])}))")


def parse_probe_output(stdout):
    """(путь, (major, minor, micro)) из ответа пробы либо None.

    Берётся последняя непустая строка: обёртки печатают баннер перед ответом.
    """
    lines = [ln for ln in (stdout or "").splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        data = json.loads(lines[-1])
        path = data["executable"]
        version = tuple(int(x) for x in data["version"][:3])
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(path, str) or not path:
        return None
    return path, version


def classify(path, version):
    """Вердикт по разобранному ответу пробы."""
    if version[:2] < MIN_VERSION:
        return "too_old"
    if not os.path.isabs(path) or not os.path.exists(path):
        return "not_python"
    return "ok"


def probe(cmd, run=subprocess.run):
    """Запустить кандидата и сказать, что это.

    Возвращает словарь со `status` из {ok, too_old, not_python, blocked,
    missing} и, где есть, `path` / `version` / `error`.

    Проверяется ДЕЛОМ, а не наличием файла: заглушка магазина существует,
    исполняется и отвечает — просто не то. `run` подменяется в тестах.
    """
    try:
        r = run(cmd + ["-c", PROBE], capture_output=True, text=True, timeout=15)
    except FileNotFoundError:
        return {"status": "missing"}
    except PermissionError as exc:
        return {"status": "blocked", "error": str(exc)}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "blocked", "error": str(exc)}
    if r.returncode != 0:
        return {"status": "not_python"}
    parsed = parse_probe_output(r.stdout)
    if not parsed:
        return {"status": "not_python"}
    path, version = parsed
    return {"status": classify(path, version), "path": path,
            "version": "%d.%d.%d" % version, "version_tuple": list(version)}


def detect(run=subprocess.run):
    """Итог по всем кандидатам.

    Первый `ok` побеждает. Иначе: был `too_old` → он (с максимальной
    версией); был `blocked` → blocked; иначе not_found. Интерпретатор,
    которым запущен сам скрипт, — последний кандидат: он доказанно
    работает, но мог быть временным (окружение установщика).
    """
    tried = []
    for cmd in CANDIDATES:
        if not shutil.which(cmd[0]):
            tried.append({"command": " ".join(cmd), "status": "missing"})
            continue
        res = probe(cmd, run)
        res["command"] = " ".join(cmd)
        tried.append(res)
        if res["status"] == "ok":
            return _summary("ok", res, tried)
    if os.path.isabs(sys.executable) and os.path.exists(sys.executable):
        me = {"command": "интерпретатор запуска", "path": sys.executable,
              "version": "%d.%d.%d" % sys.version_info[:3],
              "version_tuple": list(sys.version_info[:3]),
              "status": classify(sys.executable, sys.version_info[:3])}
        tried.append(me)
        if me["status"] == "ok":
            return _summary("ok", me, tried)
    old = [t for t in tried if t["status"] == "too_old"]
    if old:
        best = max(old, key=lambda t: t["version_tuple"])
        return _summary("too_old", best, tried)
    if any(t["status"] == "blocked" for t in tried):
        return _summary("blocked", None, tried)
    return _summary("not_found", None, tried)


def _summary(status, hit, tried):
    out = {"status": status, "min_version": "%d.%d" % MIN_VERSION,
           "found": status == "ok", "tried": tried}
    if hit:
        out["python"] = hit.get("path")
        out["version"] = hit.get("version")
        out["via"] = hit.get("command")
    return out


def data_dir():
    """Каталог данных плагина, если среда его даёт. Иначе None — не пишем."""
    for var in ("PLUGIN_DATA", "CLAUDE_PLUGIN_DATA"):
        d = os.environ.get(var)
        if d:
            return d
    return None


def save_result(result):
    """Записать итог в каталог данных плагина. Отказ — не ошибка."""
    d = data_dir()
    if not d:
        return None
    try:
        os.makedirs(d, exist_ok=True)
        out = os.path.join(d, "python-path.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump({k: result.get(k) for k in
                       ("status", "found", "python", "version", "via")},
                      f, ensure_ascii=False, indent=2)
        return out
    except OSError:
        return None


# Как команда выглядит до подстановки. Ровно эти формы пишут оба сборщика.
NAME_PREFIXES = ('python3 "', 'python "')


def patch_commands(path):
    """Подставить найденный путь в команды запуска — только по `--apply`.

    Правится `hooks/hooks.json` установленного пакета. Работает там, где
    каталог пакета записываем (Claude Code). На Codex каталог только для
    чтения, и подтверждение проверок привязано к содержимому: правка либо
    не пройдёт, либо снимет подтверждение. Возвращает число изменённых
    команд; отказ среды — 0 с сообщением у вызывающего.

    Цена, которую надо знать: обновление плагина перезаписывает каталог, и
    подстановка слетает.
    """
    root = os.path.dirname(os.path.abspath(__file__))
    conf = os.path.join(root, "hooks", "hooks.json")
    if not os.path.exists(conf):
        return 0
    try:
        with open(conf, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return 0
    if '"' in path:
        return 0
    escaped = json.dumps(path, ensure_ascii=False)[1:-1].replace('"', '\\"')
    replacement = '\\"' + escaped + '\\" \\"'
    changed = 0
    for prefix in NAME_PREFIXES:
        marker = prefix.replace(' "', ' \\"')
        n = raw.count(marker)
        if n:
            raw = raw.replace(marker, replacement)
            changed += n
    if not changed:
        return 0
    try:
        json.loads(raw)
    except ValueError:
        return 0
    try:
        with open(conf, "w", encoding="utf-8") as f:
            f.write(raw)
    except OSError:
        return 0
    return changed


HUMAN = {
    "ok": "Python найден: {python} (версия {version}, через {via}). Код выполнен.",
    "too_old": ("Python есть, но старый: {version} по пути {python}. Нужен "
                "{min_version} или новее — три точка десять, не 3.1. "
                "Обновите с python.org и попросите проверить снова."),
    "not_found": ("Python на этой машине не найден: ни один кандидат не "
                  "ответил как интерпретатор.\n\n"
                  "Без него не работают автоматические проверки качества: "
                  "документы сохранятся без положенного оформления, и никто "
                  "об этом не скажет.\n\n"
                  "Что сделать: поставить Python {min_version} или новее с "
                  "python.org (Windows — отметить в установщике галочку "
                  "«Add python.exe to PATH»), затем попросить проверить "
                  "снова. Из Microsoft Store ставить не стоит: оттуда "
                  "приходит урезанная сборка."),
    "blocked": ("Проверить не удалось: запуск интерпретатора запрещён средой "
                "или ответ неоднозначен. Это НЕ означает, что Python "
                "отсутствует. Разрешите проверку либо выполните её из "
                "обычной сессии."),
}

EXIT = {"ok": 0, "too_old": 1, "not_found": 1, "blocked": 3}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    apply = "--apply" in argv

    result = detect()
    saved = save_result(result)
    if saved:
        result["saved_to"] = saved

    patched = 0
    if apply and result["status"] == "ok":
        patched = patch_commands(result["python"])
        result["patched_commands"] = patched

    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return EXIT[result["status"]]

    print(HUMAN[result["status"]].format(**result))
    if result["status"] == "ok":
        if apply and patched:
            print(f"Команды проверок переведены на этот путь ({patched} шт.).")
        elif apply:
            print("Подставить путь в команды не удалось: каталог пакета не "
                  "записываем либо команды уже на пути. Проверки остаются на "
                  "имени интерпретатора.")
        print("Это проверка из диалога. Запускает ли проверки сама платформа "
              "на событии — отдельный вопрос, его подтверждает только событие.")
    return EXIT[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
