#!/usr/bin/env python3
"""
PostToolUse hook: после создания нового concept-документа в чувствительных зонах
(leadership / principles / concepts / kernel/ontology / sandbox/ontology /
explorations/ontology) — делает grep по ключевым словам из H1 и первого H2,
выводит топ-3 файлов с overlap в качестве предупреждения.

Цель: предотвратить класс ошибок «новый concept молча подменяет более сильный
существующий SSOT» (прецеденты 2026-05-06 UFO Hypothesis + 2026-05-20 CoMan OS
synthesis, см. правило «pre-write сверка с SSOT»).

Hook НЕ блокирует Write (exit 0). Только warning в stderr — Claude видит и
решает: ссылаться на найденный SSOT, переформулировать, или продолжить как
дополнение.

Триггеры (срабатывает только при ВСЕХ условиях):
  1. Tool = Write (не Edit — Edit меняет существующее, overlap не релевантен)
  2. Путь содержит /concepts/ ИЛИ /leadership/ ИЛИ /principles/ ИЛИ
     /kernel/ontology/ ИЛИ /sandbox/ontology/ ИЛИ /explorations/ontology/
  3. Файл новый (Write создаёт новый файл, не overwrite — определяется попыткой
     найти его в git ls-files; если найден — был раньше → skip)
  4. Расширение .md
  5. Не в .claude/, не в /_archive/, не в /templates/

Алгоритм:
  - Извлечь H1 (первая строка `# Title`) и первый H2 (`## ...`)
  - Получить keywords = унигалы из H1 + H2, фильтр: длина ≥4, не из stoplist
    (kebab-cased / camelCased считаем как одно слово)
  - grep -r по содержанию по 6 sensitive folders (rg если есть, иначе grep)
  - Топ-3 файла с наибольшим совпадением (исключая сам новый файл)
  - Если найдены — stderr с указанием файлов и команды для full-read

Exit code:
  0 always — это soft warning, не блокировка
"""

import os
import re
import sys
import json
import subprocess
from pathlib import Path


SENSITIVE_FOLDERS = [
    "coman-os/knowledge-core/leadership",
    "coman-os/knowledge-core/principles",
    "coman-os/scl/intelligence/principles",
    "drafts/ontology",
]

# Папки concept-документов, в которых тоже стоит проверить overlap.
# Concept в любой папке coman-os/**/concepts/ или {client}-knowledge-hub/.../concepts/
CONCEPT_PATH_MARKERS = ["/concepts/"]

STOPWORDS = {
    # Союзы / предлоги / частицы
    "и", "или", "не", "что", "как", "для", "это", "при", "от", "до", "в", "на",
    "под", "над", "из", "за", "по", "с", "у", "о", "об", "к", "через", "между",
    "the", "and", "or", "of", "to", "in", "for", "with", "without", "is", "are",
    "was", "were", "be", "been", "being", "this", "that", "these", "those",
    "a", "an", "via", "between", "such", "but", "if", "then",
    # Generic документ-слова
    "draft", "experimental", "accepted", "stable", "production", "deprecated",
    "concept", "decision", "spec", "scope", "memo", "доклад", "версия", "статус",
    "обзор", "обновление", "новый", "новая", "новое", "часть", "раздел",
    "тезис", "тезисы", "контекст", "результат", "введение", "выводы", "итог",
    "цель", "задача", "проблема", "решение",
}

MAX_OVERLAP_RESULTS = 3
MIN_KEYWORD_LENGTH = 4


def get_payload():
    try:
        return json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError):
        return {}


def is_target_path(file_path: str) -> bool:
    if not file_path or not file_path.endswith(".md"):
        return False
    norm = file_path.replace("\\", "/")
    if "/.claude/" in norm or "/_archive/" in norm or "/templates/" in norm:
        return False
    if "/_templates/" in norm:
        return False
    for folder in SENSITIVE_FOLDERS:
        if folder in norm:
            return True
    for marker in CONCEPT_PATH_MARKERS:
        if marker in norm:
            return True
    return False


def is_new_file(repo_root: Path, file_path: str) -> bool:
    """Returns True if file is brand-new (not in git tracked files yet)."""
    rel = os.path.relpath(file_path, repo_root)
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode != 0
    except (subprocess.SubprocessError, OSError):
        return False


