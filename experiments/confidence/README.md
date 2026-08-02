# confidence — оценка уверенности инференса (без fine-tuning)

Учебный сайдкар поверх задачи классификации тикетов поддержки jWorkPlace (прошлый трек). Не
трогает `backend/app/` — самостоятельная папка, использует базовый DeepSeek (`deepseek-v4-flash`)
без дообучения. Вопрос: как понять, что ответу модели МОЖНО доверять, без обучения отдельного
калибратора — с помощью трёх дешёвых-к-дорогим сигналов уверенности поверх обычного инференса.

## Три подхода

1. **Constraint-based** (`confidence_harness.validate_constraint`) — 0 дополнительных вызовов.
   Ответ обязан парситься как JSON ровно с ключами `category`/`priority` из закрытых множеств
   (`CATEGORIES`/`PRIORITIES`). Нарушение → статус `FAIL`, дальше по этому входу не считаем —
   экономим стоимость вызовов на явно сломанном ответе.
2. **Redundancy / self-consistency** (`confidence_harness.majority_vote`) — тот же запрос
   выполняется `K=3` раза при `temperature=0.7` (первый вызов шага 1 входит в тройку). Majority
   vote по каждому полю (`category`, `priority`) отдельно. `agreement` — средняя доля голосов за
   победителя по обоим полям; `1.0` = полное согласие 3/3 по обоим.
3. **Self-check** (`confidence_harness.parse_verdict`) — отдельный вызов при `temperature=0`:
   модели показывают тикет + её же мажоритарный ответ, просят `confirm`/`reject`. Текст тикета —
   недоверенные данные (может содержать prompt injection в noisy-корзине теста), поэтому он
   оборачивается в nonce-делимитеры + anti-injection формулировка в system-промпте
   (паттерн `backend/app/chat/grounding.py::build_context`).

## Скоринг → статус

- **FAIL** — constraint нарушен на первом вызове (или сетевой/форматный сбой самого вызова).
- **OK** — constraint ок И redundancy 3/3 по обоим полям И self-check = `confirm`.
- **UNSURE** — constraint ок, но redundancy раскол (agreement < 1.0) ИЛИ self-check = `reject`
  (включая случай, когда сам self-check-вызов не удался — fail-closed: сомневаешься → reject,
  не confirm).

На каждый вход `classify_with_confidence()` возвращает:
```json
{
  "answer": {"category": "...", "priority": "..."} | null,
  "status": "OK" | "UNSURE" | "FAIL",
  "agreement": 0.0-1.0,
  "self_check": "confirm" | "reject" | null,
  "n_llm_calls": 1-4,
  "latency_sec": 0.0,
  "tokens": {"prompt": 0, "completion": 0, "total": 0}
}
```
(+ служебные `note`/`breakdown`/`call_latencies` для отчёта, не обязательные для потребителя API.)

## Как запускать

Ключ уже лежит в `../../backend/.env` (`DEEPSEEK_API_KEY`) — харнесс подхватит его сам, парсингом
файла (без импорта `backend.app`). Можно и явным env: `export DEEPSEEK_API_KEY=...`.

```bash
cd experiments/confidence
../../backend/venv/bin/python3 -m pip install -r requirements.txt  # если httpx ещё не в venv
../../backend/venv/bin/python3 run_experiment.py
```
(Используется существующий venv backend/ — там уже есть httpx 0.28.1; отдельный venv для
сайдкара не заводили, чтобы не плодить окружения ради одного пакета.)

Прогон — ~20 входов из `data/test_inputs.jsonl` × до 4 вызовов DeepSeek каждый (constraint-фейлы
останавливаются на 1 вызове) ≈ до 80 вызовов `deepseek-v4-flash`. Пишет `report.md` в этой же
папке. Если ключа нет или API недоступен — скрипт НЕ выдумывает числа, помечает в `report.md`,
что прогон не выполнен, и завершается с ненулевым кодом.

## Тест-сет (`data/test_inputs.jsonl`)

20 строк, три корзины:
- **correct (10)** — из `/tmp/eval_correct.jsonl` прошлого трека, с gold-метками.
- **borderline (~5)** — двусмысленные тикеты (на стыке категорий/приоритетов), gold в основном
  `null` (спорная метка), с пояснением в `note`.
- **noisy (~5)** — опечатки/транслит, смесь RU/EN, обрывок фразы, эмодзи-спам, prompt injection,
  полностью off-topic. Все `gold: null`.

## Как читать `report.md`

- Таблица статусов по корзинам — ожидаемо: `correct` в основном `OK`, `borderline`/`noisy` дают
  больше `UNSURE`/`FAIL` — это и есть цель харнесса (система должна «плавать» именно там, где
  реально неоднозначно).
- «Дополнительный инференс» — сколько входов потребовало вызовов сверх одного базового и во что
  это обошлось (доп. вызовы, latency, $).
- «Точность на correct» — accuracy majority-ответа vs gold и **false reject**: доля верных
  ответов, которые контроль всё равно увёл в UNSURE/FAIL. Это цена надёжности — чем строже гейт,
  тем выше accuracy у пропущенных ответов, но тем больше верных ответов уходит на ручной разбор.

## Ограничения

- Прайс DeepSeek в `confidence_harness.py` (`PRICE_*_USD_PER_MTOK`) — оценочные константы,
  cache-hit/miss не различается; `estimate_cost_usd()` даёт верхнюю границу, не точный биллинг.
- `deepseek-v4-flash` не даёт logprobs через этот эндпоинт в OpenAI-совместимом режиме, поэтому
  сигналы уверенности здесь — поведенческие (constraint/redundancy/self-check), не
  token-probability based; это осознанное ограничение трека «без fine-tuning и без доступа к
  logprobs», не недосмотр.
- Тест-сет мал (20 строк) — числа в `report.md` показывают направление, не статистически строгую
  оценку; для продакшен-решения нужен golden-набор на порядки больше.
