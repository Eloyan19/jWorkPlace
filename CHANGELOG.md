# CHANGELOG — jWorkPlace

История этапов и деплоев. Правила/инварианты — в `CLAUDE.md`; поэтапный роадмап — в `PLAN.md`.
Формат: этап → что сделано → тесты / deploy-хеш. Новое — сверху.

---

## База знаний: выжимка о репо + персонализация «что я уже знаю» (НЕ задеплоено)

Новая вкладка **«О проекте»**: после индексации сервис даёт grounded-выжимку репо (overview +
технологии/паттерны/фичи) и ведёт **глобальный (single-user) каталог концептов** — при следующем
репо уже знакомое показывается одной строкой, новое раскрывается подробно. Профиль P1 (Explore-
консилиум architect+llm-engineer+security-auditor → Plan → Build ∥ worktree → Review+SecReview → Fix).

- **Backend `knowledge/`.** `generator.py` (материал детерминированно: `db.project_tree`-скелет +
  манифесты + README + 6 проб `hybrid_search` → **1 вызов `deepseek-chat`** JSON → fail-closed
  валидация с line-based цитатами, как в `chat/grounding`), `matching.py` (каскад дедупа
  slug→эмбеддинг nomic≥0.85→LLM-судья серой зоны 0.75–0.85 батчем), `render.py` (DB→DTO, split
  new/known). `api/knowledge.py` (`GET /projects/{id}/summary` lazy-генерация+`_gen_in_flight`+кэш
  по `head_sha`, `POST /read` авто-пометка known, `GET /concepts`). БД: `project_summaries`,
  глобальный `concepts` (+embedding BLOB для каскада), `project_concepts`; `delete_project`
  чистит связи, глобальный каталог сохраняет (обучение персистентно).
- **Frontend.** `SummaryPanel` (проп `active` — загрузка/поллинг/пометка known только на открытой
  вкладке), `types.ts` (union по `status`), `api.ts` (`getSummary`/`markSummaryRead`/`getConcepts`).
- **Безопасность (SecReview: Ship, must-fix нет).** Тройной барьер секретов (gitleaks → `redact` на
  вход → `redact` на выход LLM перед записью), весь недоверенный контент (репо + межпроектные
  описания в промпте судьи) в нонс-делимитерах + anti-injection, KB строго вне grounded code-Q&A.
- **Тесты: 290 pytest + 62 vitest** (+ live-smoke эндпоинтов). Follow-up (next): eval precision
  дедупа концептов на golden-паре репо.

---

## Учебные задания 1–3 (MVP поверх фундамента, НЕ задеплоено)

- **Зад.1 — `/help` + структура проекта.** `db.project_tree` (дерево files+символы из chunks, БЕЗ
  LLM/обхода клона), `api/structure.py` (`GET /api/projects/{id}/structure`), фронт `StructurePanel`
  + команда `/help` в чате (статический ответ на фронте, В ОБХОД abstain-гейта — мета-вопрос о продукте).
- **Зад.2 — поддержка + РЕАЛЬНЫЙ MCP.** `support/corpus.py` (FAQ-корпус `docs/faq.md` → отдельный
  мини-FAISS `__support__`, БЕЗ per-project пайплайна), `support/qa.py` (grounded по FAQ через
  `chat/grounding`, тикет из MCP — недоверенные данные в делимитерах+`redact`, гейт эскалации,
  retrieve в to_thread), `mcp_servers/tickets_server.py` (FastMCP **stdio** read-only
  `get_ticket`/`list_tickets(user_id обязателен)`/`get_user` над JSON), `support/mcp_client.py`
  (backend как MCP-клиент, allowlist id, semaphore≤2, fail-closed), `api/support.py`
  (`POST /api/support/ask`), фронт вкладка `SupportPanel`. Прошёл security-auditor (критичных нет).
- **Зад.3 — файловый tool-агент = MVP Слоя B (ОДИН агент).** `llm/base.py`+`deepseek.py` (`chat_raw`
  function-calling: tools/tool_choice, возврат `{content,tool_calls,finish_reason}`), `agent/tools.py`
  (schemas + исполнители search_code/read_file/list_files/propose_patch/write_file[только новые .md]/
  finish; guard'ы на каждый вход, `redact` каждого результата), `agent/loop.py` (tool-loop ≤8 итер,
  дедуп, fail-closed), `patcher.new_file_diff`/`assemble_full_diff` (новые файлы в общий diff),
  `api/agent.py` (`POST /api/projects/{id}/agent`: прогон → превью; confirm по `run_id` +
  перепроверка `check_apply` → `open_pr`, паттерн `/pr`), фронт `AgentPanel`. Эксперты llm+security
  ПЕРВЫМИ. **233 pytest + 51 vitest.**

