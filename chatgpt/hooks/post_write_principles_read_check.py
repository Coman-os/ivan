#!/usr/bin/env python3
"""PostToolUse hook: перед созданием новой сущности читаются принципы (§7).

ПРОБЛЕМА, КОТОРУЮ ЗАКРЫВАЕТ. §7 требует `Read` operating-principles.md перед
созданием новой сущности (модель, процесс, роль, навык, документ) — и до
2026-09-01 объявлял себя культурой с обоснованием «выполнение Read не
наблюдаемо tool-событием, hook невозможен».

Обоснование неверно. `Read` — ровно tool-событие, и оно лежит в транскрипте
сессии. Неуловимо намерение прочесть; наблюдаем факт, что НЕ прочли к моменту
записи. Правило лгало о своём классе — дефект по §24-bis.

ЗАМЕР ДО ЗАВЕДЕНИЯ (сухой прогон по 137 транскриптам, 2026-09-01): хук
сработал бы 5 раз за месяц, ложных срабатываний в выборке нет — все пять на
настоящих spec/concept. Для сравнения, тем же методом отвергнуты кандидаты:
§13 и §23 (0 срабатываний — не на что), §34 (4 срабатывания, все ложные:
ловился свод поставки, который само §34 исключает как инструкцию).

ЧТО ДЕЛАЕТ. На `Write` нового документа-сущности (concepts / decisions /
specs) проверяет, был ли в этой сессии `Read` файла принципов. Не было —
warning: принципы не прочитаны, а документ вводит новую сущность.

ЧЕГО НЕ ДЕЛАЕТ. Не судит, применены ли принципы — это суждение, кодом не
проверяется. Ловит только факт непрочтения. Не блокирует: правило может быть
уже прочитано в предыдущей сессии, а документ — продолжением начатого.

Класс: будильник (регламент по транспорту, вердикт за человеком).
Точка активации: `Write` .md в concepts/ | decisions/ | specs/.
Каденция: каждое создание документа-сущности.
Сигнал деградации: жалобы на ложные срабатывания -> сузить ENTITY_DIRS.

Правило-носитель: CLAUDE.md §7.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

PRINCIPLES = "operating-principles.md"

# Каталоги, где живут документы-сущности. Узко намеренно: правило говорит
# «новая сущность», а не «любой файл» — расширение даст шум.
# Настройка получателем: имена папок у него свои. Без этого проверка
# завязана на НАШУ раскладку, и у человека, назвавшего папку «решения»
# вместо `decisions`, она молчит навсегда — молчание неотличимо от
# «всё чисто», и он считает себя защищённым. Самодиагностика этого не
# видит: она проверяет, что хук запускается, а не что область совпала.
#
#   IVAN_ENTITY_DIRS — имена папок через запятую; заданы — заменяют
#                      набор по умолчанию целиком, а не дополняют его
#                      (человек знает свою раскладку лучше нас).
# Образец взят у post_write_md_check (IVAN_MD_* ), там же и мотивация:
# не навязывать своё представление о том, где живут документы.
def _entity_dirs():
    raw = os.environ.get("IVAN_ENTITY_DIRS", "")
    names = [re.escape(x.strip("/ ")) for x in raw.split(",") if x.strip(" /")]
    if not names:
        names = ["concepts?", "decisions", "specs"]
    return re.compile(r"/(" + "|".join(names) + r")/")


ENTITY_DIRS = _entity_dirs()

# Черновик в песочнице — тоже сущность: пять срабатываний замера пришлись
# ровно на черновиках технических документов.
SKIP_PATH = re.compile(r"/(_archive|archive|node_modules|\.git|build)/")

# Короткий файл — заготовка или индекс, не введение сущности.
MIN_CHARS = 1500


def read_transcript(payload: dict) -> str:
    """Текст транскрипта сессии. Пусто — если недоступен (fail-open)."""
    path = payload.get("transcript_path") or ""
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def principles_were_read(transcript: str) -> bool:
    """Был ли файл принципов ОТКРЫТ в этой сессии.

    Засчитывается имя в АРГУМЕНТАХ вызова инструмента — обращение к файлу.
    Не засчитывается имя в тексте ответа: «надо бы свериться с
    operating-principles.md» — это упоминание, а не чтение, и прежняя
    редакция на нём молчала (поймано прогоном 2026-09-02).

    Инструмент НЕ сужается до `Read`. Замер по всем транскриптам: файл
    принципов открывали через `Bash` 139 раз (`cat`, `sed`, `grep`) против
    5 через `Read`. Проверка «только Read» превратила бы хук в источник
    ложных предупреждений — то есть в шум, который перестают читать.

    Разбор строкой, не JSON: транскрипт бывает битым на последней строке
    (сессия ещё идёт), и падать здесь нельзя — хук молча пропустит запись
    вместо того, чтобы предупредить.
    """
    import json as _json
    for line in transcript.splitlines():
        if PRINCIPLES not in line:
            continue
        try:
            rec = _json.loads(line)
        except (ValueError, TypeError):
            continue
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            # Аргументы вызова — обращение к файлу.
            if kind == "tool_use":
                args = _json.dumps(block.get("input") or {}, ensure_ascii=False)
                if PRINCIPLES in args:
                    return True
            # Результат вызова — файл уже открыт, имя пришло в выводе
            # (`cat`, листинг каталога, вывод grep). Замер 2026-09-02: без
            # этой ветки 48 сессий из 108 получили бы ложное предупреждение —
            # файл читали, а хук считал бы, что нет.
            elif kind == "tool_result":
                out = block.get("content")
                if PRINCIPLES in _json.dumps(out, ensure_ascii=False):
                    return True
    return False


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    tool = payload.get("tool_name") or ""
    if tool != "Write":
        sys.exit(0)

    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path") or "")
    content = str(tool_input.get("content") or "")
    if not path or not path.endswith(".md"):
        sys.exit(0)

    posix = Path(path).as_posix()
    if SKIP_PATH.search(posix) or not ENTITY_DIRS.search(posix):
        sys.exit(0)
    if len(content) < MIN_CHARS:
        sys.exit(0)

    transcript = read_transcript(payload)
    if not transcript:
        sys.exit(0)  # fail-open: нечем проверить — молчим
    if principles_were_read(transcript):
        sys.exit(0)

    print(
        f"⚠️  Принципы не прочитаны: {Path(path).name} вводит новую сущность,\n"
        f"    а `{PRINCIPLES}` в этой сессии не открывался.\n"
        f"    §7: перед созданием новой сущности (модель, процесс, роль, навык,\n"
        f"    документ) — прочитать принципы и применять их.\n"
        f"    Это предупреждение, не блок: если принципы прочитаны в предыдущей\n"
        f"    сессии или документ продолжает начатое — warning игнорируется.",
        file=sys.stderr,
    )
    sys.exit(0)


if __name__ == "__main__":
    import _host_adapter
    sys.exit(_host_adapter.run_main(main))
