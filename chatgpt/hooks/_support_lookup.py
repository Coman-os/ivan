#!/usr/bin/env python3
"""Спутник: отпечаток объекта из паспорта сборки и поиск в общем месте поддержки.

Не хук (имя с подчёркиванием, на событие не регистрируется). Им пользуются
три точки, названные в ADR первой линии поддержки (2026-09-08):
  • адаптер хуков — при падении хука печатает хвост с отпечатком и адресом;
  • самопроверка при старте — раз в сессию спрашивает общее место, что
    известно про эту версию обвязки;
  • навык поддержки — по фразе человека ищет, сравнивает версию, оформляет.

Отпечаток — пара «файл в паспорте · хэш файла»; версия обвязки — поле
«найдено в», не часть отпечатка (иначе один файл в 2.4.0 и 2.5.0 не
сопоставится). Хэши пишет сборщик в BUILD-MANIFEST.json → `files`.

Сеть: только чтение открытого репозитория без токена (решение владельца
08.09: общее место открытое). Переменная GITHUB_TOKEN, если есть в
окружении, добавляется заголовком — значение никуда не пишется. Любой
сетевой отказ — словами с причиной, не молчанием (правило 22-bis о тихом
успехе: ответ проверяется на размер и на ключ `total_count`).

У нас (в монорепозитории, без паспорта) спутник работает в вырожденном
режиме: отпечаток без хэша из паспорта, адрес — из умолчаний.

Класс: спутник будильников. Точка активации — у вызывающих. Сигнал
деградации — хвост при падении хука без адреса ИЛИ самопроверка молчит
при недоступной сети.
"""
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

# Умолчания на случай паспорта без поля `support` (сборки до 2.5.0) и для
# запуска у нас. Сборщик кладёт то же самое в паспорт из hook_selection.
SUPPORT_DEFAULT = {
    "repo": "Coman-os/ivan",
    "issues": "https://github.com/Coman-os/ivan/issues",
    "discussions": "https://github.com/Coman-os/ivan/discussions",
    "api": "https://api.github.com",
    "marketplace": "https://raw.githubusercontent.com/Coman-os/ivan/main/"
                   ".claude-plugin/marketplace.json",
}
TIMEOUT = 4
MANIFEST = "BUILD-MANIFEST.json"


# --------------------------------------------------------------------- паспорт
def plugin_root(start=None):
    """Каталог плагина — тот, где лежит паспорт сборки.

    Порядок: CLAUDE_PLUGIN_ROOT / PLUGIN_ROOT (платформа даёт его хукам),
    иначе подъём от каталога этого файла. У нас паспорта нет — вернётся None,
    и всё ниже работает в вырожденном режиме.
    """
    for var in ("CLAUDE_PLUGIN_ROOT", "PLUGIN_ROOT"):
        root = os.environ.get(var)
        if root and os.path.exists(os.path.join(root, MANIFEST)):
            return root
    cur = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    while True:
        if os.path.exists(os.path.join(cur, MANIFEST)):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def load_manifest(root=None):
    root = root or plugin_root()
    if not root:
        return {}
    try:
        with open(os.path.join(root, MANIFEST), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def support_of(manifest=None):
    m = manifest if manifest is not None else load_manifest()
    sup = dict(SUPPORT_DEFAULT)
    sup.update((m or {}).get("support") or {})
    return sup


def harness_version(manifest=None):
    m = manifest if manifest is not None else load_manifest()
    return ((m or {}).get("harness") or {}).get("version") or "—"


def file_sha(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:12]
    except OSError:
        return "—"


def fingerprint(path, manifest=None, root=None):
    """Отпечаток файла: имя относительно плагина, хэш сейчас, хэш по паспорту."""
    m = manifest if manifest is not None else load_manifest(root)
    root = root or plugin_root()
    abspath = os.path.abspath(path)
    rel = (os.path.relpath(abspath, root).replace(os.sep, "/")
           if root and abspath.startswith(os.path.abspath(root)) else os.path.basename(abspath))
    recorded = ((m or {}).get("files") or {}).get(rel)
    sha = file_sha(abspath)
    return {
        "file": rel,
        "sha": sha,
        "recorded_sha": recorded,
        "in_manifest": recorded is not None,
        "modified": bool(recorded) and recorded != sha,
        "harness": harness_version(m),
    }


def tail(fp, support=None):
    """Хвост для человека и помощника: что приложить к обращению и куда идти."""
    sup = support or support_of()
    where = "в поставке" if fp.get("in_manifest") else "вне паспорта сборки"
    changed = " (файл изменён после установки)" if fp.get("modified") else ""
    return (f"Обвязка {fp.get('harness')}, файл {fp.get('file')} {where}, "
            f"отпечаток {fp.get('sha')}{changed}. "
            f"Общее место поддержки: {sup['issues']} — обращения, "
            f"{sup['discussions']} — вопросы. Скажите помощнику: "
            f"«спроси у поставщика» — он приложит эту строку.")


# --------------------------------------------------------------------- сеть
def _get_json(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "ivan-support-lookup",
    })
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        raise ValueError("пустой ответ")
    return json.loads(raw.decode("utf-8"))


