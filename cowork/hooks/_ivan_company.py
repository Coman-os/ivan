#!/usr/bin/env python3
"""Opt-in company module: one document validator for agents, Git and CI.

No downloaded code is executed by the hook. Only the installed JSON settings
and bounded UTF-8 rules are read. Installation is an explicit CLI action.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

CONFIG = ".ivan/project.json"
SKIP = {".git", ".ivan", "node_modules", "__pycache__", ".venv"}


def confined(root: Path, value: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError("Путь должен быть относительным и непустым")
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Путь выходит за пределы проекта")
    return path


def read_json(path: Path, allowed: set[str]) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - allowed or data.get("schema_version") != 1:
        raise ValueError(f"Несовместимая конфигурация: {path.name}")
    return data


def settings(root: Path) -> dict | None:
    if any((p / ".es-connection.json").exists() for p in (root, *root.parents)):
        return None
    if not (root / CONFIG).exists():
        return None
    project = read_json(root / CONFIG, {"schema_version", "company_module", "documents"})
    location = os.environ.get("IVAN_COMPANY_MODULE") or project["company_module"]
    if not isinstance(location, str) or not location:
        raise ValueError("Не указан путь к модулю компании")
    module_dir = (root / location).resolve()
    module = read_json(module_dir / "module.json", {"schema_version", "name", "version", "rules", "documents"})
    rules = confined(module_dir, module["rules"])
    if rules.stat().st_size > 24000:
        raise ValueError("Правила компании превышают 24 КБ")
    docs = dict(module["documents"])
    docs.update(project.get("documents", {}))
    if set(docs) - {"roots", "exclude", "warn_file_count"}:
        raise ValueError("Неизвестная настройка; обязательные проверки отключать нельзя")
    roots = docs.get("roots")
    if not isinstance(roots, list) or not roots or not all(isinstance(x, str) for x in roots):
        raise ValueError("Нужен непустой список проверяемых папок")
    exclusions = docs.get("exclude", [])
    if not isinstance(exclusions, list) or not all(isinstance(x, str) for x in exclusions):
        raise ValueError("Исключения задаются списком относительных папок")
    for value in roots + exclusions:
        confined(root, value)
    if any(confined(root, area).is_relative_to(confined(root, exc))
           for area in roots for exc in exclusions):
        raise ValueError("Исключение отключает всю проверяемую папку")
    threshold = docs.get("warn_file_count", 10)
    if type(threshold) is not int or threshold < 3:
        raise ValueError("Порог предупреждения должен быть целым числом от 3")
    return {"name": module["name"], "version": module.get("version", "unknown"), "rules": rules, "roots": roots,
            "exclude": exclusions, "warn_file_count": threshold}


def discover(start: Path) -> Path | None:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for folder in (current, *current.parents):
        if (folder / CONFIG).is_file():
            return folder
        if (folder / ".git").exists():
            break
    return None


def rules_context(root: Path) -> str:
    config = settings(root)
    if config is None:
        return ""
    rules = config["rules"].read_text(encoding="utf-8")
    return (f"Правила компании: {config['name']} ({config['version']}). Дополняют базового помощника; "
            "не отменяют его ограничения. При содержательном противоречии назови его, "
            "не выбирай молча.\n" + rules)


def markdown(text: str, strip_inline: bool = True) -> str:
    text = re.sub(r"(?ms)^\s*(`{3,}|~{3,}).*?^\s*\1\s*$", "", text)
    return re.sub(r"`[^`\n]+`", "", text) if strip_inline else text


def links(text: str) -> list[str]:
    text = markdown(text)
    # Balanced parentheses in destinations, plus angle-bracket paths with spaces.
    found = []
    for match in re.finditer(r"!?\[[^\]\n]*\]\(\s*", text):
        start = match.end()
        if text[start:start+1] == "<":
            end = text.find(">", start)
            if end != -1:
                found.append(text[start:end+1])
            continue
        depth, end = 0, start
        while end < len(text):
            char = text[end]
            if char.isspace() or (char == ")" and depth == 0):
                break
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            end += 1
        found.append(text[start:end])
    found += re.findall(r"(?m)^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", text)
    found += re.findall(r"\b(?:href|src)\s*=\s*['\"]([^'\"]+)['\"]", text, re.I)
    return [x[1:-1] if x.startswith("<") else x for x in found]


def anchors(file: Path) -> set[str]:
    text = markdown(file.read_text(encoding="utf-8"), strip_inline=False)
    result = set(re.findall(r"\bid\s*=\s*['\"]([^'\"]+)['\"]", text, re.I))
    counts: dict[str, int] = {}
    headings = re.findall(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", text)
    headings += re.findall(r"(?m)^([^\n]+)\n\s{0,3}(?:=+|-+)\s*$", text)
    for line in headings:
        slug = re.sub(r"[^\w\s-]", "", re.sub(r"<[^>]*>", "", line).lower())
        slug = re.sub(r"\s", "-", slug)
        n = counts.get(slug, 0)
        counts[slug] = n + 1
        result.add(f"{slug}-{n}" if n else slug)
    return result


def document_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.is_file()
                  and p.suffix.lower() in {".md", ".pdf", ".docx", ".xlsx", ".pptx", ".html"}
                  and p.name.lower() != "readme.md")


def in_scope(root: Path, path: Path, config: dict) -> bool:
    if not path.is_relative_to(root):
        return False
    rel = path.relative_to(root)
    if set(rel.parts) & SKIP:
        return False
    return any(path.is_relative_to(confined(root, r)) for r in config["roots"]) and not any(
        path.is_relative_to(confined(root, r)) for r in config["exclude"])


def git_paths(root: Path, base: str | None) -> tuple[list[Path], set[Path]]:
    # Check the whole opted-in area, including incoming links to removed files.
    raw = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "--cached",
                                   "--others", "--exclude-standard"])
    paths = [root / os.fsdecode(p) for p in raw.split(b"\0") if p]
    new = set()
    if base:
        raw_new = subprocess.check_output(["git", "-C", str(root), "diff", "--name-only", "-z",
                                           "--diff-filter=A", base, "--"])
        new = {root / os.fsdecode(p) for p in raw_new.split(b"\0") if p}
    return paths, new


def check(root: Path, paths: list[Path] | None = None, base: str | None = None) -> list[dict]:
    root = root.resolve()
    config = settings(root)
    if config is None:
        return []
    new: set[Path] = set()
    if paths is None:
        paths, new = git_paths(root, base)
    lexical = {p.parent.resolve() / p.name for p in paths}
    touched = {p for p in lexical if in_scope(root, p, config)}
    candidates = {p.resolve() for p in touched if p.exists() and in_scope(root, p.resolve(), config)}
    findings: list[dict] = []
    def add(level: str, path: Path, message: str):
        findings.append({"level": level, "path": str(path.relative_to(root)), "message": message})
    for path in touched:
        if path.is_symlink() and not in_scope(root, path.resolve(), config):
            add("error", path, "Документ-ссылка выходит из проверяемой области; содержимое не проверено")
    folders = {p.parent for p in touched if p.parent.is_dir() and p.suffix.lower() in {
        ".md", ".pdf", ".docx", ".xlsx", ".pptx", ".html"}}
    # A README edit must recheck the indexed files, not just README itself.
    for folder in sorted(folders):
        files = document_files(folder)
        readme = folder / "README.md"
        if not readme.is_file():
            if files:
                add("error", folder, "Нет README.md с назначением и индексом документов")
            continue
        text = readme.read_text(encoding="utf-8")
        if not re.search(r"Назначение\s+папки\s*[:|]|Folder\s+purpose\s*:", text, re.I):
            add("error", readme, "В README не указано назначение папки")
        indexed = set()
        for link in links(text):
            parsed = urlsplit(link)
            if not parsed.scheme and not parsed.netloc and parsed.path:
                indexed.add((folder / unquote(parsed.path)).resolve())
        for file in files:
            if file.resolve() in candidates or readme in touched:
                if file.resolve() not in indexed:
                    add("error", file, "Документ отсутствует в индексе README")
        if len(files) >= config["warn_file_count"]:
            add("warning", folder, f"Документов: {len(files)}. Проверьте необходимость группировки; разделение не обязательно")
        if 0 < len(files) < 3 and any(p.parent.resolve() == folder for p in new):
            # Warn only for a new folder: no README existed in the base tree.
            old = subprocess.run(["git", "-C", str(root), "cat-file", "-e",
                                  f"{base}:{readme.relative_to(root).as_posix()}"], capture_output=True)
            if old.returncode:
                add("warning", folder, "В новой подпапке один-два документа: проверьте, нужна ли отдельная папка")
    for file in sorted(candidates):
        if file.suffix.lower() != ".md" or not file.is_file():
            continue
        for target in links(file.read_text(encoding="utf-8")):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            destination = (file.parent / unquote(parsed.path)).resolve() if parsed.path else file
            if not destination.exists():
                add("error", file, f"Локальная ссылка ведёт на отсутствующий путь: {target}")
            elif parsed.fragment and destination.suffix.lower() in {".md", ".html"}:
                if unquote(parsed.fragment) not in anchors(destination):
                    add("error", file, f"Локальная ссылка ведёт на отсутствующий якорь: {target}")
    return findings


def install(source: Path, target: Path) -> None:
    """Connect a local company clone; updates remain company-wide, not pinned."""
    source, target = source.resolve(), target.resolve()
    module = read_json(source / "module.json", {"schema_version", "name", "version", "rules", "documents"})
    rules = confined(source, module["rules"])
    # Validate fully before touching the destination using a temporary project.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        trial = Path(d)
        (trial / ".ivan/company").mkdir(parents=True)
        shutil.copy2(source / "module.json", trial / ".ivan/company/module.json")
        dest_rules = confined(trial / ".ivan/company", module["rules"])
        dest_rules.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rules, dest_rules)
        (trial / CONFIG).write_text(json.dumps({"schema_version": 1, "company_module": ".ivan/company"}))
        settings(trial)
    if (target / CONFIG).exists():
        raise ValueError("Модуль уже подключён; измените существующее подключение явно")
    (target / ".ivan").mkdir(parents=True, exist_ok=True)
    (target / CONFIG).write_text(json.dumps({"schema_version": 1,
        "company_module": os.path.relpath(source, target)}, indent=2)+"\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    check_parser = subs.add_parser("check")
    check_parser.add_argument("--root", type=Path, default=Path.cwd())
    check_parser.add_argument("--base")
    installer = subs.add_parser("install")
    installer.add_argument("--source", type=Path, required=True)
    installer.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "install":
            install(args.source, args.target)
            print("Модуль подключён локально. Проверьте diff и добавьте файлы в Git.")
            return 0
        if not (args.root / CONFIG).is_file():
            raise ValueError("Нет .ivan/project.json: проект не подключён, проверка не выполнена")
        findings = check(args.root, base=args.base)
        for finding in findings:
            print(f"{finding['level']}: {finding['path']}: {finding['message']}")
        return int(any(x["level"] == "error" for x in findings))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(f"Ошибка проверки модуля: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
