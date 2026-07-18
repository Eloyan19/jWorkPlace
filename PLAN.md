# PLAN.md — поэтапный план разработки jWorkPlace

> 🗣 Язык — русский; имена кода/команды — латиницей.
> Источник инвариантов и решений — **`CLAUDE.md`** (grounding, безопасность токенов, per-project
> изоляция, два слоя агентов A/B, MVP-скоуп). Этот план им **подчинён**: если план и `CLAUDE.md`
> расходятся — прав `CLAUDE.md`, план правим.

## Цель и принцип

Довести jWorkPlace от «фундамента» (кода нет) до рабочего MVP: индексация репо → grounded-чат по
коду → предложение правок → PR → рой агентов (Слой B).

**Принцип «deploy-first».** Мы не сидим в локальной разработке. **Этап 0** поднимает минимальный
walking skeleton за nginx+systemd+certbot — сразу живой по `https://jwork.jorchik.com`. **Каждый
следующий этап заканчивается деплоем инкремента** на прод. Этап не закрыт, пока не пройдены обе
проверки: **(а) авто** (тесты/eval/`curl`) и **(б) ручная** — конкретное действие пользователя на
`jwork.jorchik.com` с ожидаемым результатом.

**Мы на самом проде.** Claude Code запущен на VPS `jorchik.com` (IPv4 `202.71.13.114`) — том же хосте,
что обслуживает `jorchik.com`/`llm.jorchik.com`. Деплой — **локальные операции здесь** (systemd/nginx/
certbot напрямую), не по SSH.

**Дробление ради частой выкатки.** Крупные Этапы 1 и 2 разбиты на под-релизы (1a/1b, 2a/2b) — каждый
деплоится и проверяется по ссылке отдельно, чтобы прогресс был виден как можно раньше.

**Deploy-инвариант (паттерн соседей, `webchat_with_local_llm/deploy/`):** backend — uvicorn на
`127.0.0.1:8200` под `jworkplace.service` (systemd, `Wants/After ollama.service`); фронт — прод-сборка
Vite в `/var/www/jworkplace`, раздаётся nginx; отдельный `server{}`-блок под `server_name
jwork.jorchik.com`, TLS через `certbot --nginx`; секреты (`DEEPSEEK_API_KEY`, GitHub PAT) — в
`backend/.env` (в git не попадает). Данные (клоны, индексы, SQLite) — в `$JWP_DATA_DIR` **вне**
git-дерева (напр. `/var/lib/jworkplace`).

## Обзор этапов

| Этап | Цель | Главный результат по `https://jwork.jorchik.com` |
|---|---|---|
| **0 ✅** | Walking skeleton + деплой-пайплайн | **Сделано:** страница + `/api/health`→`ok` живут по HTTPS на `jwork.jorchik.com` |
| **1 ✅** | Индексация репо (RAG-хранилище) | **Сделано:** вставил ссылку → клон+скан+tree-sitter+FAISS → `ready`, список, переключение; токен-гейт; recall@k baseline |
| **2** | Grounded-чат по коду | **2a ✅** hybrid search: спросил → фрагменты кода `file::symbol::строки` + abstain. **2b** — DeepSeek grounded-генерация |
| **3** | Правки + Pull Request | Попросил правку → предложенный diff → подтвердил → создан реальный PR (ссылка) |
| **4** | Рой агентов (Слой B) | Задача-изменение → рой (planner/critic/coder/reviewer/judge) → подтверждение → PR |

Сквозной **Eval** заводим с Этапа 1 (см. раздел «Eval»). Ответственные — агенты Слоя A
(`architect`, `llm-engineer`, `rag-indexing-engineer`, `backend-developer`, `frontend-developer`,
`code-reviewer`, `ui-ux-specialist`, `security-auditor`, `qa-engineer`, `debug-specialist`,
`performance-engineer`), проставлены у задач.

---

## Этап 0 — Walking skeleton + деплой-пайплайн

> ✅ **ВЫПОЛНЕНО 2026-07-17** (коммит `bf1ce87`). Живой `https://jwork.jorchik.com/api/health`→`ok`,
> TLS (certbot, авто-renew), HTTP→HTTPS 301, `jworkplace.service` active+enabled. Прошли ARCHITECT +
> backend/frontend-developer + SECURITY_AUDITOR (9/9 OK) + `/code-review`. Деплой-пайплайн отлажен и
> переиспользуется дальше (см. `deploy/README.md` → «Redeploy»).