def extract_keywords(text: str) -> list[str]:
    """Извлекает H1 и первый H2, токенизирует, фильтрует стоп-слова."""
    lines = text.split("\n")
    h1 = ""
    h2 = ""
    for line in lines:
        s = line.strip()
        if s.startswith("# ") and not h1:
            h1 = s[2:].strip()
        elif s.startswith("## ") and not h2:
            h2 = s[3:].strip()
            if h1:
                break
    combined = (h1 + " " + h2).lower()
    # Tokenize: keep kebab-case / underscored as single tokens
    tokens = re.findall(r"[a-zа-яё][a-zа-яё0-9_-]+", combined)
    keywords = []
    seen = set()
    for tok in tokens:
        if len(tok) < MIN_KEYWORD_LENGTH:
            continue
        if tok in STOPWORDS:
            continue
        if tok in seen:
            continue
        seen.add(tok)
        keywords.append(tok)
    return keywords[:8]  # top-8 keywords максимум, чтобы grep не пухнул


def grep_overlap(repo_root: Path, keywords: list[str], exclude_file: str) -> list[tuple[str, int]]:
    """Считает overlap-score для каждого файла в SENSITIVE_FOLDERS."""
    if not keywords:
        return []
    pattern = "|".join(re.escape(kw) for kw in keywords)
    folders = [str(repo_root / f) for f in SENSITIVE_FOLDERS if (repo_root / f).exists()]
    if not folders:
        return []
    try:
        result = subprocess.run(
            ["grep", "-r", "-l", "-i", "-E", pattern, "--include=*.md"] + folders,
            capture_output=True,
            text=True,
            timeout=10,
        )
        matched_files = [
            f.strip() for f in result.stdout.strip().split("\n")
            if f.strip() and os.path.abspath(f.strip()) != os.path.abspath(exclude_file)
        ]
    except (subprocess.SubprocessError, OSError):
        return []
    # Score = количество matched keywords в файле
    scored = []
    for fpath in matched_files:
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read().lower()
        except OSError:
            continue
        score = sum(1 for kw in keywords if kw in content)
        if score >= 2:  # минимум 2 keyword overlap чтобы избежать шума
            scored.append((fpath, score))
    scored.sort(key=lambda x: (-x[1], x[0]))
    return scored[:MAX_OVERLAP_RESULTS]


def format_warning(file_path: str, keywords: list[str], overlaps: list[tuple[str, int]]) -> str:
    repo_root_str = str(Path(__file__).resolve().parent.parent.parent)
    rel_overlaps = []
    for fpath, score in overlaps:
        rel = os.path.relpath(fpath, repo_root_str)
        rel_overlaps.append(f"  - {rel} (overlap: {score}/{len(keywords)})")
    rel_new = os.path.relpath(file_path, repo_root_str)
    return (
        f"[ssot_overlap_warn] Новый concept-документ в чувствительной зоне:\n"
        f"  {rel_new}\n\n"
        f"Keywords из H1/H2: {', '.join(keywords)}\n\n"
        f"Найдены потенциально пересекающиеся SSOT-документы (топ-{len(overlaps)}):\n"
        + "\n".join(rel_overlaps)
        + "\n\n"
        f"Действия (см. правило «pre-write сверка с SSOT»):\n"
        f"  1. Прочитать найденные документы целиком (≥500 строк → читать обязательно)\n"
        f"  2. Сверить тезис: повторяет / противоречит / дополняет?\n"
        f"  3. Если повторяет слабее — откатить, ссылаться на SSOT\n"
        f"  4. Если дополняет — переформулировать как «дополнение к [SSOT]»\n"
        f"  5. Если уже сверено до Write — игнорировать это предупреждение"
    )


def main():
    payload = get_payload()
    tool_name = payload.get("tool_name") or payload.get("name") or ""
    if tool_name != "Write":
        return 0
    tool_input = payload.get("tool_input", {}) or {}
    file_path = tool_input.get("file_path") or ""
    content = tool_input.get("content") or ""
    if not is_target_path(file_path):
        return 0
    repo_root_env = os.environ.get("CLAUDE_PROJECT_DIR")
    repo_root = Path(repo_root_env) if repo_root_env else Path(__file__).resolve().parent.parent.parent
    if not is_new_file(repo_root, file_path):
        return 0
    keywords = extract_keywords(content)
    if len(keywords) < 2:
        return 0
    overlaps = grep_overlap(repo_root, keywords, file_path)
    if not overlaps:
        return 0
    print(format_warning(file_path, keywords, overlaps), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
