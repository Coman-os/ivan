# Архитектура принципов харнеса — 6-слойная карта

| Параметр | Значение |
|----------|----------|
| Версия | 1.0.0 |
| Статус | ✅ Active |
| Слой | meta (карта над 6 слоями принципов) |
| principle_layer | meta |
| Назначение | Единая точка входа в принципы харнеса, разнесённые по source-документам. Три функции: навигационная карта, architectural narrative, Decision Tree «какой принцип применить». Source-документы — SSOT каждого слоя, этот файл — индекс над ними. |

---

# Principles Architecture — 6-слойная карта принципов системы

Единая точка входа в принципы харнеса, разнесённые по source-документам. Три функции: навигационная карта, architectural narrative, Decision Tree «какой принцип применить». Source-документы — SSOT каждого слоя, этот файл — индекс над ними.

## Для кого этот документ

- **Кому адресован:** автор системы, агент в сессии, будущие участники команды
- **Что нужно знать заранее:** CLAUDE.md (правила-gateway'и), operating-principles.md (универсальные принципы), agent-design-principles.md (§A + §B)
- **Что читатель сможет сделать:** найти нужный принцип за один lookup, понять место нового правила при добавлении, увидеть архитектуру принципов целиком

## Словарь

- **Принцип** — метаправило мышления или дизайна, применимое в нескольких ситуациях. Отвечает на «как думать / как проектировать», не на «что положить в файл X»
- **Слой** — группа принципов, регулирующих один класс поведения. Выведен тестом дефицита (см. § Метод классификации)
- **Source-документ** — SSOT принципов одного слоя. Принципы цитируются по ссылке, не копируются (правило «Ссылайся, не повторяй»)
- **Gateway** — правило в CLAUDE.md, отсылающее к source-документу. Не содержит принципа, диспетчеризирует

## История и контекст

- **Что было раньше:** Принципы существовали в разных файлах без единой карты. Поиск нужного — обход всех документов
- **Какие были варианты:** (1) Только индекс + cross-references, (2) Индекс + рефакторинг дублей, (3) Полная пересборка, (4) Гибрид: layer-индекс + точечный рефакторинг
- **Почему выбран этот:** Гибрид — минимальное вмешательство в существующие документы при максимальной видимости архитектуры. Аудит показал 0 реальных противоречий и контролируемые дубли (Signal Density, Бритва Оккама применены в разных доменах с явными ссылками на SSOT)

## Назначение и слои

Система регулирует поведение через **6 слоёв принципов** + **3 ортогональных среза**.

### 6 слоёв

| # | Слой | Что регулирует | SSOT |
|---|---|---|---|
| **0** | Размещение и структура | Куда положить документ; что считать единицей знания; как именовать | context-distribution-rules.md, knowledge-hub-design-principles.md, document-lifecycle.md |
| **1** | Универсальные принципы работы со знаниями | Как думать при работе с любым знанием (метакогниция) | operating-principles.md — 11 принципов |
| **2** | Принципы AI-агентов и Claude Code skills | Как проектировать LLM-агенты и skills | agent-design-principles.md — §A 12 + §B 8 |
| **3** | Pipeline-инфраструктура | Как многошаговый pipeline накапливает память о сбоях и обучается | pipeline-health-tracking-pattern.md — 14 принципов + Finale Contract |
| **4** | Enforcement | Как правила Слоёв 0-3 принуждаются автоматически | `.claude/hooks/*.py` + CLAUDE.md правила 12, 16, 17 |
| **5** | Multi-tenancy и IP-разделение | Как разделяется «своё / клиентское / продуктовое / закрытое ядро» | CLAUDE.md (kernel discipline, sandbox lifecycle) |

### Открытый слот для Слоя 7+

Архитектура не закрыта. Если в будущем появится ≥3 принципа, не помещающиеся в существующие 6 — добавляется Слой 7 через одну из альтернативных осей (см. § Метод классификации). Текущие preliminary-кандидаты: операционные конвенции (бэклоги, OKR-формы), process workflows (git-commits, разбор ошибок), interop/UX (multi-agent ecosystem, тон агента).

### 3 ортогональных среза

Не слои, но измерения, пронизывающие все слои:

- **Типы знаний** — про **природу знания** (модели / онтология / знание / правила интерпретации / выходы)
- **Reusable Core vs Operations** — про **частоту изменений и переиспользование**
- **Design-time vs Runtime** — про **момент применения** (kernel discipline)

## Карта принципов

Все принципы с 1-строчными описаниями и ссылками на SSOT. По каждому слою — пронумерованный список.

### Слой 0: Размещение и структура

| # | Принцип | SSOT |
|---|---|---|
| 0.1 | SSOT определяется по типу данных, не по системе | kh-design-principles §1.2 |
| 0.2 | Channel-First Transparency — канал ≠ SSOT | kh-design-principles §1.1 |
| 0.3 | Reusable Core vs Operations — разделение по 5 критериям | kh-design-principles §1.3 |
| 0.4 | Role Coverage Matrix — каждая роль видит всё через свой канал | kh-design-principles §2 |
| 0.5 | Composite → Decomposed lifecycle документов | kh-design-principles §5 |
| 0.6 | Card Recon — автономность H2-секций (Назначение + H2-spine) | kh-design-principles §6 |
| 0.7 | Decision Tree размещения — 6 шагов от «о чём документ» до layer | context-distribution-rules §2 |
| 0.8 | Genesis / Knowledge / Principles / Skill — 4-типовая онтология | rules.md § Классификация знаний |
| 0.9 | Ссылайся, не повторяй (Reference, Don't Repeat) | rules.md |
| 0.10 | 11 типов документов с разными lifecycle | document-lifecycle.md |
| 0.11 | filename = поисковый запрос (kebab-case, английский, без транслита) | context-distribution-rules §6 |
| 0.12 | Папка ≥10 .md → разбивка на подпапки по темам | context-distribution-rules §5 |

### Слой 1: Универсальные принципы работы со знаниями

| # | Принцип | SSOT |
|---|---|---|
| 1.1 | Проверяй содержимое, не обёртку | operating-principles §1 |
| 1.2 | Ошибка = сигнал к предотвращению | operating-principles §2 |
| 1.3 | Signal Density — слова = код, каждое слово управляющий сигнал | operating-principles §3 |
| 1.4 | Бритва Оккама (Structural Parsimony) — не множь сущности без необходимости | operating-principles §4 |
| 1.5 | Самоприменимость — утверждение о структуре должно удовлетворять этой структуре | operating-principles §5 |
| 1.6 | Implementation contract incremental — документация по стадии зрелости | operating-principles §6 |
| 1.7 | High-velocity implementation — 7-факторный чек-лист перед ≥5ч impl | operating-principles §7 |
| 1.8 | Factual integrity в operational артефактах — no hallucinated proper nouns, source/SSOT/morphological checks; рёбра графа требуют evidence, не только узлы | operating-principles §8 |
| 1.9 | Имя = сущность, не контекст рождения — двухходовый тест перед Write идентификатора | operating-principles §9 |
| 1.10 | Точность распределяется между входом и выходом — gateway-вопрос «где оракул» перед проектированием pipeline | operating-principles §10 |
| 1.11 | Изменение завершено только с определённой поддерживаемостью — воронка 0a/0b + три критерия (enforcement / каденция / сигнал) | operating-principles §11 |

### Слой 2: AI-агенты и Claude Code skills

**§A. Продуктовые агенты (12 принципов):**

| # | Принцип | SSOT |
|---|---|---|
| 2.A1 | Специализация — один агент = одна задача | agent-design §A1 |
| 2.A2 | Три слоя контекста (Universal / Client / Operations) + cache-aware ordering | agent-design §A2 |
| 2.A3 | Онтология в Universal Core, факты в Operations | agent-design §A3 |
| 2.A4 | Явные handoff — структурированный, не молчаливый | agent-design §A4 |
| 2.A5 | Semver + миграция обязательна при breaking change | agent-design §A5 |
| 2.A6 | Signal Density для промптов (производный от 1.3) | agent-design §A6 |
| 2.A7 | 8 обязательных секций промпта в фиксированном порядке | agent-design §A7 |
| 2.A8 | Агент не оценивает свой output (отдельный evaluator) | agent-design §A8 |
| 2.A9 | MVP Blueprint + 7 классов риска + permission matrix | agent-design §A9 |
| 2.A10 | 10 категорий eval-кейсов (минимум 1+2+9 + одно из {3,4,5} перед v1.0.0) | agent-design §A10 |
| 2.A11 | Детерминированное ядро, LLM на краях — вычислимое в код, не в промпт; reuse; гейт против premature scripting (Class A token-economy) | agent-design §A11 |
| 2.A12 | Собственный контур (dogfood) → универсальный контракт + адаптируемая конвенция; универсализация при 2-м реальном потребителе (demand-driven); потребитель включает агентов/скиллы/сборку | agent-design §A12 |

**§B. Claude Code skills (8 принципов):**

| # | Принцип | SSOT |
|---|---|---|
| 2.B1 | Output discipline — только сигнал отклонения, не подтверждение нормы | agent-design §B1 |
| 2.B2 | Лестница мест 1-6 перед предложением memory | agent-design §B2 |
| 2.B3 | Spec changes должны быть закоммичены до запуска pipeline | agent-design §B3 |
| 2.B4 | Validator-сообщения директивны, не описательны | agent-design §B4 |
| 2.B5 | Sensitivity-проверка при создании документов про закрытое ядро — до `status: accepted` | agent-design §B5 |
| 2.B6 | Memory facts injection + prompt precedence для subagent'ов | agent-design §B6 |
| 2.B7 | Именование skills — платформенный kebab-case (no underscore, ≤64) + namespace через папку `{client}/`→`{client}:name`, не префикс в имени | agent-design §B7 |
| 2.B8 | Порог создания skill — ≥3 фактических проведения / batch-потребность / накопленные потери; дефолт — линейный процесс, не pipeline с gate-файлами | agent-design §B8 |

### Слой 3: Pipeline-инфраструктура

| # | Принцип | SSOT |
|---|---|---|
| 3.1 | Immutable gates + mutable projection (CQRS) | pipeline-health §1 |
| 3.2 | Stable issue_key, не hash по тексту | pipeline-health §2 |
| 3.3 | Severity по природе, priority производна | pipeline-health §3 |
| 3.4 | Status = last_outcome + root_cause_resolved (раздельно) | pipeline-health §4 |
| 3.5 | Old-issue escalation (SLO старения) | pipeline-health §5 |
| 3.6 | Periodic root-cause pattern-pass | pipeline-health §6 |
| 3.7 | Cross-instance generalization (общие failure-classes при ≥2 pipeline) | pipeline-health §7 |
| 3.8 | Запрет смешивания infra-issues и бизнес-выводов | pipeline-health §8 |
| 3.9 | Issue→Problem promotion (4 триггера + two-pass discipline) | pipeline-health §9 |
| 3.10 | Notification через существующий канал (трекер, не email/Slack) | pipeline-health §10 |
| 3.11 | Issue lifecycle telemetry (mttp / fix_efficacy / creation_rate) | pipeline-health §11 |
| 3.12 | Manual dismissal с типизированной причиной | pipeline-health §12 |
| 3.13 | Runner-side contract enforcement — hard validation + retry contract для LLM-subagent outputs | pipeline-health §13 |
| 3.14 | Derived projection — счётчики/сводки вычисляются из строк-источника, не ведутся руками (recompute-скрипт, `--check`) | pipeline-health §14 |
| — | Pipeline Finale Contract (aggregate → promotion-check → pattern-pass) — контракт финального шага, не принцип | pipeline-health § Finale Contract |

### Слой 4: Enforcement

| # | Принцип | SSOT |
|---|---|---|
| 4.1 | Hook `post_write_readme_sync.py` — auto-update README в папках | `.claude/hooks/post_write_readme_sync.py` |
| 4.2 | Hook `post_write_doc_standard_check.py` — П1-П9 + Я1-Я9 при accepted/ready | `.claude/hooks/post_write_doc_standard_check.py` |
| 4.3 | Hook `post_write_gate_commit_check.py` — Pipeline Finale Contract enforcement | `.claude/hooks/post_write_gate_commit_check.py` |
| 4.4 | Hook `post_write_memory_gate.py` — лестница мест enforcement | `.claude/hooks/post_write_memory_gate.py` |
| 4.5 | Двухпроходный режим стандарта (П1-П9 + Я1-Я9) при promotion | правило «стандарт оформления документов», document-writing-standards.md |
| 4.6 | /spec-review (двухслойное: 2 линзы + диалектический синтез; SSOT состава — сам skill) перед status: ready_for_implementation ≥20ч | правило «архитектурное ревью артефакта», `.claude/commands/spec-review.md` |
| 4.7 | Hook `post_write_principles_map_sync.py` — карта принципов ↔ SSOT слоёв 1–3 (правка SSOT или карты → сверка реестров, block при расхождении) | `.claude/hooks/post_write_principles_map_sync.py` |
| 4.8 | Полнота охвата хуков — реестр НЕ дублируется в карте: территория = `.claude/hooks/` + `.claude/settings.json` (строки 4.1–4.4/4.7 — исторические якоря принципов, не перечень) | `.claude/settings.json` |

### Слой 5: Multi-tenancy и IP-разделение

| # | Принцип | SSOT |
|---|---|---|
| 5.1 | Kernel discipline — закрытый IP-слой никогда не идёт в LLM-контекст; наружу — только дистилляты (views) | CLAUDE.md (kernel discipline) |
| 5.2 | Sandbox lifecycle — promotion `git mv` при accept'е ADR | CLAUDE.md (sandbox lifecycle) |
| 5.3 | SSOT-декларация rendered-трекеров — markdown trackers = rendered, не SSOT | CLAUDE.md |
| 5.4 | Параллельные корневые области монорепо (продукт / методология / операционка / клиентские экземпляры) — разделение по частоте изменений и tenant-принадлежности | context-distribution-rules §1 |
| 5.5 | Pattern Library расширяется только через Trinity-checklist | distillation-checklist.md |

## Метод классификации

Слои выведены **тестом дефицита**: для каждого принципа задан вопрос «какое поведение становится невозможным без этого правила». Принципы с одинаковым ответом → один слой.

**Применённый алгоритм:**
1. Inventory принципов из source-документов (~70 единиц)
2. Для каждого принципа — тест дефицита
3. Группировка по природе регулируемого
4. Тест на ортогональность (где принцип ложится в два слоя — по корню; универсальный — внизу, домены ссылаются)
5. Тест на полноту через правила CLAUDE.md (при добавлении новых правил тест повторяется)

**Альтернативные группировки (не применённые, оставлены как open question):**
- **По аудитории** (автор / агент / команда / клиент) — даёт другую разбивку, ближе к role coverage matrix
- **По времени применения** (design-time / runtime / observation) — даёт меньше слоёв, но грубее
- **По уровню абстракции** (operational / tactical / strategic) — слишком субъективный критерий

**Пересмотр оси:** если существующая 6-слойная разбивка перестаёт давать управляющий сигнал (принципы регулярно «висят вне слоёв» или попадают в несколько одновременно) — пересмотр через одну из альтернативных осей или добавление новой оси. Триггер: ≥3 принципа не помещаются в один из 6 слоёв.

**Самоприменимость (по принципу 1.5):** этот документ — утверждение о структуре. Его собственная структура соответствует тому, что описывает: имеет один SSOT-статус (этот файл), не дублирует принципы (только ссылается), сам пройден тестом дефицита (без него — каждое добавление нового принципа = политическое обсуждение слоя).

## Что НЕ покрывает архитектура

6 слоёв покрывают только **принципы** — метаправила мышления и дизайна. Не покрывают (живёт в других местах):

- **Операционные конвенции** (форматы бэклогов, Active Tasks lifecycle, OKR mapping, выжимки встреч) → операционные правила overlay экземпляра, workflows/active-tasks.md
- **Process workflows** (git commits, разбор ошибок, проактивные подсказки skills, close-session) → CLAUDE.md правила 5, 10, 11, workflows/
- **Онтология терминов** (что такое OKR, Skill, KR, барьер) → glossary
- **Capabilities map** (какой skill запустить для задачи) → skill-capabilities-map.md
- **Memory персональная** (профиль пользователя, feedback) → приватная memory харнеса

Граница: **принцип регулирует «как думать»**, **операционная конвенция — «какой формат»**, **workflow — «в каком порядке шаги»**. Смешение разрушит ось «по природе того, что регулирует».

## Decision Tree «какой принцип применить»

Если ты делаешь X — обязательно применить принципы Y. Триггеры по убыванию частоты:

| Триггер задачи | Обязательные принципы | Опциональные |
|---|---|---|
| Создаю новый .md документ | 0.1, 0.7, 0.9, 0.11, 1.1, 1.4, 1.9 | 0.10 (если concept/decision/scope/spec) |
| Размещаю файл (выбираю папку) | 0.7, 0.3, 5.4 | 0.12 (если папка переполняется) |
| Создаю новый продуктовый AI-агент | 2.A1, 2.A2, 2.A6, 2.A8, 2.A9, 2.A10, 2.A11, 2.A12, 1.3, 1.4 | 2.A4 (если handoff), 2.A5 (всегда) |
| Создаю новый Claude Code skill | 2.B8 (порог — сначала), 2.B1, 2.B2, 2.B4, 2.B7, 1.4 | 2.B3 (если skill читает specs), 2.B5 (если создаёт документы про закрытое ядро), 2.B6 (если делает Agent tool call) |
| Создаю идентификатор в universal/shared коде | 1.9 | — |
| Проектирую многошаговый pipeline | 3.1, 3.2, 3.4, 3.9, 3.14, Finale Contract, 1.2, 1.10 | 3.7 (при ≥2 pipeline), 3.13 (если LLM-субагенты со structured output) |
| Спавню субагента (Agent tool) | 2.B6 | — |
| Пишу промпт LLM (любого) | 1.3, 2.A2, 2.A3, 5.1 | 2.A6 |
| Принимаю решение о структуре | 1.4, 1.5, 0.5, 0.6 | 1.6 (если документация) |
| Обнаружил ошибку / косяк | 1.2, 3.6 (если pipeline) | 3.9 (если повторился) |
| Меняю существующий принцип / правило | 0.9, 1.5, 1.11, 2.A5 | — |
| Завершаю структурное изменение (новый тип файла / конвенция / процесс) | 1.11 | — |
| Промоушен `_sandbox/` → production | 4.5, 5.2, 4.6 (если spec ≥20ч) | — |
| Работаю с закрытым ядром | 5.1 (обязательно) | — |
| Создаю клиентский артефакт | 5.4, 0.2, 0.4 | 5.1, 2.B5 (если задействовано ядро) |
| Запускаю pipeline (любой) | 2.B3, Finale Contract | — |

**Как читать:** «обязательные» = без применения этого принципа результат неверен или опасен. «Опциональные» = применяются по контексту (зависит от типа задачи / стадии зрелости).

## Связь с другими концептами / документами

- CLAUDE.md — gateway-слой; правила-gateway'и отсылают к SSOT в этом индексе
- skill-capabilities-map.md — Decision Tree «какой skill запустить» (аналог этого файла для skills, не принципов)
- context-distribution-rules.md — Decision Tree «куда положить документ» (Слой 0)
- glossary — онтология терминов (что такое Skill, OKR), не принципы