**Цель.** Получить живой публичный URL с минимумом кода. Отладить весь путь выкатки (systemd +
nginx + certbot) **до** того, как появится сложная логика. Дальше каждый этап переиспользует этот
пайплайн.

**Прод-предпосылки — проверено 2026-07-17 (всё зелёное):** мы на самом VPS; nginx `active`, `certbot`
установлен (уже держит сертификаты `jorchik.com`, `llm.jorchik.com`); порт `:8200` свободен; Ollama на
`:11434` с `nomic-embed-text`; **`jwork.jorchik.com` уже резолвится** на `202.71.13.114` (wildcard) —
отдельная DNS-запись не нужна. Готовый шаблон nginx-поддомена — `/etc/nginx/sites-available/llm.jorchik.com`.

**Задачи.**
- `architect` — зафиксировать раскладку монорепо: `backend/` (FastAPI), `frontend/` (Vite/React/TS),
  `deploy/` (юнит + nginx + README), `eval/`, `.env.example`; определить `$JWP_DATA_DIR` и границы.
- `backend-developer` — FastAPI-приложение с единственным эндпоинтом:
  `GET /api/health → {"status":"ok","version":<git-sha>}`; конфиг из env (pydantic-settings);
  `.venv` + `requirements.txt`; заготовка `LlmService`-интерфейса (пустой адаптер, без вызовов) —
  чтобы абстракция существовала с первого дня.
- `frontend-developer` — минимальный экран: заголовок + индикатор, дёргающий `/api/health`
  (зелёный/красный). Прод-сборка `npm run build`.
- `backend-developer` (+ ссылка на `webchat_with_local_llm/deploy/`) — `deploy/jworkplace.service`
  (uvicorn `127.0.0.1:8200`, `EnvironmentFile=backend/.env`, `Wants/After ollama.service`),
  `deploy/nginx-jworkplace.conf` (`server_name jwork.jorchik.com`: `/` → статика `/var/www/jworkplace`,
  `/api/*` → `127.0.0.1:8200`), `deploy/README.md` (DNS A-запись → systemd → build → nginx → certbot).
- `security-auditor` — проверить, что `.env` в `.gitignore`, `$JWP_DATA_DIR` вне git, nginx не
  отдаёт `.env`/data; `.env.example` без реальных значений.

**Deliverables.** Пустой, но правильный скелет репо; systemd-юнит; nginx-конфиг; README деплоя;
CI-заготовка тестов (`pytest`, `vitest`) — по одному «smoke»-тесту на сторону.

**Деплой.** Полная первичная установка на VPS: DNS A-запись `jwork.jorchik.com`, копия юнита в
systemd, сборка и выкладка фронта в `/var/www/jworkplace`, nginx server-блок, `certbot --nginx -d
jwork.jorchik.com`.

**Проверка: что сделано и работает.**
- Авто: `curl -s https://jwork.jorchik.com/api/health` → `{"status":"ok",...}` (HTTP 200, валидный
  TLS); `systemctl is-active jworkplace.service` → `active`; `pytest`/`vitest` smoke зелёные.
- Ручная: пользователь открывает `https://jwork.jorchik.com` — видит страницу jWorkPlace с зелёным
  индикатором «backend online» (замок TLS в адресной строке).

**Definition of Done.** URL живой по HTTPS, health зелёный, юнит поднимается после `reboot`,
redeploy-инструкция в `deploy/README.md` воспроизводима. Ни одного секрета в git.

---

## Этап 1 — Индексация репо

> ✅ **ВЫПОЛНЕНО 2026-07-18.** 1a+1b одним инкрементом. `backend/app/indexing/` (validation →
> clone → scan+gitleaks → chunker tree-sitter → embeddings nomic+кэш → faiss_store) + `db.py`
> (SQLite) + `api/projects.py` (фон через `pipeline` state-machine). Фронт `ProjectsPanel`
> (подключение/список/переключение, токен из `?token=`→localStorage). Токен-гейт nginx на `/api/*`
> (только `Authorization: Bearer`, health открыт) + rate-limit зоны. gitleaks fail-closed
> (секреты гейтятся до эмбеддинга). Прошли эксперты (rag-indexing + security-auditor ×2) +
> `/code-review` high (4 находки исправлены: reindex-loop, GC задачи, indexed-флаг, путь-гейт).
> Живой прод: клон+индекс `octocat/Hello-World`→`ready`; recall@k baseline **файл 1.00 / символ 0.80**
> (`eval/recall_at_k.py`, markupsafe). 30 pytest + 8 vitest зелёные.

