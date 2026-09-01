#!/usr/bin/env python3
"""
PostToolUse hook: после записи .md файла проверяет:
1. Наличие таблицы метаданных (| Параметр | Значение |)
2. Наличие поля «Назначение» — карточка файла для агентов (см. requirements.md §2.0)

Метаданные отсутствуют → block (как было).
Метаданные есть, «Назначение» отсутствует → block: без карточки файла
документ находится только полным перебором.

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

MEETING_DIGEST_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[-.]")
MEETINGS_DIR_RE = re.compile(r"(?:^|[-_])meetings?(?:[-_]|$)", re.IGNORECASE)

# Чанк корпуса RAG: машинная нарезка методологии на фрагменты под поисковый
# индекс. Шапка чанка — хлебные крошки для сборки контекста при выдаче
# (`> Методология:` / `> Раздел:` / `> Связанные документы:`), а не карточка
# для человека.
RAG_CHUNK_RE = re.compile(r"^>\s*Раздел:\s*\S", re.MULTILINE)

# Второй признак корпуса RAG: каталог, помеченный `RAG` в имени. Нарезка
# бывает разной формы — у одного корпуса шапка чанка блокквотом, у другого
# нумерованные фрагменты глоссария без общей шапки вовсе, — и текстовый признак
# ловит только первую. Помечен именно каталог, потому что нарезка есть свойство
# КОРПУСА: файл вне такого каталога чанком не является, даже если похож.
# README самого корпуса под освобождение не попадает — он адресован человеку и
# карточку несёт.
RAG_DIR_RE = re.compile(r"(?:^|[/_-])RAG(?:[/_-]|$)", re.IGNORECASE)

# Носитель карточки в YAML-документе. Стандарт (§Формат шапки) запрещает
# двойную шапку: документ несёт ЛИБО YAML frontmatter, ЛИБО таблицу. Значит у
# YAML-документа требовать «Назначение» в таблице нельзя — выполнить оба
# требования разом невозможно. Роль карточки там играет `description:`.
YAML_DESCRIPTION_RE = re.compile(r"^description:\s*\S", re.MULTILINE)

NAZNACHENIE_PATTERN = re.compile(r"^\|\s*\*?\*?Назначение\*?\*?\s*\|", re.MULTILINE)

# П13: поле «Обновлён» держит ОДНУ запись — дата + строка сути. Журнал правок
# живёт в разделе «## История изменений» в теле документа.
#
# Механизм, который ловится: правка требует где-то отметиться, единственное поле
# про изменения одно, записи прирастают к нему через «Ранее …». Каждая по
# отдельности порога заметности не превышает — через десяток правок шапка
# перестаёт быть карточкой. Замер 2026-08-31: 33 документа с полем длиннее 400
# символов, худший — 27 327 (54 записи о еженедельной регенерации ростера).
#
# ПОРОГ 400 выбран по данным, а не на глаз: осмысленная одиночная запись
# укладывается в 200–300 символов (после прохода пять вычищенных дали
# 182–383), а всё, что длиннее, в замере оказывалось склейкой из ≥2 записей.
# Считается ЗНАЧЕНИЕ поля, не строка целиком.
#
# WARN, не block: 400 символов бывают одной осмысленной записью (offer-from-role-
# deficit нёс 3061 — семь версий подряд, но каждая по делу). Блокировка на
# пороге длины давала бы ложные срабатывания на честном тексте; отличить
# «журнал» от «длинной записи» код может лишь эвристикой ниже, и она не
# настолько надёжна, чтобы останавливать работу.
OBNOVLEN_PATTERN = re.compile(
    r"^\|\s*\*?\*?(?:Обновлён|Обновлено|Обновлен)\*?\*?\s*\|(.*?)\|\s*$",
    re.MULTILINE)
OBNOVLEN_MAX = 400
# Признак склейки: вторая дата в значении поля либо слово-соединитель «Ранее».
JOURNAL_SIGN = re.compile(r"Ранее\s+20\d\d-|20\d\d-\d\d-\d\d.*20\d\d-\d\d-\d\d", re.S)


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


def is_meeting_digest(file_path: str) -> bool:
    """Выжимка встречи — жанр, у которого сводка встроена в структуру.

    Поле «Назначение» — карточка для того, кто решает, читать ли файл. У выжимки
    эту работу делает её собственное начало: жанр задаёт постоянную структуру,
    где первая секция — резюме встречи («0. РЕЗЮМЕ», «TL;DR», «Статус»,
    «Прогресс по KR»). Прочитавший первый экран уже знает, о чём документ и
    что из него забрать. Карточка сверху повторяла бы это, ничего не добавив.

    Требование НЕ новое — хук отставал от текста правила. Стандарт оформления
    (§Скоуп применения) с самого начала говорит «не применяется к операционным
    трекерам, выжимкам встреч, memory-файлам», П12 повторяет то же для блока
    «Зачем документ». В коде же освобождались только выжимки slice-v3
    (`*-views/`, frontmatter `meeting_type`) — по техническому признаку YAML,
    а не по жанру. Выжимки прежних форматов того же жанра под освобождение не
    попадали: 140 файлов очереди backfill на 2026-08-31.

    Признак: файл в каталоге `meetings/` (любой глубины) с датой в начале имени
    (`YYYY-MM-DD-*.md`). Дата отделяет саму выжимку от соседей по каталогу —
    `history-log.md`, README, сводные документы, — которым карточка нужна: их
    как раз находят поиском, а не по дате встречи из трекера.
    """
    # Каталог выжимок называется по-разному: `meetings`, `meetings-w18`,
    # `04-meetings-digest`, `pre-team-meetings`. Точное сравнение с «meetings»
    # ловило только первый вариант: выжимка в каталоге с префиксом-номером
    # оставалась вне освобождения, хотя жанр тот же.
    if not any(MEETINGS_DIR_RE.search(part) for part in file_path.split(os.sep)[:-1]):
        return False
    return bool(MEETING_DIGEST_NAME_RE.match(os.path.basename(file_path)))


def is_rag_chunk(file_path: str, content: str) -> bool:
    """Фрагмент корпуса RAG — не документ, а единица поисковой выдачи.

    Поле «Назначение» — карточка для того, кто решает, читать ли файл. У чанка
    такого читателя нет: его не выбирают и не открывают, его достаёт поисковый
    индекс и отдаёт кусками в контекст модели. Роль карточки здесь выполняет
    собственная шапка чанка — хлебные крошки «какая методология, какой раздел,
    с чем связан», по которым собирается контекст выдачи.

    Тот же ход, что с выжимками встреч: жанр, где адресат и способ нахождения
    заданы устройством, а не карточкой. Разница в том, что освобождение выжимок
    уже было записано в стандарте, а это — новое (решение владельца
    2026-08-31).

    Признак — строка `> Раздел:` в шапке. Проверено на корпусе: 60 из 60
    чанков её несут, и ни один документ вне нарезки её не имеет.
    Соседняя строка `> Методология:` для признака не годится — она встречается
    и в выжимках встреч клиентов.

    Признаков два, потому что нарезки бывают разной формы:
    (1) строка `> Раздел:` в шапке чанка — форма с общей шапкой;
    (2) файл внутри каталога, помеченного `RAG` в имени, — форма, где чанки
        суть нумерованные фрагменты глоссария без общей шапки.
    Второй признак смотрит на каталог не как на «место», а как на границу
    корпуса: нарезка есть свойство корпуса целиком, и файл вне такого каталога
    чанком не является, даже если похож по виду.

    README корпуса под освобождение не попадает: он адресован человеку,
    который решает, что это за корпус и зачем, — ему карточка нужна.
    """
    if os.path.basename(file_path) in ("README.md", "index.md"):
        return False
    if RAG_DIR_RE.search(os.path.dirname(file_path)):
        return True
    return bool(RAG_CHUNK_RE.search(content))


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
            # 25 строк хватает таблице метаданных, но YAML-frontmatter бывает
            # длиннее (changelog внутри). Обрезанный на середине frontmatter не
            # закрывается вторым `---`, YAML_FRONTMATTER_RE не матчится, и
            # документ, освобождённый по `type:`/`meeting_type:`/`name:`,
            # ошибочно получает требование таблицы. Прецедент 2026-08-31:
            # engine-operations-architecture.md (`type: concept`, frontmatter
            # 43 строки) — самый цитируемый файл очереди backfill, требование
            # получал по обрезке, а не по существу.
            head = ""
            for i, line in enumerate(f):
                if i >= 60:
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

    # Выжимка встречи — жанр со встроенной сводкой, вне скоупа стандарта
    # (document-writing-standards §Скоуп применения; П12).
    if is_meeting_digest(file_path):
        return

    # Чанк корпуса RAG — единица поисковой выдачи, не документ.
    if is_rag_chunk(file_path, head):
        return

    # YAML-документ: карточка живёт в `description`, не в таблице.
    #
    # Стандарт (§Формат шапки) требует выбрать РОВНО ОДИН формат шапки и прямо
    # запрещает двойную. Прежняя редакция хука знала только табличный носитель
    # «Назначения», поэтому YAML-документ вне TEMPLATE_TYPES попадал в
    # неисполнимое требование: добавить таблицу — нарушить стандарт, не
    # добавить — получать предупреждение вечно. Прецедент 2026-08-31: девять
    # прогонов causal-engine (`type: test-run`, YAML с title/status/agent, без
    # description) — цитируемые документы, у которых карточку негде было
    # разместить законно.
    #
    # Решение: для документа с YAML-frontmatter носителем карточки признаётся
    # `description:`. Требование не снимается — переносится в тот носитель,
    # который стандарт для этого документа разрешает.
    #
    # ВАЖНО: ветка работает ТОЛЬКО когда таблицы нет. Frontmatter бывает
    # техническим (`audience: internal-engineering` и ничего больше) при живой
    # таблице метаданных ниже — карточка у такого документа есть, и требовать
    # вдобавок `description` значит требовать двойную шапку, ровно то, что
    # правило запрещает. Регрессия 2026-09-01: первая редакция этой ветки
    # перехватывала документ до проверки таблицы и дала 148 ложных
    # срабатываний на корпусе.
    # Таблица ищется в тексте ПОСЛЕ frontmatter и до первого раздела: таблица
    # в теле документа (у прогонов их несколько) шапкой не является и права
    # нести карточку не имеет.
    yaml_match = YAML_FRONTMATTER_RE.match(head)
    after = head[yaml_match.end():] if yaml_match else head
    header_zone = re.split(r"^##\s", after, maxsplit=1, flags=re.MULTILINE)[0]
    has_table = any(marker in header_zone for marker in METADATA_MARKERS)
    if yaml_match and not has_table:
        if YAML_DESCRIPTION_RE.search(yaml_match.group(1)):
            return
        print(json.dumps({
            "decision": "warn",
            "reason": (
                f"[post_write_md_check] {basename}: YAML-frontmatter без поля "
                f"`description`.\n"
                f"Fix: добавить в frontmatter строку\n"
                f"  description: <1–3 предложения, ≤100 слов: для кого документ, "
                f"что забирают, когда читать>\n"
                f"Таблицу метаданных НЕ добавлять — стандарт запрещает двойную "
                f"шапку (document-writing-standards §Формат шапки). У документа "
                f"с YAML-frontmatter карточку несёт `description`."
            ),
        }), file=sys.stderr)
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
        print(json.dumps({
            "decision": "block",
            "reason": (
                f"[post_write_md_check] {basename}: нет поля «Назначение» в таблице метаданных.\n"
                f"Fix: добавить строку в таблицу метаданных:\n"
                f"  | Назначение | <1–3 предложения, ≤100 слов: для кого документ, "
                f"что забирают, когда читать> |\n"
                f"Это карточка файла для агентов (kh-check Проверка A). "
                f"SSOT: requirements.md §2.0."
            ),
        }), file=sys.stderr)
        return

    check_obnovlen(basename, head)


def check_obnovlen(basename: str, head: str) -> None:
    """П13: поле «Обновлён» — одна запись, журнал в теле документа."""
    m = OBNOVLEN_PATTERN.search(head)
    if not m:
        return
    value = m.group(1).strip()
    if len(value) <= OBNOVLEN_MAX:
        return
    # Длинная одиночная запись — законна (см. комментарий у OBNOVLEN_MAX).
    # Ругаемся только когда видно склейку: вторая дата или «Ранее».
    if not JOURNAL_SIGN.search(value):
        return
    print(json.dumps({
        "decision": "warn",
        "reason": (
            f"[post_write_md_check] {basename}: поле «Обновлён» несёт журнал "
            f"({len(value)} символов, порог {OBNOVLEN_MAX}).\n"
            f"Fix (П13): оставить в поле одну текущую запись — дата + строка сути "
            f"(≤200 символов), прежние перенести в раздел в конце документа:\n"
            f"  ## История изменений\n"
            f"  | Дата | Что изменилось |\n"
            f"  |------|----------------|\n"
            f"  | <YYYY-MM-DD> | <что изменилось> |\n"
            f"Правило записи — REPLACE, не append: новая правка заменяет поле, "
            f"прежнее уходит первой строкой журнала. Цепочка «Ранее … Ранее …» "
            f"внутри ячейки запрещена.\n"
            f"SSOT: document-writing-standards.md §П13."
        ),
    }))
    return


if __name__ == "__main__":
    import _host_adapter
    sys.exit(_host_adapter.run_main(main))
