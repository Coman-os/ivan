#!/usr/bin/env python3
"""
PostToolUse hook: enforcement of CLAUDE.md rule 9 — capabilities-map must be
updated when a skill is created (class: reglament — activation point is
machine-observable, map drift is an observed problem; harness review 2026-07-19,
spec W4.2).

Other post_write hooks skip .claude/ entirely (SKIP_PATH_PARTS contains the
".claude" atom); this hook is the dedicated gate for the skills area and does
NOT touch their skip lists.

Triggers on Write/Edit of *.md under .claude/commands/ or .claude/skills/:
  - NEW file (not in git ls-files) → BLOCK (exit 2) unless
    skill-capabilities-map.md was modified recently (same session,
    FRESHNESS_WINDOW) — directive message tells to update the map first.
  - EDIT of existing skill file → soft warn (exit 0 + stderr): "сверь
    Decision Tree capabilities-map".

Deletions/renames happen via Bash and are invisible to PostToolUse — accepted
limitation; the /improvement-review enforcement agent audits the map against
territory on its cadence.

Exit codes: 0 = pass/warn, 2 = block (directive message in stderr).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

MAP_REL = "coman-os/scl/intelligence/knowledge-hub/skills/skill-capabilities-map.md"
FRESHNESS_WINDOW_SEC = 60 * 60  # map touched within the last hour == updated this session
SKIP_BASENAMES = {"README.md"}
SKILL_AREAS = ("/.claude/commands/", "/.claude/skills/")
# resources/ are style guides, not skills
SKIP_PARTS = ("/resources/", "/templates/", "/_archive/")


def get_payload():
    try:
        return json.loads(sys.stdin.read())
    except Exception:
        return {}


def is_skill_path(file_path: str) -> bool:
    if not file_path or not file_path.endswith(".md"):
        return False
    norm = file_path.replace("\\", "/")
    if os.path.basename(norm) in SKIP_BASENAMES:
        return False
    if any(p in norm for p in SKIP_PARTS):
        return False
    return any(a in norm for a in SKILL_AREAS)


def is_new_file(repo_root: Path, file_path: str) -> bool:
    try:
        rel = os.path.relpath(file_path, repo_root)
        res = subprocess.run(
            ["git", "ls-files", "--error-unmatch", rel],
            cwd=repo_root, capture_output=True, timeout=10,
        )
        return res.returncode != 0
    except Exception:
        return False  # fail-open: cannot determine → treat as existing


def main():
    payload = get_payload()
    if (payload.get("tool_name") or payload.get("name") or "") not in ("Write", "Edit"):
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not is_skill_path(file_path):
        return 0

    repo_root_env = os.environ.get("CLAUDE_PROJECT_DIR")
    repo_root = Path(repo_root_env) if repo_root_env else Path(__file__).resolve().parent.parent.parent
    map_path = repo_root / MAP_REL

    if not is_new_file(repo_root, file_path):
        print(
            "[skill_map_check] Правка skill: сверь Decision Tree / цепочки в "
            f"{MAP_REL} (правило «создал skill → обнови карту»). Warn, не блок.",
            file=sys.stderr,
        )
        return 0

    map_fresh = map_path.exists() and (time.time() - map_path.stat().st_mtime) < FRESHNESS_WINDOW_SEC
    if map_fresh:
        return 0

    print(
        "[skill_map_check] BLOCK: создан новый skill, а capabilities-map не обновлена "
        "в этой сессии (правило «создал skill → обнови карту», класс: регламент).\n"
        f"Fix: обнови {MAP_REL} (Decision Tree, цепочки, «Обновлён»), затем повтори Write skill-файла.\n"
        "Осознанный обход: touch карты без содержательной правки — виден в git diff.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