def _search(query, support, timeout=TIMEOUT, limit=5):
    """Поиск обращений; результат всегда со словом о причине, если не удался."""
    url = (f"{support['api']}/search/issues?per_page={limit}&q="
           + urllib.parse.quote(query, safe=""))
    try:
        data = _get_json(url, timeout)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "reason": f"общее место ответило {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "reason": f"общее место недоступно: {exc}"}
    if "total_count" not in data:
        return {"ok": False, "reason": "ответ без поля total_count — тихий сбой"}
    items = [{
        "number": it.get("number"),
        "title": it.get("title"),
        "url": it.get("html_url"),
        "state": it.get("state"),
        "labels": [lab.get("name") for lab in it.get("labels") or []],
        "fixed_in": next((lab.get("name").split(":", 1)[1] for lab in it.get("labels") or []
                          if str(lab.get("name", "")).startswith("починено:")), None),
    } for it in data.get("items") or []]
    return {"ok": True, "count": data["total_count"], "items": items}


def search_version(version, support=None, timeout=TIMEOUT):
    """Открытые обращения, где в теле названа эта версия обвязки."""
    sup = support or support_of()
    q = f'repo:{sup["repo"]} is:issue is:open "{version}" in:body'
    return _search(q, sup, timeout)


def search_file(rel_file, support=None, timeout=TIMEOUT):
    """Обращения (любого статуса) по имени файла — «что с этим у других»."""
    sup = support or support_of()
    q = f'repo:{sup["repo"]} is:issue "{rel_file}"'
    return _search(q, sup, timeout)


def latest_version(support=None, timeout=TIMEOUT):
    """Последняя опубликованная версия — из манифеста маркетплейса в репозитории."""
    sup = support or support_of()
    try:
        data = _get_json(sup["marketplace"], timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "reason": f"манифест выпуска недоступен: {exc}"}
    versions = [p.get("version") for p in data.get("plugins") or [] if p.get("version")]
    if not versions:
        return {"ok": False, "reason": "в манифесте выпуска нет версий"}
    return {"ok": True, "latest": max(versions, key=_vkey), "all": versions}


def _vkey(v):
    parts = []
    for p in str(v).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return parts


def sent_by_me(support=None):
    """Обращения текущей учётки — только через gh (нужна авторизация)."""
    sup = support or support_of()
    try:
        out = subprocess.run(
            ["gh", "issue", "list", "-R", sup["repo"], "--author", "@me",
             "--state", "all", "--json", "number,title,state,url"],
            capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "reason": f"gh недоступен: {exc}"}
    if out.returncode != 0:
        return {"ok": False, "reason": (out.stderr or "gh вернул ошибку").strip()[:200]}
    try:
        return {"ok": True, "items": json.loads(out.stdout or "[]")}
    except ValueError:
        return {"ok": False, "reason": "gh вернул не JSON"}


# ------------------------------------------------------------- человеческое
def summary_for_version(version, support=None, timeout=TIMEOUT):
    """Одна-две строки для самопроверки при старте."""
    res = search_version(version, support, timeout)
    if not res["ok"]:
        return f"Общее место поддержки: {res['reason']} — известные дефекты этой версии не проверены."
    if res["count"] == 0:
        return f"Общее место поддержки: открытых обращений на обвязку {version} нет."
    fixed = [it for it in res["items"] if it.get("fixed_in")]
    line = f"Общее место поддержки: на обвязку {version} открыто {res['count']} обращ."
    if fixed:
        line += " Починено в " + ", ".join(sorted({it["fixed_in"] for it in fixed})) + "."
    titles = "; ".join(f"#{it['number']} {it['title']}" for it in res["items"][:3])
    return line + (f" Например: {titles}." if titles else "")


def main(argv):
    m = load_manifest()
    sup = support_of(m)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "--fingerprint" and rest:
        fp = fingerprint(rest[0], m)
        print(json.dumps(fp, ensure_ascii=False))
        print(tail(fp, sup))
        return 0
    if cmd == "--others":
        if rest:
            fp = fingerprint(rest[0], m)
            res = search_file(fp["file"], sup)
        else:
            res = search_version(harness_version(m), sup)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0 if res["ok"] else 1
    if cmd == "--version-summary":
        print(summary_for_version(harness_version(m), sup))
        return 0
    if cmd == "--update":
        res = latest_version(sup)
        res["installed"] = harness_version(m)
        if res["ok"]:
            res["behind"] = (None if res["installed"] == "—"
                             else _vkey(res["latest"]) > _vkey(res["installed"]))
        print(json.dumps(res, ensure_ascii=False))
        return 0 if res["ok"] else 1
    if cmd == "--sent":
        print(json.dumps(sent_by_me(sup), ensure_ascii=False, indent=1))
        return 0
    if cmd == "--support":
        print(json.dumps(sup, ensure_ascii=False))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
