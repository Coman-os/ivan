#!/usr/bin/env python3
"""
PostToolUse hook: after writing/editing a .md file, checks if the parent
directory's README.md index is up-to-date. If not, reminds Claude to update it.

Does NOT auto-fix — just alerts. The actual fix happens either:
- By Claude in response to the alert (inline)
- By /kh-check Проверка E (deep check with metadata parsing)

Light and fast: only checks file list, not metadata content.
"""

import json
import os
import re
import subprocess
import sys


# Directories to skip entirely
SKIP_PATH_PARTS = {"node_modules", ".git", ".claude", "__pycache__", "_templates", ".sync-state", "CRM"}

# Folder names where the 10+ files size check is skipped.
# meetings/ folders are chronological archives; history-log.md already handles navigation.
SKIP_SIZE_CHECK_DIRS = {"meetings", "w6-w12-archive"}

# Regex-pattern для имён папок которые ВООБЩЕ не требуют README (включая «нет README + есть .md»).
# Это технические артефакт-папки pipeline'ов, не нуждающиеся в навигации.
# - {date}-views/ — slice-v3 audience views (LP renders), index не нужен (Phase 1: 1 active profile)
# - {date}-audit/ — slice-v3 audit JSONs + manifests, не human-navigation
# Суффикс `-N` покрывает несколько встреч одной команды в один день
# ({date}-2-views/ и т.п.) — тот же класс технических папок (SP-128).
SKIP_README_DIR_PATTERNS = [
    re.compile(r"^\d{4}-\d{2}-\d{2}(-\d+)?-views$"),
    re.compile(r"^\d{4}-\d{2}-\d{2}(-\d+)?-audit$"),
]

# Root-level dirs where README sync applies
KH_ROOTS = {"coman-os"}  # instance overlay extends with {client}-knowledge-hub

# строка «Назначение папки» в README подпапки. Допускается любое
# markdown-выделение вокруг (`**Назначение папки:**`, `*Назначение папки*`) и
# blockquote-префикс — сверяется наличие смысловой строки, не её оформление.
README_PURPOSE_RE = re.compile(r"Назначение\s+папки", re.IGNORECASE)


def get_project_dir():
    return os.environ.get("CLAUDE_PROJECT_DIR", "")


def is_in_kh(file_path, project_dir):
    """Check if file is inside a KH root directory."""
    rel = os.path.relpath(file_path, project_dir)
    first_part = rel.split(os.sep)[0]
    return first_part in KH_ROOTS


def get_md_files_in_dir(directory):
    """Get all .md files in directory (not recursive), excluding README.md."""
    md_files = []
    try:
        for entry in os.listdir(directory):
            full = os.path.join(directory, entry)
            if entry == "README.md":
                continue
            if entry.endswith(".md") and os.path.isfile(full):
                md_files.append(entry)
    except (FileNotFoundError, PermissionError):
        pass
    return sorted(md_files)


