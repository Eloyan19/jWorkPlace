# Level 1 — код-тесты (unit/integration)

**Прогон:** pytest **366 passed** · vitest **64 passed** (9 файлов) · офлайн, детерминированы.
Команды: `cd backend && .venv/bin/python -m pytest -q` · `cd frontend && npx vitest run`.

## Backend (pytest, 26+ файлов, `backend/tests/`)
Зеркалят модули `app/`. Ключевое покрытие:
- **Индексация:** `test_chunker`, `test_faiss_store` (LRU, инвалидация), `test_embeddings` (кэш по blob_sha), `test_langs`, `test_lexical`, `test_hybrid`, `test_scan` (gitleaks fail-closed), `test_secret_gate`, `test_validation` (SSRF).
- **Grounding/чат:** `test_grounding` (line-based цитаты), `test_chat`, `test_injection` (anti-prompt-injection).
- **База знаний:** `test_knowledge` (генерация, matching known/new, gray-zone judge, `mark_concept_known`, `reset_known_catalog`), `test_api_knowledge` (эндпоинты summary/`concepts/{slug}/known`/`concepts/reset`).
- **Правки/PR:** `test_patcher` (git apply --check), `test_stage3b` (PAT at rest), `test_review`.
- **LLM-адаптер:** `test_llm_deepseek` (ошибки API, retry на `length`, парсинг) — `@pytest.mark.asyncio`.
- **Прочее:** `test_db`, `test_fts`, `test_project_management`, `test_support` (MCP), `test_health`, `test_structure`, `test_progress`.

Тесты, добавленные в рамках задания:
- **Тест-инфра (Level 1):** `test_faiss_store`, `test_embeddings`, `test_langs`, `test_llm_deepseek` — +65 тестов на непокрытые модули (coverage backend 81%→83%).
- **Фича «Изучено»/сброс:** +9 тестов в `test_knowledge`/`test_api_knowledge`.

## Frontend (vitest, 9 файлов, `frontend/src/__tests__/`)
`AgentPanel`, `ChatPanel`, `EditPanel`, `HealthIndicator`, `ProjectsPanel`, `SearchPanel`,
`StructurePanel`, `SummaryPanel` (переписан под ручную пометку «Изучено» + «Очистить»), `SupportPanel`.
Все — `@testing-library/react` + `vi.mock('../api')`.

## Герметичность (доказано сквозным прогоном)
Первый CI-прогон на чистом раннере честно упал (11 fail): async-тесты без `pytest-asyncio`,
`test_scan` без бинаря `gitleaks`, `test_knowledge` без мока Ollama. Все три исправлены — тесты
больше не зависят от окружения VPS. Это и есть польза Level 1 в CI: ловит скрытые зависимости.