**Цель.** Пользователь вставляет ссылку на публичный GitHub-репо → сервис клонирует, безопасно
сканирует, code-aware-индексирует и сохраняет per-project индекс. Проекты копятся, между ними можно
переключаться. Пока **без чата** — только «подключить и проиндексировать».

**Под-релизы (деплоим по отдельности):**
- **1a — подключение репо:** валидация ссылки + клон (shallow, `core.hooksPath=/dev/null`) + скан
  секретов + SQLite `projects` + UI «вставь ссылку»/список со статусом. Деплой: вставил ссылку →
  проект склонирован и виден в списке (статус `cloned`), ещё без индекса.
- **1b — индексация и переключение:** tree-sitter чанкинг + nomic-эмбеддинги + FAISS per-project +
  статус `ready` + переключение активного проекта + инкрементальный reindex + eval recall@k baseline.
  Деплой: проект доходит до `ready`, переключается между двумя.

**Задачи.**
- `rag-indexing-engineer` (эксперт-дизайн ПЕРВЫМ) — конвейер индексации и схема чанка
  `{project_id, file, lang, symbol, symbol_kind, start_line, end_line, text, blob_sha}`; выбор
  tree-sitter-грамматик по расширению + fallback по строкам с overlap; лимиты репо/файлов и
  фильтр бинарных/vendored до эмбеддинга (потолок 3.8 ГБ RAM); стратегия инкрементальности по
  `blob_sha` и dedup-кэш эмбеддингов.
- `security-auditor` (эксперт ПЕРВЫМ, параллельно) — **скан секретов ДО индексации**
  (gitleaks-подобно): чужой `.env`/ключи не должны попасть в эмбеддинги/индекс/логи; клон с
  `-c core.hooksPath=/dev/null` (git-хуки чужого репо не исполняем); валидация входной GitHub-ссылки
  (SSRF/локальные пути/размер).
- `security-auditor` — **защита публичного URL (микро-решение к 1a):** сервис single-user и открыт по
  ссылке → решить, закрывать ли `jwork.jorchik.com` простым Bearer-токеном в nginx (паттерн
  `llm.jorchik.com`), чтобы чужие не жгли ключ DeepSeek и не запускали индексацию произвольных репо.
- `backend-developer` — модуль клонирования:
  `git clone --depth 1 --filter=blob:none -c core.hooksPath=/dev/null <url>
  $JWP_DATA_DIR/repos/<project-id>/`; таймаут; отсев больших файлов.
- `backend-developer` — SQLite-хранилище: таблицы `projects` (id, url, name, status, indexed_at,
  head_sha), `files`/`chunks` (метаданные чанков, `blob_sha`); слой доступа + миграции.
- `rag-indexing-engineer` — индексатор: обход дерева → чанкинг → эмбеддинги через Ollama
  `nomic-embed :11434` → FAISS per-project в `$JWP_DATA_DIR/indexes/<project-id>/`; кэш эмбеддингов
  по `blob_sha`.
- `backend-developer` — эндпоинты: `POST /api/projects {url}` (запуск индексации, вернуть
  `project_id`), `GET /api/projects` (список + статус), `GET /api/projects/{id}` (детали/прогресс),
  `POST /api/projects/{id}/reindex` (инкрементально по `git pull` → изменённые файлы). Индексация —
  фоновая задача со статусами (`cloning/scanning/indexing/ready/error`).
- `frontend-developer` + `ui-ux-specialist` — UI: поле «вставь ссылку на репо», список проектов с
  статусом/прогрессом индексации, переключатель активного проекта (активный `project_id` в
  состоянии + localStorage).
- `performance-engineer` (по факту) — контроль RAM/латентности индексации на среднем репо.
- `qa-engineer` — pytest на чанкинг (границы символов), инкрементальность (изменил 1 файл →
  переиндексирован только он), фильтр секретов; Vitest на список/переключение.

