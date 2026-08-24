#!/usr/bin/env python3
"""
PostToolUse hook: после Write/Edit документа decisions / scope / concepts / specs,
если status в YAML frontmatter установлен в `accepted` или `ready` — проверяет
обязательные элементы по document-writing-standards.md (правило «стандарт оформления документов»):

  П1 — наличие H2 «Словарь»
  П6 — наличие H2 «Для кого этот документ» (для документов >150 строк)
  П8 — наличие H2 «История и контекст» (для decisions / scope)
  Я2 — отсутствие апострофных гибридов: regex `[A-Za-z]+'[а-яё]`
  Я6 — расшифровка аббревиатур из стоп-листа при первом упоминании

При нарушении возвращает exit 2 + список нарушений в stderr (Claude увидит
блокирующее сообщение и должен прогнать стандарт).

Lazy-режим для старых документов: если в frontmatter есть `created:` раньше
2026-05-15 — hook только предупреждает (exit 0 с stderr-сообщением), не блокирует.

Триггер по пути:
  - содержит /decisions/, /scope/, /concepts/, /specs/
  - расширение .md
  - НЕ в .claude/, не в шаблонах templates/, не в _archive/

Триггер по содержимому:
  - YAML frontmatter с полем `status:` равным accepted | ready
"""

import os
import re
import sys
import json
from datetime import date

STANDARD_LINK = (
    "coman-os/scl/intelligence/knowledge-hub/instructions/workflows/"
    "document-writing-standards.md"
)
TEMPLATES_LINK = (
    "coman-os/scl/intelligence/knowledge-hub/instructions/templates/"
)
STANDARD_INTRODUCED = date(2026, 5, 15)

# Стоп-лист аббревиатур: при первом появлении в основном тексте должна быть
# расшифровка в скобках ИЛИ присутствие в секции «Словарь».
# Только проектные аббревиатуры, не общеупотребительные (API, JSON, URL — исключение).
ABBREV_STOPLIST = [
    "ISC", "SCL", "CDT", "MVP", "SLA", "MCP", "KR", "NDA", "PII",
    "UFO", "DEMO", "KPIOWL", "OntoUML", "KMS", "ICP", "MBTI",
    "GIGO", "SKU", "SSOT",
]


def get_payload():
    try:
        return json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return {}


def get_tool_input(payload):
    return payload.get("tool_input", {}) or {}


def is_target_path(file_path: str) -> bool:
    if not file_path or not file_path.endswith(".md"):
        return False
    norm = file_path.replace("\\", "/")
    if "/.claude/" in norm:
        return False
    if "/templates/" in norm:
        return False
    if "/_archive/" in norm:
        return False
    if "/_templates/" in norm:
        return False
    # workflow / reference документы — не decisions / scope / concept / spec
    if "/instructions/workflows/" in norm or "/instructions/reference/" in norm:
        return False
    return any(
        seg in norm
        for seg in ["/decisions/", "/scope/", "/concepts/", "/specs/"]
    )


