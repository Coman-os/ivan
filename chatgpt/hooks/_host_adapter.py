"""Чтение входа хука в единой форме независимо от среды запуска.

Зачем. Хуки написаны под форму Claude Code: путь файла в
`tool_input.file_path`, `tool_name` из набора Write/Edit, корень проекта в
переменной окружения `CLAUDE_PROJECT_DIR`. Codex (GPT Work) с мая 2026 даёт
хукам тот же JSON на stdin, те же имена событий и ту же блокировку
(`decision: block` либо код 2), но три поля выглядят иначе:

1. Запись файла приходит как `apply_patch`, и `tool_input` — это
   `{"command": "<текст заплатки>"}`. Поля `file_path` нет вовсе.
2. `tool_name` на проводе всегда `apply_patch`. Имена `Write` и `Edit` у
   Codex служат только отбору хуков в конфиге и в сам JSON не попадают —
   хук, сверяющий `tool_name` со строкой, молча вышел бы, ничего не
   проверив. Отказ этого рода незаметен: хук отработал, вердикт «чисто».
3. Переменной корня проекта нет, и окружение хука вычищается начисто
   (`env_clear` в command_runner.rs) — опереться на экспорт оболочки нельзя.

Одна заплатка трогает НЕСКОЛЬКО файлов (грамматика `hunk+`), поэтому
`read_inputs` возвращает список форм — по одной на файл, — а `for_each`
прогоняет тело хука по каждой.

Почему модуль, а не обёртка над запуском. В нашем репозитории хуки
маршрутизируются через `run_hook.py`, но в поставку он не едет: у
получателя хуки вызываются напрямую. Адаптер на диспетчере работал бы
только у нас — то есть ровно там, где он не нужен.

Класс: регламент (часть обвязки enforcement). Точка — чтение stdin в
каждом хуке. Сигнал деградации — хук, прошедший на Codex без вердикта
там, где на Claude Code он блокирует.

Источники формы Codex: learn.chatgpt.com/docs/hooks;
openai/codex — codex-rs/core/src/tools/handlers/apply_patch.rs (tool_input),
codex-rs/apply-patch/src/parser.rs (грамматика заплатки),
codex-rs/core/src/tools/hook_names.rs (Write/Edit как имена отбора),
codex-rs/hooks/src/engine/command_runner.rs (env_clear).
"""

import io
import json
import os
import re
import sys

# Заголовки файлов в заплатке. Разбор снисходительный: парсер Codex работает
# в нестрогом режиме и допускает пробелы вокруг маркеров
# (parser.rs: PARSE_IN_STRICT_MODE = false), поэтому якорим на \s*.
_FILE_HEADER = re.compile(
    r"^\s*\*\*\* (Add|Update|Delete) File: (.+?)\s*$", re.MULTILINE
)
_MOVE_TO = re.compile(r"^\s*\*\*\* Move to: (.+?)\s*$", re.MULTILINE)

# Строки итога в tool_response после применения заплатки: "A путь" / "M путь"
# / "D путь" (apply-patch/src/lib.rs). Для PostToolUse это надёжнее разбора
# заплатки: пути уже разрешены, заплатка уже применена.
_RESULT_LINE = re.compile(r"^\s*([AMD]) (.+?)\s*$", re.MULTILINE)

# Операция заплатки → имя инструмента в форме Claude Code. Хуки различают
# создание файла и правку существующего (readme_sync считает Write признаком
# нового файла), поэтому различие сохраняем.
_OP_TO_TOOL = {"Add": "Write", "Update": "Edit", "Delete": "Edit", "Move": "Edit"}
_RESULT_TO_TOOL = {"A": "Write", "M": "Edit", "D": "Edit"}

# Границы hunk'а внутри заплатки: следующий заголовок файла либо конец конверта.
_HUNK_SPLIT = re.compile(
    r"^\s*\*\*\* (?:Add|Update|Delete) File: |^\s*\*\*\* End Patch\s*$", re.MULTILINE
)