> **Этап 4 (мультиролевой рой Слоя B) — ⏸ ОТЛОЖЕН (решение 2026-07-20).** Одиночный tool-агент
> (Зад.3) — MVP-вход в Слой B; мультиролевой рой (planner/critic/coder/reviewer/judge) надстраивается
> позже над тем же `agent/tools.py`. Дизайн роя сохранён в `PLAN.md` / `CLAUDE.md § Слой B`.

---

## Доработка: управление репо (deploy `c80bc70`)

У `ready`-проекта в UI три действия: Обновить (инкрементальный `/reindex`), Переиндексировать заново
(`POST /{id}/rebuild` → `clone_repo` с нуля), Удалить (`DELETE /{id}` → чистит БД/FTS/FAISS/клоны;
`embed_cache` глобальный не трогаем; `_safe_project_dir` guard + `_pr_in_flight` gate). **200 pytest + 39 vitest.**

## Этап 3c — AI-ревью PR, dogfood (deploy `07b5d6b`, fix `711b898`)

`review/reviewer.py` (`parse_diff` без зависимостей → хунки `D1..Dn`, `build_review_queries`,
`retrieve_context` hybrid k=6 без `should_abstain`, `REVIEW_SYSTEM_PROMPT` анти-инъекция БЕЗ
approve-поля, двойной `redact` вход+выход, `render_markdown` + маркер `<!-- jworkplace-ai-review -->`),
`api/review.py` (`POST /api/projects/{id}/review`, лимит diff→422, fail-closed без сырого diff в логах),
`.github/workflows/ai-review.yml` (`pull_request` НЕ `_target`, `permissions:{}`+job минимум, PR
title/body через env против script-injection, форки fail-closed, обновление ОДНОГО комментария по
маркеру), nginx `~ .../review$` (Bearer, 180s), `eval/review_quality.py`. Эксперты
(llm+architect+security ПЕРВЫМИ) + аудит реализации (чисто) + qa (32 pytest) + `/code-review`.
**177 pytest.** Прод: `/review` за Bearer, 422 на большой diff, `[REDACTED]` на секрет,
«approve»-инъекция не проходит. **Dogfood ПРОШЁЛ:** тестовый PR #1 → Action → AI-комментарий с реальными
багами + ссылками на RAG-контекст проекта.
Урок CI: `gh pr diff/view/comment` без `actions/checkout` → нужен `--repo "$REPO"` (иначе «not a git repository»).

## Этап 3b — реальный PR через per-project fine-grained PAT (deploy `594705a`)

`config.py` (`JWP_SECRET_KEY`+`fernet()` fail-closed, `worktrees_dir`), `db.py` (`github_token_enc`
BLOB + идемпотентная миграция, set/get/clear token), `edit/github.py` (`validate_token` — push+full_name
против репо ИМЕННО проекта; `encrypt/decrypt` Fernet; `open_pr` — writable-клон в `worktrees/<pid>` БЕЗ
blob:none → `git apply` → ветка `jworkplace/<slug>` → bot-commit → push; токен git-у ТОЛЬКО через env
`GIT_CONFIG_*` http.extraHeader, `gh pr create` через `GH_TOKEN` env — никогда argv/URL/reflog;
stderr+PR-body через `redact`), `api/edit.py` (`generate_validated_edit` — единый серверный источник
diff для `/edit` и `/pr`), `api/projects.py` (`PUT/DELETE /token`; `POST /pr` — human-in-the-loop:
confirm + **регенерация+сверка** diff, 409 на расхождение, guard от гонки, fail-closed, `_project_dto`
allowlist+`can_edit`). Фронт: `ProjectsPanel` (поле PAT password, не в localStorage, бейдж can_edit),
`EditPanel` (кнопка «Подтвердить и открыть PR» с `expected_diff`, ссылка на PR, 409 «превью устарело»).
nginx `~ .../(token|pr)$` (Bearer, 300s), systemd `HOME=/root` (для `gh`). Прошли security-auditor
(дизайн + аудит — чисто) + qa (43 pytest) + `/code-review` (3 находки). **145 pytest + 31 vitest.**
Прод: `GET /api/projects` отдаёт `can_edit`; `/pr` без confirm → 400; nginx token|pr за Bearer;
`PUT /token` невалидным токеном → 400. **Ручная проверка реального PR — за пользователем** (нужен PAT + тестовый репо).

