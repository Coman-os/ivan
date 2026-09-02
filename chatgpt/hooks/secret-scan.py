#!/usr/bin/env python3
"""secret-scan.py — deny-list сканер секретов для tracked-файлов.

Назначение: единая точка enforcement правила «секрет не попадает в git».
Ловит известные форматы credential'ов в содержимом файлов. Вызывается:
  - из .git/hooks/pre-commit (--staged) — блокирует коммит с секретом;
  - вручную / в CI: secret-scan.py <file...> или --all (весь tracked-tree).

Возврат: 0 — чисто; 1 — найдены секреты (печатает file:line + имя паттерна,
само значение маскируется); 2 — ошибка вызова.

Контекст: GP-020 (portfolio-snapshot сериализовал OAuth-токен в git), правило «секрет не попадает в tracked-артефакт» CLAUDE.md.
"""
import re
import subprocess
import sys

# Имя паттерна -> regex. Пороги длины подобраны так, чтобы НЕ ловить
# плейсхолдеры (y0__REDACTED-...) и deny-list-строки документации (-e "y0__").
PATTERNS = {
    "yandex-oauth": re.compile(r"y0__[A-Za-z0-9_-]{30,}"),
    "oauth-header": re.compile(r"OAuth\s+[A-Za-z0-9_-]{30,}"),
    "bearer-header": re.compile(r"Bearer\s+[A-Za-z0-9._-]{30,}"),
    "github-pat": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "aws-akid": re.compile(r"AKIA[0-9A-Z]{16}"),
}

# Маркеры строк, которые заведомо НЕ секрет (документация правила/сканера).
ALLOW_MARKERS = ("REDACTED", "deny-list", "<REDACTED", "PATTERNS =", "re.compile(")


def staged_files():
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True,
    ).stdout
    return [f for f in out.splitlines() if f.strip()]


def all_tracked():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout
    return [f for f in out.splitlines() if f.strip()]


def mask(value):
    head = value[:6]
    return f"{head}…[{len(value)} символов, скрыто]"


def scan(path):
    hits = []
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for lineno, line in enumerate(fh, 1):
                if any(m in line for m in ALLOW_MARKERS):
                    continue
                for name, rx in PATTERNS.items():
                    m = rx.search(line)
                    if m:
                        hits.append((path, lineno, name, mask(m.group(0))))
    except (OSError, UnicodeError):
        pass
    return hits


def main(argv):
    args = argv[1:]
    if "--staged" in args:
        files = staged_files()
    elif "--all" in args:
        files = all_tracked()
    elif args:
        files = args
    else:
        print("usage: secret-scan.py [--staged | --all | <file>...]", file=sys.stderr)
        return 2

    all_hits = []
    for f in files:
        all_hits.extend(scan(f))

    if all_hits:
        print("🔴 secret-scan: обнаружены потенциальные секреты — коммит заблокирован:\n")
        for path, lineno, name, masked in all_hits:
            print(f"  {path}:{lineno}  [{name}]  {masked}")
        print("\nУбери секрет из файла (значение — только в gitignored .env / env-переменной).")
        print("Если это ложное срабатывание — добавь маркер в ALLOW_MARKERS или обходи `git commit --no-verify`.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
