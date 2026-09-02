#!/usr/bin/env python3
"""Найти настоящий Python и записать его абсолютный путь в конфиг поставки.

Зачем. Команды запуска проверок прописаны именем интерпретатора, а единого
имени не существует ни в одну сторону:

  · на Mac есть только `python3`; `python` и `py` отсутствуют — проверено
    на машине разработки;
  · на типичной Windows наоборот: `python3` нет, `python` появляется при
    отмеченной галочке установщика (по умолчанию снята), `py` — при
    установке с сайта и отсутствует при установке из магазина или Anaconda.

Замена `python3` на `python` переносит поломку с Windows на Mac, а не
устраняет её. Поэтому имя не угадывается, а один раз определяется при
установке; дальше команды зовут найденный ПУТЬ.

Заглушка магазина приложений. Windows кладёт в PATH исполняемый файл
`python3.exe`, который на любой запуск печатает «Python не найден» и выходит
ненулевым кодом. Он проходит проверку «файл существует и исполняется», то
есть выглядит рабочим интерпретатором вплоть до момента запуска. Отличается
только делом: настоящий Python на `-c` печатает версию, заглушка — нет.

Почему не переписать проверки на Node, устранив зависимость целиком:
отвергнуто по цене — пять скриптов, из них проверка документов на 700 строк.

Запускается навыком знакомства `ivan-setup` один раз при установке.
Печатает человекочитаемый итог и кладёт JSON рядом с собой.

Класс: регламент (часть обвязки установки). Точка — установка поставки.
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

# Ниже этой версии наши проверки не запускаются: они пользуются
# `sys.stdlib_module_names` (3.10+) и разбором через ast с современными
# полями. Найденный Python 2 или ранний 3.x хуже отсутствия — он выглядит
# годным и падает на первом же запуске.
MIN_VERSION = (3, 9)

# Что печатает проба. Спрашиваем именно ПУТЬ и версию одной строкой:
# `sys.executable` даёт абсолютный путь к настоящему исполняемому файлу даже
# тогда, когда звали через лаунчер `py`, — а нам нужен путь, а не имя.
PROBE = ("import sys;"
         "print('OK', sys.executable, '%d.%d' % sys.version_info[:2])")


def probe(cmd):
    """Запустить кандидата и убедиться, что это настоящий Python.

    Возвращает (абсолютный путь, версия) либо None.

    Проверяется ДЕЛОМ, а не наличием файла: заглушка магазина существует,
    исполняется и отвечает — просто не то. Единственный надёжный различитель
    — заставить кандидата выполнить код и вернуть ожидаемое.
    """
    try:
        r = subprocess.run(cmd + ["-c", PROBE],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    parts = (r.stdout or "").strip().split()
    # Метка OK — против кандидата, который вернул ноль, но напечатал не то
    # (обёртки, печатающие баннер; заглушки, отвечающие подсказкой).
    if len(parts) < 3 or parts[0] != "OK":
        return None
    path, version = parts[1], parts[2]
    try:
        major, minor = (int(x) for x in version.split(".")[:2])
    except ValueError:
        return None
    if (major, minor) < MIN_VERSION:
        return None
    if not os.path.isabs(path) or not os.path.exists(path):
        return None
    return path, version


def detect():
    """Первый кандидат, оказавшийся настоящим Python нужной версии."""
    # Интерпретатор, которым запущен сам этот скрипт, — уже доказанный
    # рабочий Python: он выполняет этот код. Но берётся он НЕ первым: скрипт
    # мог быть запущен временным интерпретатором (виртуальное окружение
    # установщика), которого в следующей сессии не будет. Сначала ищем
    # устойчивое имя в системе, свой путь — как запасной.
    for cmd in CANDIDATES:
        if not shutil.which(cmd[0]):
            continue
        found = probe(cmd)
        if found:
            return found[0], found[1], " ".join(cmd)
    if os.path.isabs(sys.executable) and os.path.exists(sys.executable):
        return (sys.executable,
                "%d.%d" % sys.version_info[:2],
                "интерпретатор запуска")
    return None, None, None


# Как команда выглядит до подстановки. Ровно эти формы пишут оба сборщика.
NAME_PREFIXES = ('python3 "', 'python "')


def patch_commands(path):
    """Подставить найденный путь в команды запуска проверок.

    Пока команда зовёт ИМЯ, запуск остаётся вероятным: имя разрешается через
    PATH, а PATH на Windows приводит к заглушке магазина. Подставленный
    абсолютный путь делает запуск определённым.

    Правится `hooks/hooks.json` установленного пакета — тот самый файл, из
    которого платформа берёт команды. Обе поставки хранят его одинаково, и
    правка одна на обе.

    Возвращает число изменённых команд.

    ЦЕНА, которую надо знать: обновление плагина перезаписывает каталог, и
    подстановка слетает — команды возвращаются к имени. На Mac это ничего не
    меняет (имя там разрешается), на Windows проверки замолкают снова.
    Ловит это самодиагностика при старте: она видит команды и говорит, если
    подстановка откатилась.
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

    # Кавычки вокруг пути обязательны: он часто содержит пробелы — и в
    # каталоге программ Windows, и в домашней папке с именем и фамилией.
    # Без кавычек команда распадается на части, и запускается не то.
    quoted = json.dumps(path, ensure_ascii=False) if '"' not in path else None
    if quoted is None:
        return 0
    # Внутри JSON-строки кавычки уже экранированы, поэтому подставляем
    # экранированную форму, а не сырую.
    escaped = quoted[1:-1].replace('"', '\\"')
    replacement = '\\"' + escaped + '\\" \\"'

    changed = 0
    for prefix in NAME_PREFIXES:
        marker = prefix[:-1] + ' \\"'  # `python3 \"` в тексте JSON
        marker = prefix.replace(' "', ' \\"')
        n = raw.count(marker)
        if n:
            raw = raw.replace(marker, replacement)
            changed += n
    if not changed:
        return 0
    try:
        json.loads(raw)  # не отдать платформе сломанный конфиг
    except ValueError:
        return 0
    try:
        with open(conf, "w", encoding="utf-8") as f:
            f.write(raw)
    except OSError:
        return 0
    return changed