def _content_for(payload, path, tool_name):
    """Содержимое файла для `tool_input.content` — форма Claude Code.

    Зачем. Часть хуков читает не путь, а СОДЕРЖИМОЕ: гейт памяти ищет в нём
    `ladder_checked`, проверка секретов — credential'ы, проверка англицизмов —
    слова. У Claude Code содержимое лежит в `tool_input.content` (или
    `new_string` у правки). Codex не передаёт ни того, ни другого — в
    `tool_input` только текст заплатки. Без восстановления такой хук выходит
    на строке `if not content: return`, и отказ незаметен: код 0, вердикта
    нет. Ровно так замолчал memory_gate на первом прогоне.

    Два источника, в порядке надёжности:
      1. файл на диске — для PostToolUse он уже записан и несёт итог целиком;
      2. добавленные строки hunk'а — для PreToolUse, где файла ещё нет.
         Это ЧАСТИЧНОЕ содержимое: только строки с `+`, без неизменённого
         контекста. Для поиска запрещённого (секрет, англицизм) этого хватает —
         ищут в добавленном. Для проверки наличия (`ladder_checked` в шапке)
         частичного текста может не хватить, если правка не трогала шапку;
         такой хук на PreToolUse даст ложное «нет поля». Поэтому событие
         PostToolUse для проверок наличия предпочтительно — там читается диск.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        pass

    patch = (payload.get("tool_input") or {}).get("command")
    if not isinstance(patch, str):
        return ""

    # Ищем hunk именно этого файла: от его заголовка до следующей границы.
    basename = os.path.basename(path)
    for match in _FILE_HEADER.finditer(patch):
        if os.path.basename(match.group(2).strip()) != basename:
            continue
        rest = patch[match.end():]
        stop = _HUNK_SPLIT.search(rest)
        body = rest[: stop.start()] if stop else rest
        added = [
            line[1:] for line in body.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]
        return "\n".join(added)
    return ""


def is_codex_payload(payload):
    """Вход пришёл от Codex, а не от Claude Code.

    Признак — `tool_name: apply_patch`: у Claude Code такого инструмента нет,
    у Codex он на проводе всегда именно такой. Отсутствие `file_path` при
    наличии `command` — второй признак, на случай смены имени инструмента.
    """
    if payload.get("tool_name") == "apply_patch":
        return True
    tool_input = payload.get("tool_input") or {}
    return "file_path" not in tool_input and "command" in tool_input


def project_root(payload=None):
    """Корень проекта: переменная окружения, иначе подъём от cwd до .git.

    У Claude Code есть CLAUDE_PROJECT_DIR. У Codex переменной нет и окружение
    вычищено, но `cwd` приходит в JSON на каждом событии и хук запускается
    именно в этом каталоге. Подъём до `.git` — единственный доступный способ
    найти корень репозитория; при неудаче возвращаем сам cwd, чтобы хук
    работал в вырожденном случае, а не падал.
    """
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return env_root

    start = (payload or {}).get("cwd") or os.getcwd()
    current = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return os.path.abspath(start)
        current = parent


def _paths_from_response(payload):
    """Пути из tool_response — предпочтительный источник для PostToolUse.

    tool_response — голая строка вида
    "Success. Updated the following files:\\nM путь\\nA путь".
    Заголовок отсекается требованием однобуквенной метки в начале строки.
    """
    response = payload.get("tool_response")
    if not isinstance(response, str) or "files" not in response:
        return []
    return [
        (_RESULT_TO_TOOL[mark], path) for mark, path in _RESULT_LINE.findall(response)
    ]


def _paths_from_patch(payload):
    """Пути из текста заплатки — источник для PreToolUse (файлы ещё не записаны).

    `Move to:` даёт ВТОРОЙ путь внутри hunk'а Update: файл переезжает, и
    проверять надо место назначения тоже — правило размещения смотрит именно
    на новый путь.
    """
    patch = (payload.get("tool_input") or {}).get("command")
    if not isinstance(patch, str):
        return []
    found = [(_OP_TO_TOOL[op], path) for op, path in _FILE_HEADER.findall(patch)]
    found += [("Edit", path) for path in _MOVE_TO.findall(patch)]
    return found


def normalize(payload):
    """Вход хука → список форм Claude Code, по одной на затронутый файл.

    Вход не от Codex либо файлов в нём нет → список из одного исходного
    payload: хук получает ровно то, что пришло. Это делает адаптер прозрачным
    на родной установке — форма не трогается, поведение не меняется.

    Путь резолвится относительно cwd: заплатка Codex несёт как абсолютные,
    так и относительные пути, а хуки открывают файл по этому значению.
    """
    if not isinstance(payload, dict) or not is_codex_payload(payload):
        return [payload]

    found = _paths_from_response(payload) or _paths_from_patch(payload)
    if not found:
        return [payload]

    cwd = payload.get("cwd") or os.getcwd()
    seen = set()
    normalized = []
    for tool_name, path in found:
        abs_path = path if os.path.isabs(path) else os.path.join(cwd, path)
        abs_path = os.path.normpath(abs_path)
        if abs_path in seen:
            continue
        seen.add(abs_path)

        form = dict(payload)
        form["tool_name"] = tool_name
        form["tool_input"] = dict(payload.get("tool_input") or {})
        form["tool_input"]["file_path"] = abs_path
        # Содержимое: часть хуков читает его, а не путь. Без него они выходят
        # молча — отказ, неотличимый от «проверено, чисто».
        content = _content_for(payload, abs_path, tool_name)
        if content:
            form["tool_input"]["content"] = content
            if tool_name == "Edit":
                # Правка у Claude Code несёт новый текст в `new_string`;
                # хуки, читающие его, иначе увидят пустоту.
                form["tool_input"]["new_string"] = content
        # Исходное имя сохраняем: телеметрия должна показывать, что пришло
        # с провода, иначе журнал соврёт о среде запуска.
        form["host_tool_name"] = payload.get("tool_name", "")
        normalized.append(form)

    return normalized


def read_inputs(raw=None):
    """Прочитать stdin и вернуть список форм — замена `json.loads(sys.stdin.read())`.

    Единственная строка, которую правит экспортёр в теле каждого хука.
    Нечитаемый вход → пустой список: хук молча выходит, не мешая сессии.
    """
    if raw is None:
        raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return []
    return normalize(payload)


def for_each(body, raw=None):
    """Прогнать тело хука по каждому файлу входа; вернуть код выхода.

    `body(hook_input)` — обычная логика хука, ожидающая форму Claude Code.
    Возврат: код выхода (`int`), либо None/0 как «чисто».

    Первый ненулевой код прекращает разбор и возвращается наружу. Иначе
    блокирующий хук, сработав на первом файле заплатки, отдал бы 0 после
    последнего — и запись прошла бы.
    """
    for hook_input in read_inputs(raw):
        code = body(hook_input)
        if code:
            return code
    return 0


def run_main(main):
    """Запустить `main()` хука по одному разу на каждый файл входа.

    Подменяет `sys.stdin` формой Claude Code перед каждым вызовом, поэтому
    тело хука не правится вовсе: оно как читало `json.loads(sys.stdin.read())`,
    так и читает. Единственная правка в файле хука — замена строки запуска
    на `_host_adapter.run_main(main)`.

    Почему подмена stdin, а не аргумент. У хуков две формы входа
    (`hook_input = json.loads(...)` в теле main и `return json.loads(...)`
    в отдельной функции-читателе) и две формы запуска (`main()` и
    `sys.exit(main())`). Общего у них ровно одно: main вызывается без
    аргументов и читает stdin сам. Подмена stdin — единственная точка,
    работающая для всех, без разбора тела каждого хука.

    Коды выхода: хук либо возвращает код, либо зовёт `sys.exit` сам.
    Первый ненулевой прекращает разбор — блокировка на одном файле заплатки
    обязана остановить всю запись, иначе после последнего файла ушёл бы 0.
    """
    raw = sys.stdin.read()
    forms = read_inputs(raw)
    if not forms:
        return 0

    for hook_input in forms:
        sys.stdin = io.StringIO(json.dumps(hook_input, ensure_ascii=False))
        try:
            code = main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        if code:
            return code
    return 0