## Этап 3a — правка → предпросмотр diff, БЕЗ PR (deploy `cc52e55`)

`chat/grounding.py` (вынесены `safe_repo_path` + `read_span` — общий traversal-guard), `edit/patcher.py`
(`EDIT_SYSTEM_PROMPT` anti-injection, `build_edit_context` окна символов в нонс-делимитерах+redact,
`parse_and_validate_edits` — структурированные JSON-edits вместо unified diff от LLM: `file`∈hits,
запрет `.git/`/`.github/workflows/`, `old_block` УНИКАЛЕН+дословен в redacted-проекции файла, дедуп;
`assemble_diff` — difflib+redact; `check_apply` — `git apply --check` hardening-env), `api/edit.py`
(`POST /api/projects/{id}/edit`: retrieve→гейт should_abstain→генерация temp=0→валидация→сборка
diff→--check; fail-closed `{ok:false}`; instruction max_length=2000), фронт `EditPanel` (подсветка diff
+/−, источники), nginx `~ ^/api/projects/[^/]+/edit$` (Bearer, 180s), `eval/pr_quality.py`. Прошли
security-auditor + llm-engineer (Plan Mode-гейт) + `/code-review`. **102 pytest + 24 vitest.**
Прод: правка → diff с источником `file::symbol::строки`, `git apply --check` прошёл; off-topic → отказ без генерации.

## Этап 2b — grounded-генерация (88 pytest + 18 vitest)

`llm/deepseek.py` (реальный httpx `deepseek-chat`, ключ только в Authorization, retry на
finish_reason=length, `LlmError` без repr/URL), `llm/base.py` (`chat` + response_format/temperature),
`chat/grounding.py` (`build_context` нонс-делимитеры + `SYSTEM_PROMPT` anti-injection,
`parse_and_validate` line-based цитаты по ФАЙЛУ на диске + traversal/project_id-guard, `redact` второй
барьер секретов), `api/chat.py` (`POST /api/chat`: retrieve → гейт should_abstain БЕЗ LLM → генерация →
валидация → downgrade в abstain при пустых/невалидных цитатах; коерция роли, takeLast), `api/search.py`
через `redact`, фронт `ChatPanel`, nginx `= /api/chat` (Bearer, 180s). Прошли llm-engineer +
security-auditor + `/code-review`. **Baseline grounded-точности:** cases 5/5 = 1.00 валидных line-based
цитат, negatives 4/4 abstain.

## Этап 2a — retrieval без LLM

`indexing/lexical.py` (code_tokenize camelCase/snake/пути), per-project FTS5 `fts_<pid>` в `db.py`
(bm25 веса 1/5/2), `indexing/hybrid.py` (RRF k=60 dense+lex, гейт abstain по сырым скорам: dense<0.62 И
нет уверенного bm25≤−4, dense-only fallback), `faiss_store` LRU-кэш, `api/search.py` (`POST /api/search`),
фронт `SearchPanel` + `activeProject.ts`, nginx `= /api/search`. **Baseline: файл 1.00 / символ 0.80 /
MRR 0.900; abstain позитивы 5/5, negatives 4/4.**

## Этап 1 — индексация

`backend/app/indexing/` (validation SSRF-safe → безопасный clone → scan+gitleaks → tree-sitter chunker
→ nomic-эмбеддинги+кэш → per-project FAISS), `db.py` (SQLite projects/files/chunks/embed_cache),
`api/projects.py` (POST/GET/reindex, фон через pipeline state-machine), фронт `ProjectsPanel`
(подключение/список/переключение), токен-гейт nginx на `/api/*` (кроме health) + rate-limit,
eval recall@k (**baseline: файл 1.00, символ 0.80**).

## Этап 0 — фундамент

`backend/app/` (FastAPI, `/api/health`, `LlmService`-абстракция), фронт health-индикатор, деплой
(systemd + nginx + certbot). 11 агентов Слоя A, публичный репо `Eloyan19/jWorkPlace`.
