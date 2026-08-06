PASS

# Review — T13 удаление мёртвого bulk-эндпоинта базы знаний (ветка task-13-remove-dead-endpoint)

Ревьюер: backend-developer (правка) + оркестратор (сверка диффа). Python-домен.

## Вердикт: PASS

Удаление хирургическое и полное:
- `POST /api/knowledge/projects/{id}/read` (`mark_read`) удалён из `app/api/knowledge.py`; докстринг
  модуля обновлён (упоминание bulk-`/read` убрано).
- `db.mark_concepts_known` (множественное число) удалён из `app/db.py`.
- Per-concept `db.mark_concept_known` (ЕДИНСТВЕННОЕ число, db.py:560) и маршрут
  `POST /concepts/{slug}/known` **сохранены** — ручная пометка работает.
- Тесты read-ветки (`test_api_knowledge`) и `test_knowledge::test_mark_concepts_known_idempotent`
  удалены.

Проверки:
- `grep mark_concepts_known` по `backend/app` + `backend/tests` — **0 совпадений в коде** (остались
  только упоминания в доках/plan/swarm-report — ожидаемо).
- Фронт (`frontend/src/api.ts`) bulk-`/read` не вызывал — пометка идёт per-concept `/known`.
- Импорты целы: `asyncio.to_thread`/`HTTPException` ещё используются в оставшихся маршрутах.
- `python -m pytest -q` → **364 passed** (было 367, −3 удалённых теста мёртвого кода), без ошибок импорта.

Регрессий нет; per-concept функционал не задет. Эскалаций (живой вызывающий) не обнаружено.