**Deliverables.** Рабочий индексатор; SQLite-схема; per-project FAISS; UI подключения и
переключения; первый `eval/`-набор (golden «вопрос → файл/символ») и harness recall@k.

**Деплой.** Инкремент на прод: `$JWP_DATA_DIR` на VPS с правами сервиса; env для лимитов индексации;
redeploy backend+frontend. Проверить доступ юнита к Ollama `:11434`.

**Проверка: что сделано и работает.**
- Авто: `curl -XPOST .../api/projects -d '{"url":"<небольшой публичный репо>"}'` → `project_id`;
  поллинг `GET /api/projects/{id}` доходит до `status:"ready"`; на диске появились
  `indexes/<id>/`; `pytest` индексатора зелёный; `eval` recall@k печатает базовую цифру; повторный
  reindex после правки одного файла трогает только его (лог/метрика).
- Ручная: на `https://jwork.jorchik.com` пользователь вставляет ссылку на репо → видит прогресс →
  проект становится «ready» в списке → переключается между двумя проиндексированными проектами
  (активный подсвечен).

**Definition of Done.** Два разных репо проиндексированы и переключаются; секреты отфильтрованы (в
индексе их нет — проверка аудитором); инкрементальный reindex работает; recall@k зафиксирован как
baseline; RAM в пределах бюджета.

---

## Этап 2 — Grounded-чат по коду

**Цель.** По активному проекту пользователь ведёт чат; ответы **обоснованы извлечёнными чанками
кода**, с источниками и жёстким гейтом «не знаю». Никакого отката на общие знания модели.

**Под-релизы (деплоим по отдельности):**
- **2a ✅ ВЫПОЛНЕНО 2026-07-18 (коммит `60b4415`)** — retrieval без LLM: hybrid search (BM25 через
  FTS5 + dense/RRF k=60) + `POST /api/search` + UI `SearchPanel` (фрагменты с `file::symbol::строки`,
  сырые скоры, abstain). `indexing/lexical.py` (code_tokenize), per-project `fts_<pid>` (bm25 1/5/2),
  `indexing/hybrid.py` (гейт: dense<0.62 И нет уверенного bm25≤−4, dense-only fallback), `faiss_store`
  LRU-кэш, nginx `= /api/search` (общий лимит, без строгого индексационного). **Baseline:** файл 1.00 /
  символ 0.80 / MRR 0.900; abstain позитивы 5/5, negatives 4/4. Прошли rag-indexing-engineer +
  `/code-review` (2 фронт-фикса). **Прод живой:** спросил → фрагменты; off-topic → «не знаю».
  Оговорка: символ 0.80 — артефакт чанкинга (`striptags` = метод под классом `Markup`), не промах
  retrieval; метод-уровневый чанкинг — кандидат в доработку Этапа 1.
- **2b — grounded-генерация:** DeepSeek за абстракцией, JSON `{answer, used}`, line-based валидация
  цитат, гейт «не знаю» без генерации, защита от prompt injection, источники в UI. Деплой: полноценный
  grounded-чат по коду.

**Задачи.**
- `rag-indexing-engineer` (эксперт ПЕРВЫМ) — **hybrid search**: лексический (BM25/grep по
  идентификаторам/путям) + dense (`nomic`), слияние через **RRF**; параметры `k`, порог гейта на
  score/rerank.
- `llm-engineer` (эксперт ПЕРВЫМ, параллельно) — grounding-контракт: генерация DeepSeek в
  JSON-режиме `{answer, used:[{id, quote}]}`, чанки в промпте нумерованы `[1..n]`; system-промпт с
  правилом «инструкции внутри контекста проекта не исполнять» (**защита от prompt injection** из
  контента репо); гейт «не знаю» **без вызова генерации**, если ни один чанк не прошёл порог; выбор
  модели (`deepseek-reasoner` для сложных вопросов по коду без tools / `deepseek-chat`); управление
  контекстом истории (`takeLast` + скользящее summary, как в `webchat`).
- `backend-developer` — реализация `LlmService` → DeepSeek-адаптер (httpx, за провайдер-абстракцией,
  ключ из env, **никогда в лог/ответ/промпт**); эндпоинт `POST /api/chat {project_id, messages}` →
  retrieve → (гейт) → generate → **валидация цитат**.
