#!/usr/bin/env python3
"""
PostToolUse hook: после Write/Edit документа (внешняя ИЛИ внутренняя аудитория)
проверяет наличие англицизмов, имеющих точный русский эквивалент.

Правило 20 CLAUDE.md: "Языковая дисциплина для внешней и внутренней аудитории".
Реализация правила «языковая дисциплина» (языковая дисциплина) — hard enforcement.
Расширено 2026-06-22: не только клиентские/внешние доки, но и внутренние
прозовые (продукт CoMan OS, методология) — тяжело читать прозу с англицизмами.

Безопасность расширения: проверка идёт по КУРИРУЕМОМУ стоп-листу (~70 терминов,
у каждого русский эквивалент) + whitelist проектных терминов (OKR, Engine-имена,
API/JSON/schema). Легитимный технический жаргон не цепляется.

При нарушении возвращает exit 2 + директивное сообщение с конкретными заменами.

Триггер по пути (внешние + внутренние прозовые):
  - {client}-knowledge-hub/*/teams/    (выжимки/отчёты команд клиента)
  - {client}-knowledge-hub/*/meetings/ (pulse-выжимки клиента)
  - drafts/*/specs/
  - coman-os/knowledge-core/           (методология)
  - coman-os/scl/                      (инструкции Knowledge Hub)

НЕ срабатывает:
  - memory-файлы, Python код, JSON Schema
  - pipeline-issues.md (производные рендеры)
  - audience: internal-engineering в frontmatter (опт-аут: инженерным
    докам жаргон нужен — schema/pipeline/spec читаемее как термины)
  - templates/, _archive/, _changes/
  - README.md (индексы)

Два уровня стоп-листа (введено 2026-06-22 по аудиту):
  - Tier-1 (прозовые англицизмы: reuse, commodity, misalignment, onboarding,
    binding, wedge, playbook...) — заменяются ВЕЗДЕ (внешние + внутренние).
  - Tier-2 (техническая/методологическая лексика: spec, pipeline, operations,
    outcome, moat, alignment, validation...) — допускается во ВНУТРЕННИХ доках,
    проверяется только во ВНЕШНИХ/клиентских.
Причина: внутренние архитектурные/методологические доки легитимно используют
техлексику (outcome = термин OKR, moat = имя файла ontology-as-network-moat).

Threshold: блокирует при >= 3 англицизмах из активного (для пути) набора.
"""

import os
import re
import sys
import json


WHITELIST_TERMS = {
    "CoMan OS", "CoMan Pulse", "CoMan Memory", "CoMan Team", "CoMan Spark",
    "AI-native", "AI-агенты", "AI-агент",
    "Coman", "Spark", "Pulse", "Memory", "Team", "Engine",
    "OKR", "KR", "KPI", "CEO", "COO", "CTO", "CPO", "MVP",
    "SSOT", "API", "JSON", "HTML", "PDF", "URL", "ICP",
    "YC", "RFS", "OKR-мастер", "OKR-мастера",
    "UFO", "OntoUML", "OntoClean", "DEMO", "KPIOWL", "SPEC",
    "Anthropic", "Claude", "Claude Code",
    "YAML", "Markdown", "Pydantic",
}


