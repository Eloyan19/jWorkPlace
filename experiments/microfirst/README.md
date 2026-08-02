# microfirst — micro-model first (двухуровневый инференс: эмбеддинг-микромодель → LLM)

Учебный сайдкар (финал серии про экономику инференса). Задача — **классификация интента тикета
поддержки** (6 категорий) — решаем **каскадом из двух уровней**:

- **Уровень 1 — micro-model (эмбеддинги, БЕЗ LLM):** nearest-centroid на `nomic-embed-text` (локальный
  Ollama :11434, ядро retrieval самого jWorkPlace). Возвращает метку + сигнал уверенности
  (`margin` = отрыв top1 от top2) → статус `OK`/`UNSURE`.
- **Уровень 2 — LLM fallback:** DeepSeek `deepseek-v4-flash` вызывается **только** на `UNSURE`
  (двусмысленно / off-topic / инъекция / шум), с anti-injection и строгим форматом.

> Полная теория, термины и **глубокое сравнение трёх способов Уровня 1** (маленькая LLM vs
> embedding-классификация vs ML-классификатор) — в [`EXPLAINER.md`](./EXPLAINER.md). Начни с него.

Не трогает `backend/app/`. HTTP-ядро DeepSeek, парсинг ключа и прайс переиспорт из соседнего
`../confidence/confidence_harness.py`; контракт Ollama (endpoint/префиксы/L2-норм) зеркалит
`backend/app/indexing/embeddings.py`. Секреты (`DEEPSEEK_API_KEY` из env/`backend/.env`) — никогда
не логируются и не попадают в промпт.

## Почему embedding-микромодель (кратко; подробно — в EXPLAINER §4)

Инфраструктура nomic уже есть на VPS; большой размеченной выборки нет (few-shot прототипы работают,
logreg переобучится); вход многоязычный/шумный (TF-IDF ломается на транслите); уверенность встроена
геометрически (margin); дёшево + детерминированно + локально (small LLM на 3.8 ГБ RAM избыточна).

## Как устроено

- `micro.py` — `EmbeddingMicroModel`: строит центроиды из `data/seed_examples.jsonl` (эмбеддинг+усреднение),
  классифицирует по косинусу, гейт по `margin ≥ TAU_MARGIN` и `top1 ≥ TAU_ABS`. Пороги —
  откалиброваны по распределению margin реального прогона (см. EXPLAINER §7, caveat про held-out).
- `pipeline.py` — маршрут `classify_two_level` (micro → при UNSURE → LLM) + базлайн `classify_llm_only`
  («всегда LLM») для честного сравнения. Все функции решения чистые/fail-closed.
- `data/` — `seed_examples.jsonl` (прототипы, TRAIN — отдельно от теста), `test_inputs.jsonl` (общий
  20-тикетный набор треков confidence/routing/decomposition), `fallback_prompt.txt` (промпт Уровня 2).
- `run_microfirst.py` — реальный прогон обоих путей по каждому входу → `report.md`.
- `tests/test_microfirst.py` — юнит-логика (гейт margin/top1, валидатор категории, маршрут micro→LLM,
  fail-closed, anti-injection-делимитеры) на замоканных Ollama+DeepSeek, без сети.

## Запуск

Нужны: Ollama :11434 с `nomic-embed-text`; `DEEPSEEK_API_KEY` (env или `backend/.env`).

```bash
cd experiments/microfirst
../../backend/venv/bin/python3 run_microfirst.py        # реальный прогон → report.md
../../backend/venv/bin/python3 -m pytest tests/ -q       # юнит-логика (без сети)
```

Нет ключа / Ollama или API недоступны → скрипт НЕ выдумывает числа, помечает это в `report.md` и
выходит с кодом 1.

## Итог прогона (см. `report.md`)

Микромодель забрала **70%** входов; вызовы большой LLM **−68%**, стоимость **−71%**, **точность не
просела** (10/10 на gold = «всегда-LLM»). Инъекция корректно эскалирована и не поддалась. Главный
урок: сигнал уверенности эмбеддинга — **margin (отрыв), а не абсолютная близость** (nomic жмёт все
косинусы в полосу ~0.7).

## Ограничения

- 20 строк — направление, не строгая статистика.
- Порог `TAU_MARGIN=0.03` калиброван на этом наборе (в проде — held-out val-set, EXPLAINER §7).
- Прайс `PRICE_*` — placeholder; относительный вывод (−71%) держится, точный биллинг — нет.
- Классифицируем только категорию; приоритет вне Уровня 1 сознательно (эмбеддинги слабы на оси
  срочности — EXPLAINER §6).
- Реализован один способ Уровня 1 (embedding); small-LLM и TF-IDF-варианты разобраны концептуально
  (EXPLAINER §3) — эмпирическое сравнение всех трёх = продолжение трека.