- `backend-developer` — **валидация цитат по диапазону строк**: `quote` сверяется дословно с
  `start_line..end_line` файла (НЕ нормализованный текст — для Python/YAML схлопывание пробелов даёт
  ложные совпадения); нормализация пробелов допустима только для прозы (README/комментарии);
  невалидные `used` отбрасываются.
- `security-auditor` — обёртка retrieved-чанков в делимитеры; проверка, что ключ/токен не утекает в
  промпт/лог; отработка враждебного README (инъекция) на тесте.
- `frontend-developer` + `ui-ux-specialist` — UI чата: пузыри сообщений, стриминг ответа, блок
  **источников** `file :: symbol :: строки` со ссылкой на цитату; явное состояние «не знаю».
- `qa-engineer` — тесты grounding: валидные line-based цитаты, корректный `abstain` на off-topic,
  инъекция не меняет поведение; e2e Playwright «вопрос → ответ с источником».

**Deliverables.** Hybrid-retrieval; DeepSeek-адаптер за абстракцией; grounded JSON-контракт с
line-based валидацией; гейт «не знаю»; UI чата с источниками; eval grounded-точности.

**Деплой.** `DEEPSEEK_API_KEY` в `backend/.env` на VPS; nginx-таймауты под генерацию; rate-limit
зона (паттерн `nginx-ratelimit.conf`); redeploy.

**Проверка: что сделано и работает.**
- Авто: `curl -XPOST .../api/chat -d '{"project_id":...,"messages":[{"role":"user","content":"что
  делает класс X"}]}'` → JSON с `answer` и непустым валидным `used` (цитаты дословно в диапазоне
  строк); вопрос заведомо не по проекту → предзаданный «не знаю» **без** генерации; eval
  grounded-точности печатает долю валидных цитат + корректность abstain; тест инъекции зелёный.
- Ручная: на `https://jwork.jorchik.com` пользователь выбирает проект, спрашивает «что делает проект»
  и «где вызывается Y» → получает ответ **с кликабельными источниками** (`file::symbol::строки`);
  вопрос не по теме → аккуратное «не знаю, уточните».

**Definition of Done.** Grounded-ответы с валидными line-based цитатами; abstain работает без
генерации; инъекция из репо не пробивает; ключ нигде не светится; grounded-точность зафиксирована.

---

## Этап 3 — Правки + Pull Request

**Цель.** Пользователь просит правку → сервис предлагает патч → **после явного подтверждения**
создаёт реальный Pull Request из feature-ветки. Прямой push в `main` запрещён. Пока **без роя** —
одиночная генерация патча (рой придёт на Этапе 4, переиспользуя этот PR-механизм).

**Задачи.**
- `security-auditor` (эксперт ПЕРВЫМ) — GitHub-доступ: **fine-grained PAT** с минимальным scope
  (`contents` + `pull_requests`), в env, не в git/лог даже частично; политика веток (только
  `jworkplace/<feature>`, запрет push в `main`/чужие ветки); безопасность git-операций.
- `llm-engineer` (эксперт ПЕРВЫМ, параллельно) — контракт генерации патча: формат diff, привязка к
  grounded-контексту, инструкция «менять только обоснованное»; критерии REVIEWER-протокола к Этапу 4.
- `backend-developer` — PR-флоу: рабочая ветка `jworkplace/<feature>` → применение патча (`git
  apply` / запись файлов) → **`git apply --check` обязателен** (не применяется чисто → не в PR) →
  `commit` → `push` → **PR через `gh`/GitHub API** → вернуть ссылку.
- `backend-developer` — эндпоинты: `POST /api/projects/{id}/edit {instruction}` → сгенерированный
  предпросмотр diff (ничего не пушим); `POST /api/projects/{id}/pr {confirm}` → создать PR **только
  при явном confirm** (**human-in-the-loop**, аналог `AwaitingInput`).
- `frontend-developer` + `ui-ux-specialist` — UI: показ предложенного diff (подсветка),
  кнопка «Подтвердить и открыть PR», статус/ссылка на созданный PR; явный шаг подтверждения.
- `security-auditor` — повторный аудит: токен вне рабочей директории клона и вне логов; PR не
  содержит секретов.