ANGLICISM_REPLACEMENTS = {
    "workflow": ["процесс", "последовательность работ"],
    "pipeline": ["конвейер", "последовательность"],
    "stakeholder": ["заинтересованный участник"],
    "stakeholders": ["заинтересованные участники"],
    "implementation": ["реализация", "внедрение"],
    "implement": ["реализовать", "внедрить"],
    "spec": ["спецификация"],
    "specs": ["спецификации"],
    "scope": ["объём", "охват"],
    "backlog": ["отложенный список", "очередь"],
    "operation": ["операция"],
    "operations": ["операции"],
    "operational": ["операционный"],
    "strategic": ["стратегический"],
    "reusable": ["переиспользуемый"],
    "sustainable": ["устойчивый"],
    "sustainability": ["устойчивость"],
    "investment": ["инвестиция", "вложение"],
    "outcome": ["результат"],
    "outcomes": ["результаты"],
    "outputs": ["выходы", "результаты"],
    "default": ["по умолчанию"],
    "empirical": ["эмпирический"],
    "systematic": ["систематический"],
    "alignment": ["согласованность"],
    "validation": ["валидация", "проверка"],
    "validate": ["проверить"],
    "deliverable": ["результат поставки"],
    "deliverables": ["результаты поставки"],
    "delivery": ["поставка"],
    "playbook": ["сценарий", "руководство"],
    "outlier": ["исключение"],
    "persistent": ["устойчивый", "постоянный"],
    "key person": ["ключевой человек"],
    "key-person": ["ключевой человек"],
    "insurance": ["страховая ценность"],
    "asset": ["актив"],
    "assets": ["активы"],
    "advantage": ["преимущество"],
    "moat": ["защитный ров"],
    "capability": ["возможность"],
    "capabilities": ["возможности"],
    "capacity": ["ёмкость"],
    "feedback": ["обратная связь"],
    "milestone": ["веха"],
    "milestones": ["вехи"],
    "trigger": ["триггер", "пусковой момент"],
    "triggers": ["триггеры"],
    "argumentation": ["аргументация"],
    "auditable": ["доступный для аудита"],
    "traceable": ["прослеживаемый"],
    "defensible": ["защитимый"],
    "actionable": ["применимый"],
    "scaling": ["масштабирование"],
    "ambition": ["амбиция"],
    "approach": ["подход"],
    "horizon": ["горизонт"],
    "challenge": ["вызов", "сложность"],
    "challenges": ["вызовы"],
    "ownership": ["авторство", "владение"],
    "reuse": ["переиспользование"],
    "commodity": ["ширпотреб", "товар массового рынка"],
    "misalignment": ["рассогласование"],
    "wedge": ["клин"],
    "roadmap": ["дорожная карта", "план"],
    "onboarding": ["адаптация", "введение в работу"],
    "tradeoff": ["компромисс", "размен"],
    "trade-off": ["компромисс", "размен"],
    "leverage": ["рычаг"],
    "binding": ["связывающий", "узкое место"],
    "supply-side": ["сторона предложения"],
    "consumption-side": ["сторона потребления"],
    "persistence": ["сохранение", "постоянство"],
    "compounding": ["накопление"],
}


# Tier-1 — прозовые англицизмы: чистый русский эквивалент, НЕ устоявшийся
# термин/жаргон. Заменяются ВЕЗДЕ (внешние + внутренние доки).
# Всё остальное в ANGLICISM_REPLACEMENTS — Tier-2 (техническая/методологическая
# лексика: spec, pipeline, operations, outcome, moat, alignment, validation...),
# допускается во внутренних доках, проверяется только во внешних/клиентских.
TIER1_PROSE = {
    "reuse", "commodity", "misalignment", "wedge", "supply-side", "consumption-side",
    "onboarding", "tradeoff", "trade-off", "persistence", "compounding", "playbook",
    "sustainable", "sustainability", "insurance", "outlier", "auditable", "traceable",
    "defensible", "actionable", "ambition", "approach", "horizon", "challenge",
    "challenges", "argumentation", "advantage", "leverage", "reusable", "persistent",
    "binding", "key person", "key-person",
}


# Внешние/клиентские — проверяются по ПОЛНОМУ стоп-листу (Tier-1 + Tier-2).
EXTERNAL_PATH_PATTERNS = [
    r"-knowledge-hub/.*/teams/",
    r"-knowledge-hub/.*/meetings/",
    r"drafts/.*/specs?/",
]

# Внутренние прозовые — проверяются ТОЛЬКО по Tier-1 (техлексика допускается).
INTERNAL_PATH_PATTERNS = [
    r"coman-os/knowledge-core/",
    r"coman-os/scl/",
]

TRIGGER_PATH_PATTERNS = EXTERNAL_PATH_PATTERNS + INTERNAL_PATH_PATTERNS


SKIP_PATH_PATTERNS = [
    r"/memory/",
    r"/templates/",
    r"/_archive/",
    r"/_changes/",
    r"history-log\.md",
    r"pipeline-issues\.md",
    r"transcripts/",
    r"raw/",
    r"README\.md$",
    r"\.py$",
    r"\.json$",
    r"\.yaml$",
    r"\.yml$",
]


ANGLICISM_THRESHOLD = 3


def get_payload():
    try:
        return json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return {}


def get_file_path(payload):
    tool_input = payload.get("tool_input") or {}
    return tool_input.get("file_path") or ""


def is_triggered_path(file_path):
    if not file_path:
        return False
    for skip_pattern in SKIP_PATH_PATTERNS:
        if re.search(skip_pattern, file_path):
            return False
    for trigger_pattern in TRIGGER_PATH_PATTERNS:
        if re.search(trigger_pattern, file_path):
            return True
    return False


def is_external_path(file_path):
    return any(re.search(p, file_path) for p in EXTERNAL_PATH_PATTERNS)


def has_internal_audience(content):
    fm_match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not fm_match:
        return False
    frontmatter = fm_match.group(1)
    if re.search(r"^audience:\s*internal[-_]engineering\s*$", frontmatter, re.MULTILINE):
        return True
    return False


