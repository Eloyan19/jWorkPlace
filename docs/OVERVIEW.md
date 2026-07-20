# Backend Overview — jWorkPlace API

## Архитектура

Backend — FastAPI-приложение (`backend/app/main.py`), запускается uvicorn на `127.0.0.1:8200`.
Публичный URL (`https://jwork.jorchik.com`) проксируется через nginx: `/api/*` → backend,
статику (`/`) → Vite-сборка фронта. Токен-барьер (Bearer) и rate-limit — на nginx.

## Основные API-эндпоинты

### 1. Health — `GET /api/health`

Проверка работоспособности сервиса. Не требует авторизации.

- **Ответ:** `{"status": "ok", "version": "<git-sha>"}`
- **Назначение:** мониторинг, индикатор «backend online» на фронте.

---

### 2. Projects — `/api/projects`

Управление подключёнными GitHub-репозиториями.

| Метод | Путь | Назначение |
|-------|------|------------|
| `POST` | `/api/projects` | Подключить новый репозиторий (валидация URL → клон → фоновая индексация). |
| `GET` | `/api/projects` | Список всех проектов со статусами (`cloning`/`scanning`/`indexing`/`ready`/`error`). |
| `GET` | `/api/projects/{id}` | Детали конкретного проекта (статус, ошибка, дата индексации, head_sha). |
| `POST` | `/api/projects/{id}/reindex` | Переиндексировать проект (инкрементально: `git pull` → изменённые файлы). |

---

### 3. Chat — `POST /api/chat`

Grounded-чат по коду активного проекта (Этап 2b).

- **Тело:** `{ project_id, messages: [{ role, content }] }`
- **Логика:** hybrid search → гейт «не знаю» (abstain без вызова LLM) → контекст → DeepSeek JSON-режим → line-based валидация цитат по файлам на диске.
- **Ответ:** `{ answer, abstain, sources: [{ file, symbol, lines, citation, quote }] }`
- **Назначение:** ответы, обоснованные кодом проекта; off-topic → «Не знаю по этому проекту».

---

### 4. Search — `POST /api/search`

Retrieval без LLM (Этап 2a) — гибридный поиск по коду активного проекта.

- **Тело:** `{ project_id, query, k }`
- **Логика:** dense-эмбеддинги (FAISS) + BM25 (SQLite FTS5) → RRF-ранжирование → гейт abstain.
- **Ответ:** `{ project_id, query, k, abstain, abstain_reason, hits: [...] }`
- **Назначение:** сырой поиск фрагментов кода с оценками релевантности (dense_score, bm25_score, rrf_score).

---

### 5. Edit — `POST /api/edit` *(планируется, Этап 3)*

Предложить правку кода по текстовому описанию.

- **Назначение:** генерация diff'а на основе найденного контекста → подтверждение пользователем.

---

### 6. Review — `POST /api/review` *(планируется, Этап 3)*

Запросить code review изменений.

- **Назначение:** анализ diff'а LLM-агентами с замечаниями по стилю, безопасности, архитектуре.

---

### 7. Structure — `GET /api/structure` *(планируется)*

Получить структуру проекта (дерево файлов/символов).

- **Назначение:** навигация по репозиторию без клонирования на клиенте.

---

### 8. Support — `POST /api/support` *(планируется)*

Техническая поддержка / вопросы по платформе.

- **Назначение:** общие вопросы, не привязанные к конкретному проекту.

---

### 9. Agent — `POST /api/agent` *(планируется, Этап 4)*

Запуск роя runtime-агентов (Слой B) для выполнения сложной задачи.

- **Назначение:** multi-agent pipeline (planner → coder → reviewer → judge) → создание Pull Request.

---

## Статус реализации

| Эндпоинт | Статус | Этап |
|----------|--------|------|
| `GET /api/health` | ✅ Реализован | 0 |
| `POST /api/projects` | ✅ Реализован | 1 |
| `GET /api/projects` | ✅ Реализован | 1 |
| `GET /api/projects/{id}` | ✅ Реализован | 1 |
| `POST /api/projects/{id}/reindex` | ✅ Реализован | 1 |
| `POST /api/search` | ✅ Реализован | 2a |
| `POST /api/chat` | ✅ Реализован | 2b |
| `POST /api/edit` | 🔜 Планируется | 3 |
| `POST /api/review` | 🔜 Планируется | 3 |
| `GET /api/structure` | 🔜 Планируется | — |
| `POST /api/support` | 🔜 Планируется | — |
| `POST /api/agent` | 🔜 Планируется | 4 |

## Ключевые модули backend

| Модуль | Назначение |
|--------|------------|
| `app/api/` | Эндпоинты FastAPI (health, projects, search, chat) |
| `app/indexing/` | Конвейер индексации: клон, скан, tree-sitter чанкинг, эмбеддинги, FAISS, BM25 |
| `app/chat/` | Grounding: построение контекста, парсинг и валидация цитат |
| `app/llm/` | Абстракция LLM-провайдера (адаптер DeepSeek) |
| `app/db.py` | SQLite-хранилище проектов, файлов, чанков |
| `app/config.py` | Конфигурация из env (pydantic-settings) |