def main():
    path, version, via = detect()

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "python-path.json")
    if not path:
        # Молчание здесь — ровно тот дефект, против которого заведена
        # самодиагностика: «не нашли» обязано звучать, а не выходить кодом.
        print("Python на этой машине не найден.\n"
              "\n"
              "Без него не работают автоматические проверки качества: "
              "документы сохранятся без положенного оформления, и никто об "
              "этом не скажет.\n"
              "\n"
              "Что сделать: поставить Python с python.org (Windows — "
              "отметить в установщике галочку «Add python.exe to PATH»), "
              "затем запустить знакомство заново.\n"
              "\n"
              "Из Microsoft Store ставить не стоит: оттуда приходит "
              "урезанная сборка, с которой проверки работают не всегда.")
        try:
            with open(out, "w", encoding="utf-8") as f:
                json.dump({"found": False}, f, ensure_ascii=False, indent=2)
        except OSError:
            pass
        return 1

    try:
        with open(out, "w", encoding="utf-8") as f:
            json.dump({"found": True, "python": path, "version": version},
                      f, ensure_ascii=False, indent=2)
    except OSError as exc:
        print(f"Python найден ({path}), но записать это рядом с собой не "
              f"получилось: {exc}\nПроверки будут запускаться по имени, а не "
              f"по пути, — на Windows это ненадёжно.")
        return 1

    patched = patch_commands(path)
    print(f"Python найден: {path} (версия {version}, через {via}).")
    if patched:
        print(f"Проверки качества переведены на этот путь ({patched} шт.) — "
              f"запуск больше не зависит от того, какое имя понимает система.")
    else:
        print("Команды запуска остались на имени интерпретатора: подставить "
              "путь не удалось. На этой машине проверки работают, но после "
              "переноса на другую могут замолчать.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
