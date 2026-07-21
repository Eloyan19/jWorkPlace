# jWorkPlace

AI code-assistant поверх произвольного GitHub-репо: RAG-индексация кода, grounded-чат по проекту,
рой runtime-агентов, авто-PR. Прод: **https://jwork.jorchik.com** · backend `127.0.0.1:8200`.

> Стадия: **Этап 0 — walking skeleton** (живой health-эндпоинт по домену). Роадмап — [`PLAN.md`](PLAN.md),
> правила/инварианты/агенты — [`CLAUDE.md`](CLAUDE.md).

## Раскладка

```
backend/    FastAPI-пакет app/ (config, version, api/, llm/), тесты   — Python 3.13
frontend/   Vite + React + TS (health-индикатор)                      — Node 22
deploy/     systemd-юнит + nginx-конфиг + README деплоя
eval/       метрики качества (заводятся с Этапа 1) — см. [`eval/README.md`](eval/README.md)
.claude/    агентские профили (архитектор, разработчики, QA и др.)   — см. [`CLAUDE.md`](CLAUDE.md)
```

## Быстрый старт (dev — два процесса)

**Backend** (`:8200`):
```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # заполни значения (секреты — только сюда)
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8200
# проверка:  curl -s http://127.0.0.1:8200/api/health  → {"status":"ok","version":...}
```

**Frontend** (`:5173`, dev-proxy `/api` → `:8200`):
```bash
cd frontend
npm install
npm run dev
```

Открой `http://localhost:5173` — заголовок jWorkPlace и зелёный индикатор «backend online».

## Тесты

```bash
cd backend  && .venv/bin/pytest        # backend smoke
cd frontend && npx vitest run          # frontend smoke
```

## Прод-деплой

systemd + nginx + certbot на VPS `jorchik.com` — см. **`deploy/README.md`**. Секреты — в
`backend/.env` (в git не попадает). Данные рантайма — в `$JWP_DATA_DIR` (`/var/lib/jworkplace`,
вне git-дерева; появляются с Этапа 1).

## Инварианты (кратко; полностью — `CLAUDE.md`)

- Браузер → **свой backend** → LLM/GitHub. Ключи/токены — только env, никогда в git/логи/ответ/промпт.
- Фронт зовёт **относительный** `/api/...` (same-origin): в dev проксирует Vite, в проде — nginx.
- LLM — за провайдер-абстракцией (`app/llm/`) с первого дня; на Этапе 0 адаптер — заглушка без сети.