def read_text_safe(path):
    """Содержимое файла; при любой ошибке чтения — пустая строка (hook не падает
    на битой кодировке/правах, максимум даёт лишний warning)."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


def get_subdirs(directory):
    """Get immediate subdirectories."""
    subdirs = []
    try:
        for entry in os.listdir(directory):
            full = os.path.join(directory, entry)
            if os.path.isdir(full) and not entry.startswith("."):
                subdirs.append(entry + "/")
    except (FileNotFoundError, PermissionError):
        pass
    return sorted(subdirs)


def get_files_in_readme(readme_path):
    """Extract file/dir names from README index table."""
    files = set()
    try:
        with open(readme_path, "r", encoding="utf-8") as f:
            for line in f:
                # Match table rows like: | file.md | ... |
                # or | [file.md] | ... |
                # or | `file.md` | ... |
                # or | subfolder/ | ... |
                match = re.match(r"\|\s*`?\[?([^\]|`]+\.md)\]?`?", line)
                if match:
                    files.add(match.group(1).strip())
                    continue
                match = re.match(r"\|\s*`?\[?([^\]|`]+/)\]?`?", line)
                if match:
                    files.add(match.group(1).strip())
    except (FileNotFoundError, PermissionError):
        pass
    return files


def is_untracked_by_git(file_path, project_dir):
    """True если git не знает путь (реально новый файл), False если tracked (существующий).

    Надёжный сигнал новизны вместо «нет в README-индексе»: правка существующего,
    но не-проиндексированного файла не должна читаться как добавление нового.
    Fail-safe: при любой ошибке git → False (считаем НЕ новым, не блокируем).
    """
    try:
        r = subprocess.run(
            ["git", "-C", project_dir, "ls-files", "--error-unmatch", file_path],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode != 0  # non-zero → путь не отслеживается → новый файл
    except Exception:
        return False


def main():
    hook_input = json.loads(sys.stdin.read())

    tool_name = hook_input.get("tool_name", "")
    file_path = ""

    if tool_name == "Write":
        file_path = hook_input.get("tool_input", {}).get("file_path", "")
    elif tool_name == "Edit":
        file_path = hook_input.get("tool_input", {}).get("file_path", "")

    # Only check .md files
    if not file_path or not file_path.endswith(".md"):
        return

    # Skip README itself (avoid infinite loop)
    if os.path.basename(file_path) == "README.md":
        return

    # CLAUDE.md — управляющий файл харнесса Claude Code (иерархический, грузится
    # автоматически по папкам), не KH-документ; не входит в навигационный индекс README.
    if os.path.basename(file_path) == "CLAUDE.md":
        return

    # Skip excluded paths
    parts = file_path.split(os.sep)
    for part in SKIP_PATH_PARTS:
        if part in parts:
            return

    project_dir = get_project_dir()
    if not project_dir:
        return

    # Only check files inside KH
    if not is_in_kh(file_path, project_dir):
        return

    parent_dir = os.path.dirname(file_path)
    readme_path = os.path.join(parent_dir, "README.md")

    # Skip технические pipeline-папки (slice-v3 {date}-views/, {date}-audit/ etc.)
    # У них нет human-навигационного use case.
    parent_basename = os.path.basename(parent_dir)
    for pattern in SKIP_README_DIR_PATTERNS:
        if pattern.match(parent_basename):
            return

    # Get actual contents of directory
    actual_md = set(get_md_files_in_dir(parent_dir))
    actual_dirs = set(get_subdirs(parent_dir))
    actual_all = actual_md | actual_dirs

    # If no README exists and there are files — alert
    if not os.path.exists(readme_path):
        if actual_md:
            dir_name = os.path.basename(parent_dir)
            print(json.dumps({
                "decision": "block",
                "reason": (
                    f"[readme_sync] Папка {dir_name}/ содержит {len(actual_md)} "
                    f".md файл(ов), но не имеет README.md с навигационным индексом.\n"
                    f"Fix: создать {readme_path} по шаблону:\n"
                    f"  # {dir_name}\n"
                    f"  \n"
                    f"  Краткое описание назначения папки (1–2 фразы).\n"
                    f"  \n"
                    f"  | Документ | Статус | Описание | Обновлён |\n"
                    f"  |----------|--------|----------|----------|\n"
                    f"  | [filename.md](filename.md) | accepted | Что внутри | YYYY-MM-DD |\n"
                    f"Статус и дату брать из таблицы метаданных каждого файла "
                    f"(первые 20 строк, поля `Статус` и `Обновлён`)."
                ),
            }))
        return

    # сторож «Назначение папки». README подпапки KH обязан нести строку
    # `> **Назначение папки:** …` — одну фразу «зачем эта папка» по ТЕМЕ. Без неё
    # размещение документа сверяется с *названием* папки, а совпадение по слову ≠
    # по теме (прецедент 2026-07-28: кейс про agent-native лёг в okr-research/ по
    # слову «OKR»). Правило — CLAUDE.md §32 + context-distribution-rules §5/§7 шаг 5a.
    # Класс — warning, не block: строка требует осмысленной темы, а не подстановки;
    # блокировать Write чужого документа из-за чужого README = ложная one-door.
    if not README_PURPOSE_RE.search(read_text_safe(readme_path)):
        rel_readme = os.path.relpath(readme_path, project_dir)
        print(json.dumps({
            "decision": "warn",
            "reason": (
                f"[readme_sync] {rel_readme} не несёт строку «Назначение папки» "
                f"(CLAUDE.md §32).\n"
                f"Fix: добавить первой строкой после заголовка:\n"
                f"  > **Назначение папки:** {{зачем эта папка — тема, не список файлов}}\n"
                f"Формулировать по ТЕМЕ и отличию от соседних папок: именно с этой "
                f"строкой сверяется размещение новых документов."
            ),
        }))
        return

    # Check folder size — warn if 20+ .md files (excluding README) at this level
    # Skip size check for chronological archive folders (meetings/, etc.)
    # NOTE: проверка работает ИНДУКТИВНО — наличие подпапок (_archive/, research/) НЕ освобождает
    # от требования. Если на верхнем уровне ≥20 .md файлов, нужна дальнейшая декомпозиция,
    # независимо от того, есть ли уже подпапки.
    # Срабатывает только при ДОБАВЛЕНИИ РЕАЛЬНО НОВОГО файла: `Write` git-untracked файла.
    # `Edit` по определению не добавляет файл в папку (структура не меняется) → size-блок
    # не применяется. Прежний признак «basename нет в README-индексе» давал ложные
    # срабатывания: правка существующего, но не проиндексированного файла читалась как
    # добавление нового (fix 2026-07-13; прецедент — 15× ложных блоков на edit'ах ADR).
    # Порог 20 эмпирически обоснован распределением размеров папок KH (2026-05-16):
    # ~95% папок с ≥10 файлов — это здоровые каталоги однотипных объектов (concepts/,
    # decisions/, icp/, posts/), которые НЕ требуют декомпозиции; порог 20 убирает
    # ложноположительные срабатывания, продолжая ловить реальные критичные случаи.
    dir_basename = os.path.basename(parent_dir)
    is_new_file = (tool_name == "Write") and is_untracked_by_git(file_path, project_dir)
    if is_new_file and len(actual_md) >= 20 and dir_basename not in SKIP_SIZE_CHECK_DIRS:
        dir_name = os.path.basename(parent_dir)
        actual_subdirs = set(get_subdirs(parent_dir))
        existing_subdirs_note = (
            f" Существующие подпапки ({', '.join(sorted(actual_subdirs))}) не покрывают весь скоуп."
            if actual_subdirs else ""
        )
        print(json.dumps({
            "decision": "block",
            "reason": (
                f"[readme_sync] Папка {dir_name}/ содержит {len(actual_md)} .md "
                f"файлов на верхнем уровне — это ≥20.{existing_subdirs_note}\n"
                f"Согласно context-distribution-rules.md §5 — нужна декомпозиция.\n"
                f"Fix: либо\n"
                f"  1. Предложить разбивку на подпапки по смысловым темам "
                f"(не по дате, не по типу файла; каждая подпапка ≥3 файлов) "
                f"и выполнить `git mv` каждого файла в свою подпапку.\n"
                f"  2. Запустить `/cleanup-crew` для полного аудита структуры "
                f"(орфан-агент + placement-агент сделают разбивку)."
            ),
        }))
        return

    # README exists — check if the written file is in the index
    indexed = get_files_in_readme(readme_path)
    basename = os.path.basename(file_path)

    if basename not in indexed:
        dir_name = os.path.basename(parent_dir)
        # Check for other missing files too
        missing = actual_all - indexed
        if missing:
            missing_str = ", ".join(sorted(missing))
            sample = sorted(missing)[0]
            print(json.dumps({
                "decision": "block",
                "reason": (
                    f"[readme_sync] README.md в {dir_name}/ не содержит: "
                    f"{missing_str}.\n"
                    f"Fix: добавить в таблицу индекса {readme_path} строку:\n"
                    f"  | [{sample}]({sample}) | <статус> | <описание> | <YYYY-MM-DD> |\n"
                    f"Статус и дату взять из таблицы метаданных файла "
                    f"(первые 20 строк, поля `Статус` и `Обновлён`). "
                    f"Повторить для всех {len(missing)} недостающих файл(ов)."
                ),
            }))


if __name__ == "__main__":
    main()