def parse_frontmatter(text: str) -> dict | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    block = text[4:end]
    fm = {}
    for line in block.split("\n"):
        m = re.match(r"^([a-zA-Z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip()
    return fm


def has_h2(text: str, *titles: str) -> bool:
    # H2 = строка вида "## Заголовок"
    for line in text.split("\n"):
        s = line.strip()
        if not s.startswith("## "):
            continue
        title = s[3:].strip().lower()
        for t in titles:
            if title.startswith(t.lower()):
                return True
    return False


def find_apostrophe_hybrids(text: str, allow_ontology_types: bool = False) -> list[str]:
    # Латиница + апостроф + кириллическое окончание.
    # Покрывает: producer'ом, tenant'а, pipeline'а, Tracker'е, LLM'ом, snapshot'а.
    pattern = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*['’][а-яёА-ЯЁ]+\b")
    matches = pattern.findall(text)
    if not allow_ontology_types:
        return matches
    # Исключение Я2 для онтологических документов (правило «стандарт оформления документов» §Я2):
    # формальные имена типов с CamelCase (Decision, Rule, Concept, IndicatorRelation,
    # StrategyGoal и др.) — proper nouns, склонение через апостроф разрешено.
    # Общие термины (producer, tenant, pipeline) остаются запрещены.
    camelcase_or_capword = re.compile(r"^[A-Z][a-zA-Z0-9_-]*['’]")
    return [m for m in matches if not camelcase_or_capword.match(m)]


def is_ontology_doc(file_path: str, doc_type: str) -> bool:
    # Документы с type: decision ИЛИ путём в kernel/ontology/, kernel/decisions/,
    # drafts/ontology/ — попадают под исключение Я2 для онтологических типов.
    if doc_type == "decision":
        return True
    p = file_path.replace("\\", "/")
    return (
        "/drafts/ontology/" in p
    )


def body_after_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def find_unexplained_abbrevs(text: str) -> list[str]:
    body = body_after_frontmatter(text)
    # Если в документе есть секция «Словарь» — считаем, что аббревиатуры в нём
    # будут проверены автором, и hook их не валидирует. Мы только проверяем
    # отсутствие словаря отдельно (П1).
    if has_h2(body, "Словарь"):
        return []
    found = []
    for abbr in ABBREV_STOPLIST:
        # Ищем первое появление как whole word.
        m = re.search(rf"\b{re.escape(abbr)}\b", body)
        if not m:
            continue
        # Проверим: в течение 80 символов справа есть скобка с расшифровкой?
        tail = body[m.end():m.end() + 80]
        if re.match(r"\s*\(", tail):
            continue  # есть скобка — считаем расшифровкой
        found.append(abbr)
    return found


def parse_created_date(fm: dict) -> date | None:
    # Канон — поле `created:`. Fallback на `date:` для старых ADR (2026-05-06..14),
    # которые использовали `date:` до стандартизации.
    raw = fm.get("created", "").strip() or fm.get("date", "").strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def get_doc_type(file_path: str) -> str:
    norm = file_path.replace("\\", "/")
    for t in ["decisions", "scope", "concepts", "specs"]:
        if f"/{t}/" in norm:
            return t[:-1] if t.endswith("s") else t
    return ""


def main() -> int:
    payload = get_payload()
    tool_input = get_tool_input(payload)
    file_path = tool_input.get("file_path", "")

    if not is_target_path(file_path):
        return 0

    if not os.path.exists(file_path):
        return 0

    try:
        with open(file_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return 0

    fm = parse_frontmatter(text)
    if not fm:
        # Документ без frontmatter в decisions/scope/concepts/specs.
        # Не наша задача — другие hooks (post_write_md_check) скажут о метаданных.
        return 0

    status = fm.get("status", "").strip().lower().strip("\"'")
    if status not in ("accepted", "ready"):
        return 0

    lazy = False
    created = parse_created_date(fm)
    if created and created < STANDARD_INTRODUCED:
        lazy = True

    body = body_after_frontmatter(text)
    line_count = text.count("\n") + 1
    doc_type = get_doc_type(file_path)

    violations = []

    # П1 — Словарь
    if not has_h2(body, "Словарь", "Glossary"):
        violations.append(
            "П1: секция «## Словарь» отсутствует.\n"
            "      Fix: добавить в начало body (после frontmatter) блок:\n"
            "        ## Словарь\n"
            "        - **Термин 1** — определение.\n"
            "        - **Термин 2** — определение."
        )

    # П6 — Для кого этот документ (для >150 строк)
    if line_count > 150 and not has_h2(
        body, "Для кого этот документ", "Для кого", "Аудитория"
    ):
        violations.append(
            "П6: секция «## Для кого этот документ» отсутствует "
            f"(документ {line_count} строк > 150).\n"
            "      Fix: добавить блок:\n"
            "        ## Для кого этот документ\n"
            "        - <роль 1> — <что забирает из документа>\n"
            "        - <роль 2> — <что забирает>"
        )

    # П8 — История и контекст (для decisions / scope)
    if doc_type in ("decision", "scope") and not has_h2(
        body, "История и контекст", "Контекст"
    ):
        violations.append(
            "П8: секция «## История и контекст» отсутствует "
            f"(обязательна для {doc_type}).\n"
            "      Fix: добавить блок с описанием — что было ДО решения, "
            "какие альтернативы рассматривались, почему выбрано именно это."
        )

    # Я2 — апострофные гибриды (с исключением для CamelCase в онтологических документах)
    allow_camelcase = is_ontology_doc(file_path, doc_type)
    hybrids = find_apostrophe_hybrids(body, allow_ontology_types=allow_camelcase)
    if hybrids:
        sample = list(dict.fromkeys(hybrids))[:5]
        violations.append(
            f"Я2: найдено {len(hybrids)} апострофных гибридов "
            f"(примеры: {', '.join(sample)}).\n"
            "      Fix: либо не склонять («pipeline»), либо "
            "транслитерировать («пайплайна»). Запрещено: «pipeline'а»."
            + (
                "\n      (Онтологические типы CamelCase — Decision'а, Rule'ы — "
                "разрешены в этом документе по §Я2 исключению.)"
                if allow_camelcase
                else ""
            )
        )

    # Я6 — нерасшифрованные аббревиатуры
    bad_abbrevs = find_unexplained_abbrevs(body)
    if bad_abbrevs:
        violations.append(
            f"Я6: аббревиатуры из стоп-листа без расшифровки и без секции "
            f"«Словарь»: {', '.join(bad_abbrevs)}.\n"
            "      Fix: при первом упоминании дать расшифровку в скобках — "
            "«SCL (Skill Consistency Layer)», ИЛИ добавить термин в "
            "секцию «## Словарь»."
        )

    if not violations:
        return 0

    header = (
        f"[doc_standard] Документ {os.path.basename(file_path)} получил "
        f"status: {status} без Прохода 2 по стандарту."
    )
    if lazy:
        header = (
            f"[doc_standard] Документ {os.path.basename(file_path)} "
            f"(created: {created}, до 2026-05-15) — lazy-режим, "
            f"предупреждение без блокировки."
        )

    bullets = "\n".join(f"  - {v}" for v in violations)
    footer = (
        f"\nПрогнать стандарт: {STANDARD_LINK}\n"
        f"Для новых документов копировать шаблон из: {TEMPLATES_LINK}"
    )
    msg = f"{header}\nНарушения:\n{bullets}{footer}"

    print(msg, file=sys.stderr)
    return 0 if lazy else 2


if __name__ == "__main__":
    sys.exit(main())
