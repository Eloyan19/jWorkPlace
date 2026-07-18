# eval/ — метрики качества jWorkPlace

> Заготовка. На **Этапе 0** (walking skeleton) метрик ещё нет — этот каталог заполняется начиная
> с **Этапа 1**. Паттерн и отчётность — как у соседей `../webchat/eval` и `../rag/eval`
> (метрики заводим сразу, не «потом»).

## Три метрики MVP (см. `PLAN.md` → «Eval» и `CLAUDE.md` → «Eval»)

| Метрика | Появляется | Что измеряет | Ведущий агент |
|---|---|---|---|
| **Retrieval recall@k** | Этап 1 | Golden-набор «вопрос → файл/символ»: доля попаданий нужного чанка в top-k | `rag-indexing-engineer` |
| **Grounded-точность** | Этап 2 | Доля ответов с валидными (line-based) цитатами + корректный `abstain` на off-topic | `llm-engineer` |
| **PR-качество** | Этап 3 | Доля патчей, проходящих `git apply --check`, и доля принятых пользователем PR | `llm-engineer` |

На Этапе 4 к PR-качеству добавляется «доля задач роя, дошедших до валидного PR».

**Принцип:** baseline фиксируется в момент введения метрики и не должен регрессировать на
следующих этапах. Harness тестов — на `qa-engineer`.

## Retrieval recall@k — baseline Этапа 1

Harness: `recall_at_k.py` + golden-набор `golden_markupsafe.json` (репо `pallets/markupsafe`,
5 вопросов «вопрос → файл/символ»). Запуск (из `backend/` с активным `.venv`, Ollama на `:11434`):

```bash
# dense-only (baseline Этапа 1) vs hybrid (Этап 2a) на одном индексе:
JWP_DATA_DIR=/tmp/jwp-eval python ../eval/recall_at_k.py --golden ../eval/golden_markupsafe.json --k 5 --mode hybrid
JWP_DATA_DIR=/tmp/jwp-eval python ../eval/recall_at_k.py --golden ../eval/golden_markupsafe.json --k 5 --mode dense --project-id <id>
```

**Baseline (2026-07-17, dense-only retrieval, k=5):** recall@5 по файлу **1.00 (5/5)**,
по символу **0.80 (4/5)**. Эта цифра не должна регрессировать на следующих этапах.

## Hybrid search + abstain — baseline Этапа 2a

Harness тот же (`recall_at_k.py`), флаг `--mode dense|hybrid`, добавлены **MRR** и **negative-кейсы**
(`golden.negatives` — вопросы не по репо, для калибровки гейта abstain).

**Baseline (2026-07-18, hybrid BM25+dense/RRF, k=5):**
- recall@5 файл **1.00 (5/5)**, символ **0.80 (4/5)**, **MRR 0.900** — уровень dense-baseline,
  без регресса. Слияние RRF k=60, веса bm25 body/symbol/path = 1/5/2.
- **Гейт abstain: позитивы не-abstain 5/5, negatives abstain 4/4.** Пороги (калиброваны здесь,
  `hybrid.py`): dense cosine `< 0.62` **И** нет уверенного лексического хита (`bm25 > −4.0`).
- **Оговорка про символ 0.80:** промах — вопрос про `striptags`; это **метод класса `Markup`**,
  а чанкер Этапа 1 режет по top-level символам, поэтому код `striptags` живёт в чанке `Markup`
  (который находится на позиции 1). Это артефакт гранулярности чанкинга + golden-ожиданий, **не**
  промах retrieval — нужный код извлекается. Метод-уровневый чанкинг — кандидат в доработку Этапа 1.
- Пороги abstain калиброваны на одном репо (markupsafe) — **стартовые**, пересматриваем на новых
  проектах (nomic со `search_query/search_document` даёт «пол» косинуса ~0.55 даже off-topic).