- `qa-engineer` — тесты: `git apply --check` отсекает битый патч; без confirm PR **не** создаётся;
  eval PR-качества (доля патчей, проходящих `--check`).

**Deliverables.** PR-флоу на `gh`; предпросмотр diff в UI; human-in-the-loop подтверждение;
fine-grained PAT в env; eval PR-качества.

**Деплой.** GitHub PAT в `backend/.env` на VPS; `gh` доступен юниту; redeploy. Тест на
специально созданном приватном тестовом репо (не на чужом проде).

**Проверка: что сделано и работает.**
- Авто: `POST /api/.../edit` → валидный diff, проходящий `git apply --check`; `POST /api/.../pr`
  без confirm → отказ; с confirm → реальный PR (проверить ссылку через GitHub API); лог-скан: PAT
  нигде не печатается; eval PR-качества печатает долю валидных патчей.
- Ручная: на `https://jwork.jorchik.com` пользователь просит правку в своём тестовом репо → видит
  предложенный diff → жмёт «Подтвердить и открыть PR» → получает **кликабельную ссылку на реальный
  PR** в GitHub; без подтверждения ничего не уходит.

**Definition of Done.** PR создаётся только после подтверждения и только из feature-ветки; битый
патч отсекается `--check`; PAT нигде не светится; PR-качество зафиксировано как baseline.

---

## Этап 4 — Рой агентов (Слой B)

**Цель.** Для задач-изменений jWorkPlace поднимает **рой runtime-агентов** на DeepSeek
function-calling (пишем с нуля): анализ → план+критика → патч → ревью → синтез → PR. Простые вопросы
по-прежнему идут grounded-ответом без роя.

> Схема ролей/состояний из `CLAUDE.md` (`## Слой B`) — **стартовая гипотеза**, дорабатывается с
> `llm-engineer`, не копируется из Android-референса.

**Задачи.**
- `llm-engineer` (эксперт ПЕРВЫМ, ведущий этап) — дизайн tool-loop на **`deepseek-chat`**
  (`deepseek-reasoner`/thinking **несовместим с tools**); tool-схемы `search_code(query,k)` /
  `read_file(path,range)` / `list_files(glob)` / `propose_patch(path,diff)` / `open_pr(title,body)`;
  промпты ролей ANALYZER/PLANNER/CRITIC/CODER/REVIEWER/JUDGE; **жёсткие лимиты**: ≤8 итераций на
  роль, бюджет токенов/стоимости, replan-петля ≤2; обработка `finish_reason`
  (`tool_calls`/`length`/`stop`); невалидный JSON в `tool_call` → 1 retry, затем FAIL; «параллельные»
  PLANNER+CRITIC = 2 stateless-запроса; `detectComplexity` (простой вопрос vs задача-изменение).
- `backend-developer` — реализация оркестратора и tool-loop; инструменты роя поверх готовых модулей
  (`search_code` → hybrid search Этапа 2; `propose_patch`/`open_pr` → PR-флоу Этапа 3); роли видят
  проект **только через tools** (не весь репо в контекст).
- `backend-developer` — REVIEWER = LLM-judge по diff, первая строка `PASS`/`FAIL` (фиксированный
  протокол); перед PR — `git apply --check` обязателен (не применяется → REVIEWER FAIL); FAIL →
  replan-петля (≤2).
- `security-auditor` — рой не меняет целей от текста репо (инъекция); патчи не к `main`; `open_pr`
  только после подтверждения пользователя; бюджет/лимиты защищают от runaway-стоимости.
- `frontend-developer` + `ui-ux-specialist` — UI прогресса роя: этапы (analyzer/planner/critic/
  coder/reviewer/judge), точки подтверждения (план, PR), итоговый PR.
- `performance-engineer` — латентность/стоимость задачи роя; контроль числа вызовов DeepSeek.
- `qa-engineer` — тесты: лимит итераций срабатывает; невалидный tool-call → retry→FAIL без
  бесконечного цикла; REVIEWER FAIL уводит в replan; e2e «задача → рой → подтверждение → PR».

**Deliverables.** Tool-loop оркестратор с нуля; 6 ролей; 5 инструментов; лимиты и обработка
`finish_reason`; UI прогресса роя с точками подтверждения; eval «доля задач, дошедших до валидного
PR».