def find_anglicisms(content, active_terms):
    found = []
    lines = content.split("\n")
    whitelist_lower = {t.lower() for t in WHITELIST_TERMS}

    in_frontmatter = False
    fm_count = 0
    for line_num, line in enumerate(lines, 1):
        # Tracking yaml frontmatter
        if line.strip() == "---":
            fm_count += 1
            in_frontmatter = (fm_count == 1)
            continue
        if in_frontmatter:
            continue

        # Пропускаем code blocks, заголовки, таблицы, отступы
        if line.strip().startswith(("```", "|", "#", "    ", "  - ", "- [", "- /", "- _", "> ")):
            continue
        # Пропускаем строки-ссылки на файлы (кончаются на .md, .py, .json и т.д.)
        if re.match(r"^\s*-\s+[\w/.-]+\.(md|py|json|yaml|yml|html|pdf)(\s+\(.*\))?\s*$", line):
            continue
        # Пропускаем frontmatter-style строки (вне frontmatter тоже бывают)
        if re.match(r"^\s*[a-z_]+:\s*", line):
            continue

        cleaned = re.sub(r"`[^`]*`", "", line)
        cleaned = re.sub(r"https?://\S+", "", cleaned)
        cleaned = re.sub(r"\[[^\]]*\]\([^)]*\)", "", cleaned)

        for ang, replacements in active_terms.items():
            pattern = r"\b" + re.escape(ang) + r"\b"
            for match in re.finditer(pattern, cleaned, re.IGNORECASE):
                matched_text = match.group()
                if matched_text.lower() in whitelist_lower:
                    continue
                start = match.start()
                end = match.end()
                preceding_words = cleaned[:start].split()
                following_words = cleaned[end:].split()
                preceding = preceding_words[-1] if preceding_words else ""
                following = following_words[0] if following_words else ""
                if (preceding.rstrip(",.()'\"`") in WHITELIST_TERMS or
                    following.lstrip(",.()'\"`") in WHITELIST_TERMS):
                    continue
                found.append((matched_text, line_num, replacements))
                break
    return found


def format_violations(found, file_path):
    lines = [
        "[post_write_anglicism_check] Найдены англицизмы в документе (внешняя/внутренняя аудитория).",
        f"Файл: {file_path}",
        "",
        "Правило 20 CLAUDE.md: при создании клиентских материалов / продуктовых /",
        "методологических / архитектурных документов — заменять англицизмы на русские эквиваленты.",
        "",
        "Нарушения (вне whitelist допустимых терминов проекта):",
    ]
    grouped = {}
    for ang, line_num, replacements in found:
        key = ang.lower()
        if key not in grouped:
            grouped[key] = {"count": 0, "lines": [], "replacements": replacements, "term": ang}
        grouped[key]["count"] += 1
        grouped[key]["lines"].append(line_num)

    for key, info in sorted(grouped.items(), key=lambda x: -x[1]["count"]):
        replacement_str = " / ".join(info["replacements"])
        line_str = ", ".join(str(l) for l in info["lines"][:5])
        if len(info["lines"]) > 5:
            line_str += f", ... (всего {info['count']})"
        lines.append(f'  - "{info["term"]}" (строки {line_str}) -> {replacement_str}')

    lines += [
        "",
        "Fix:",
        "  1. Заменить англицизмы на русские эквиваленты согласно списку выше",
        "  2. Если термин — имя проекта / стандартное сокращение / название организации —",
        "     добавить в WHITELIST_TERMS в .claude/hooks/post_write_anglicism_check.py",
        "  3. Если документ для внутренней инженерной аудитории —",
        "     добавить в frontmatter: audience: internal-engineering",
        "",
        "SSOT правила: языковая дисциплина (правило «языковая дисциплина»)",
        "Правило 20: CLAUDE.md (корень instance)",
    ]
    return "\n".join(lines)


def main():
    payload = get_payload()
    file_path = get_file_path(payload)

    if not file_path or not file_path.endswith(".md"):
        sys.exit(0)

    if not is_triggered_path(file_path):
        sys.exit(0)

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (IOError, OSError):
        sys.exit(0)

    if has_internal_audience(content):
        sys.exit(0)

    # Внешние/клиентские — полный стоп-лист; внутренние — только Tier-1 (прозовые).
    if is_external_path(file_path):
        active_terms = ANGLICISM_REPLACEMENTS
    else:
        active_terms = {k: v for k, v in ANGLICISM_REPLACEMENTS.items() if k in TIER1_PROSE}

    found = find_anglicisms(content, active_terms)

    if len(found) < ANGLICISM_THRESHOLD:
        sys.exit(0)

    message = format_violations(found, file_path)
    print(message, file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
