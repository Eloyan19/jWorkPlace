PASS

# Ревью — фича «Изучено» + очистка базы знаний (ветка jworkplace/knowledge-manual-learned)

Уровень: high-recall, дифф `git diff HEAD` (backend/app + frontend/src). Корректностных багов не найдено.

## Проверено
- slug строго kebab (`generator.py:195` `^[a-z0-9]+(-[a-z0-9]+)*$`) → URL-безопасен в `markConceptKnown` (encodeURIComponent не нужен).
- `get_project_concepts` — единственный потребитель `render.py:21`; добавленная колонка `c.slug` не ломает.
- known-DTO `{name}` (`render.py:23`) согласован с оптимистичным `{name: concept.name}` на фронте.
- Гонки закрыты: `handleMarkKnown`/`handleReset` сверяют `mountedRef.current` + `activeIdRef.current === targetId` после await.
- `mark_concept_known` идемпотентен (`COALESCE(known_at, now)`), 404-детект по `rowcount` (slug UNIQUE).
- `reset_known_catalog` мягкий (только known/known_at, строки/связи целы), возвращает count.
- Авто-пометка удалена полностью (`markedRef` + вызов в loadSummary убраны); `markSummaryRead` удалён из api.ts, dangling-ссылок нет.

## Некритичное (не блок)
- Backend `POST /read` + `db.mark_concepts_known` больше не зовутся фронтом (bulk-mark противоречит ручной модели), оставлены «для обратной совместимости». Мёртвый путь — кандидат на удаление отдельной уборкой (их тесты пока зелёные, не трогаем в этом PR).

## Тесты
pytest 366 · vitest 64 · Playwright S1–S6 6/6 — зелёные.
