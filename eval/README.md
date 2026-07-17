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
JWP_DATA_DIR=/tmp/jwp-eval python ../eval/recall_at_k.py --golden ../eval/golden_markupsafe.json --k 5
# переиспользовать готовый индекс: добавить --project-id <id>
```

**Baseline (2026-07-17, dense-only retrieval, k=5):** recall@5 по файлу **1.00 (5/5)**,
по символу **0.80 (4/5)**. Единственный промах символа (`striptags`) ожидаемо добьёт **hybrid
search (BM25+dense/RRF)** Этапа 2 — dense-эмбеддинги хуже ловят точные идентификаторы. Эта цифра
не должна регрессировать на следующих этапах.
