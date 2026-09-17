#!/usr/bin/env python3
"""PostToolUse hook на Bash: правка файла командой проходит те же проверки,
что и правка инструментом Write.

ПРОБЛЕМА. Свод держит 23 проверки на `Write`/`Edit` — метаданные документа,
стандарт оформления, англицизмы, реестр решений, синхронизация профилей.
Все они берут на вход одно: путь к файлу. Но файл правится и командой:
`cat > f.md`, `sed -i '' ... f.md`, `python3 -c "io.open('f.md','w')"`.
Для проверок это просто текст команды — путь оттуда никто не доставал,
и правка проходила молча.

ЗАМЕР (сессия 11.09): Bash 449 вызовов, Write 2, Edit 3. Отчёт писался
heredoc 7 раз и io.open 24 раза, через Write — ни разу. То есть 23 проверки
из 29 висели на инструменте, которым работа почти не велась. Обвязка
не «не сработала» — она не видела работы.

ЧТО ДЕЛАЕТ. Достаёт из команды пути файлов, которые она ПИШЕТ (не читает),
и для каждого зовёт те же проверки с тем же входом `{"tool_input":
{"file_path": ...}}`. Вердикты собираются и печатаются одним блоком.

ПОЧЕМУ ПОСРЕДНИК, А НЕ 23 ПРАВКИ. Проверки не переписываются: у них общий
контракт входа, и достаточно подставить путь. Дублировать разбор команды
в каждой — значит 23 раза повторить одну и ту же ошибку разбора.

Класс: будильник (печатает, не блокирует). Блокировать нельзя: разбор
команды — эвристика, а ложный блок Bash отрезает основной инструмент
работы. Прецедент того же дня: хук с битой строкой в настройках заблокировал
Bash целиком, чинилось через Edit. Вердикт вызванной проверки посредник
ПЕРЕДАЁТ, но не исполняет: проверка, блокирующая на Write (exit 2), через
посредник только говорит. Это осознанная потеря строгости — цена за то,
что путь достаётся эвристикой.

Три критерия: точка — Bash с записью в файл; каденция — каждая такая
команда; сигнал деградации — жалобы на ложные срабатывания (сузить
WRITE_RE) либо проверка, которую посредник зовёт впустую.
"""

import json
import os
import re
import subprocess
import sys

HOOKS = os.path.dirname(os.path.abspath(__file__))

# Команды, которые ПИШУТ в файл. Чтение (cat f, grep f) сюда не входит:
# проверки судят содержимое записи, а не факт просмотра.
WRITE_RE = [
    re.compile(r">>?\s*([^\s|&;<>()]+\.(?:md|py|json|html|css|ya?ml))"),      # cat > f, echo >> f
    re.compile(r"\bsed\s+-i\b[^|;&]*?\s([^\s|&;<>()]+\.(?:md|py|json|html|ya?ml))"),
    re.compile(r"\b(?:io\.)?open\(\s*['\"]([^'\"]+\.(?:md|py|json|html|ya?ml))['\"]\s*,\s*['\"][wa]"),
    re.compile(r"\bwrite_text\(|\bPath\(\s*['\"]([^'\"]+\.(?:md|py|json))['\"]"),
    re.compile(r"\b(?:cp|mv)\s+[^|;&]*?\s([^\s|&;<>()]+\.(?:md|py|json|html|ya?ml))\s*$"),
]

# Проверки, которые зовём. Только те, чей предмет — содержимое файла;
# синхронизаторы профилей и карт сюда не входят: они и так идут по своим
# путям и на Bash-правку реагируют тем же грепом.
# Проверки, которые зовём. Отбор — сухим прогоном по 336 транскриптам
# (37 585 вызовов Bash против 1 850 Write/Edit: 95% работы шло мимо проверок).
# Критерий переноса — ≥1 истинное срабатывание и 0 ложных.
RELAY = [
    "post_write_doc_standard_check",
    "post_write_md_check",
    "post_write_memory_gate",
    "post_write_principles_read_check",
]

TRANSCRIPT = ""

SKIP_DIRS = ("/tmp/", "/private/tmp/", "scratchpad", "node_modules", "/.git/",
             "/build/", "/__pycache__/")

# Цель ЗА пределами нашего репозитория молча превращается в ложный блок:
# проверки зовут `git ls-files` относительно нашего корня, чужой путь даёт
# `../../…`, git отвечает кодом 128, и «файла нет в индексе» читается как
# «файл новый». Прогон 12.09: 55 целей из 342 лежали вне репозитория либо в
# `build/`, и три из них дали BLOCK на файлах, которые в своём репозитории
# давно отслеживаются. Проверки написаны про НАШ репозиторий — за его край
# посредник их не выносит.


def targets(cmd: str) -> list[str]:
    out = []
    for rx in WRITE_RE:
        for m in rx.finditer(cmd):
            for g in m.groups():
                if g and g not in out:
                    out.append(g)
    return out