**Деплой.** Бюджеты/лимиты в env; redeploy; прогон роя на тестовом приватном репо.

**Проверка: что сделано и работает.**
- Авто: e2e — задача-изменение проходит analyzer→planner/critic→coder→reviewer→judge; REVIEWER
  отдаёт `PASS`/`FAIL` первой строкой; при FAIL — replan (≤2), затем стоп; лимит ≤8 итераций
  соблюдён; патч проходит `git apply --check`; без confirm PR не создаётся; eval печатает долю
  задач до валидного PR.
- Ручная: на `https://jwork.jorchik.com` пользователь даёт содержательную задачу-изменение → видит
  прогресс роя по ролям → подтверждает план → подтверждает PR → получает ссылку на PR. Простой
  вопрос по коду по-прежнему отвечается grounded-ответом **без** роя (detectComplexity).

**Definition of Done.** Рой доводит задачу-изменение до PR под human-in-the-loop, укладываясь в
лимиты итераций/бюджета; невалидные состояния (битый JSON/патч, FAIL-ревью) обрабатываются без
зацикливания; injection не сбивает роли; метрика «до валидного PR» зафиксирована.

---

## Eval (сквозной, с Этапа 1)

Метрики заводим сразу (паттерн `webchat/eval`, `rag/eval`), не «потом». Ведущий — по домену:
`rag-indexing-engineer` (recall), `llm-engineer` (grounded/PR/рой), harness — `qa-engineer`.
- **Retrieval recall@k** (с Этапа 1) — golden «вопрос → файл/символ», доля попаданий нужного чанка
  в top-k.
- **Grounded-точность** (с Этапа 2) — доля ответов с валидными line-based цитатами + корректный
  `abstain` на off-topic.
- **PR-качество** (с Этапа 3) — доля патчей, проходящих `git apply --check`, и доля принятых
  пользователем PR; на Этапе 4 добавляется «доля задач роя, дошедших до валидного PR».

Каждый этап сдвигает свою метрику; baseline фиксируется в момент введения и не должен регрессировать
на следующих этапах.

---

## Риски, зависимости, отложенное

**Зависимости между этапами.** 1 → 2 (чат нужен индекс) → 3 (правки опираются на grounded-контекст)
→ 4 (рой переиспользует hybrid search Этапа 2 и PR-флоу Этапа 3). Внутри этапа эксперт (`architect`/
`llm-engineer`/`rag-indexing-engineer`/`security-auditor`) идёт **до** реализации (Plan Mode-гейт).

**Ключевые риски.**
- **RAM 3.8 ГБ.** Индексация большого репо + FAISS + Ollama могут упереться в память → лимиты
  размера/файлов, shallow-clone `--filter=blob:none`, потоковая индексация; контроль
  `performance-engineer`.
- **Утечка секретов** (главный риск фазы без Docker). `DEEPSEEK_API_KEY`/PAT — только env, вне
  клона, вне логов, вне промпта LLM; чужие секреты отсекаются сканом до индексации; аудит на каждом
  этапе с секретами.
- **Prompt injection из чужого репо.** Контент — недоверенные данные: делимитеры + system-правило +
  роли не меняют целей; тест враждебного README.
- **Стоимость/латентность роя.** Бюджеты токенов, лимит итераций, replan ≤2, обработка
  `finish_reason` — против runaway.
- **`--filter=blob:none` ломает `git blame/log`.** Для read-only индекса ок; понадобится история —
  клонировать иначе (пересмотреть при необходимости).

**Осознанно отложено (не в MVP).**
- ❌ **Фаза 2 — исполнение чужого кода** (сборка/тесты/run). В MVP только чтение + git; исполнения
  нет → Docker пока не вводим.
- ❌ Выбор моделей в UI (абстракция `LlmService` есть с Этапа 0/2, переключатель — фаза 3).
- ❌ Мультипользовательность/шаринг/auth (один пользователь без авторизации) — фаза 4;
  тогда же GitHub App/OAuth вместо PAT.

**Открытый вопрос (спрашивать, не выдумывать).**
- 🔓 **Изоляция для фазы 2** — какой механизм исполнения чужого кода (Docker / nsjail / firejail /
  отдельный системный юзер). Решаем, когда дойдём до исполнения; для MVP не блокирует, т.к. код не
  запускаем.