def sub_payload(path: str) -> dict:
    """Вход для вызываемой проверки.

    `tool_name` обязателен: восемь проверок из семнадцати гейтятся на
    `tool_name in ("Write", "Edit")` и без него молча выходят нулём —
    посредник звал бы их впустую и отчитывался, что зовёт. Ставим "Write":
    команда создала или переписала файл, это её случай.

    `transcript_path` пробрасываем, потому что `principles_read` и
    `runbook_read` судят не файл, а факт «открывали ли источник в ЭТОЙ
    сессии»; без транскрипта обе выходят нулём (fail-open) и тоже
    молчали бы всегда.
    """
    ti = {"file_path": path}
    # `content` читаем с диска: часть проверок судит по телу записи из
    # `tool_input` (так устроен Write), а у команды такого поля нет — без
    # него `principles_read_check` выходит нулём на любом входе. К моменту
    # PostToolUse файл уже записан, диск и есть то самое тело.
    try:
        if os.path.getsize(path) <= 2_000_000:
            ti["content"] = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        pass
    out = {"tool_name": "Write", "tool_input": ti}
    if TRANSCRIPT:
        out["transcript_path"] = TRANSCRIPT
    return out


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        return 0
    global TRANSCRIPT
    TRANSCRIPT = payload.get("transcript_path") or ""
    cmd = payload.get("tool_input", {}).get("command", "") or ""
    if not cmd:
        return 0

    # Корень ищем в трёх местах: cwd из payload, CLAUDE_PROJECT_DIR и `cd`
    # внутри самой команды. Полагаться на один — значит молча пропускать:
    # харнес зовёт хук не из того каталога, где выполнялась команда, и
    # относительный путь не находится (прогон 11.09: хук отрабатывал за 23 мс,
    # то есть цикл проверок не запускался ни разу).
    roots = []
    for r in (payload.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR"), os.getcwd()):
        if r and r not in roots:
            roots.append(r)
    m_cd = re.search(r"\bcd\s+[\"']?([^\"';&|]+)", cmd)
    if m_cd:
        c = m_cd.group(1).strip()
        if c not in roots:
            roots.insert(0, c)

    # Корень репозитория: за его краем проверки не судят (см. SKIP_DIRS).
    repo_root = ""
    for r in roots:
        try:
            rr = subprocess.run(["git", "-C", r, "rev-parse", "--show-toplevel"],
                                capture_output=True, text=True, timeout=5)
        except Exception:
            continue
        if rr.returncode == 0 and rr.stdout.strip():
            repo_root = os.path.realpath(rr.stdout.strip())
            break

    said = []
    for rel in targets(cmd):
        path = None
        if os.path.isabs(rel):
            path = rel if os.path.exists(rel) else None
        else:
            for r in roots:
                cand = os.path.join(r, rel)
                if os.path.exists(cand):
                    path = cand
                    break
        if not path or any(s in path for s in SKIP_DIRS):
            continue
        if repo_root and not os.path.realpath(path).startswith(repo_root):
            continue
        for name in RELAY:
            script = os.path.join(HOOKS, name + ".py")
            if not os.path.exists(script):
                continue
            try:
                r = subprocess.run(
                    [sys.executable, script],
                    input=json.dumps(sub_payload(path)),
                    capture_output=True, text=True, timeout=10)
            except Exception:
                continue
            msg = (r.stderr or "").strip()
            if not msg and r.stdout.strip().startswith("{"):
                try:
                    msg = json.loads(r.stdout).get("reason", "").strip()
                except Exception:
                    msg = ""
            if msg:
                said.append(f"  [{name}] {os.path.basename(path)}\n{msg}")

    if said:
        # Канал к модели — JSON с `reason`, не stderr. Замер свода: закрывающий
        # хук две недели отвечал allow + systemMessage — 1863 срабатывания,
        # до модели дошло ноль. Тот же класс, что 22-bis: интерфейс принял
        # сообщение, адресат не получил. Первая редакция этого посредника
        # печатала в stderr и повторила ошибку: 18 вызовов, ни одного
        # доставленного.
        text = ("[bash-relay] правка командой прошла проверки записи:\n"
                + "\n".join(said)
                + "\n\n  Те же проверки стоят на Write/Edit. Правка через Bash их "
                  "обходила — посредник зовёт их по пути из команды. "
                  "Починить файл и продолжить.")
        # Говорит, но не останавливает. Разбор команды — эвристика: путь
        # достаётся регулярным выражением из текста, и ошибка разбора здесь
        # стоит дороже пропуска — блок на Bash отрезает основной инструмент
        # работы (11.09: хук с битой строкой в настройках заблокировал Bash
        # целиком, чинить пришлось через Edit). Канал `additionalContext`
        # доносит текст до модели, не отменяя команду; `decision: block`
        # стоял здесь до 12.09 и противоречил собственному docstring.
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": text,
            }
        }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import _host_adapter
    sys.exit(_host_adapter.run_main(main))
