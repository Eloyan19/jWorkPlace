# experiments/finetune — учебный LoRA fine-tune классификатора тикетов

Учебный трек fine-tuning, приземлённый на реальные данные jWorkPlace: **классификация тикетов
поддержки** (категория из 6 + приоритет из 3, см. [`taxonomy.md`](./taxonomy.md)). Это **сайдкар**,
не прод-фича — весь код здесь, `backend/app/` не тронут.

## Почему так, а не через API

Путь тренировки — **локальный**: Kaggle (бесплатный GPU T4) → LoRA на `Qwen2.5-3B-Instruct` →
экспорт в GGUF → запуск в Ollama на нашем VPS. OpenAI недоступен; DeepSeek — облачный API без
fine-tuning эндпоинта в текущей интеграции, поэтому тюн делаем сами на открытой модели.

## Полный флоу

```
1. (здесь, на VPS)     готовим датасет  → data/train.jsonl + data/eval.jsonl + manifest.json
2. (здесь, на VPS)     validate.py      → гейт качества данных перед отправкой в Kaggle
3. (здесь, опционально) baseline_runner.py против облачной/локальной модели → cloud-baseline для сравнения
4. (Kaggle, GPU T4)    kaggle_finetune.ipynb → Run All: baseline → LoRA-тюн → after-метрика → GGUF
5. (Kaggle → VPS)      скачать .gguf, Modelfile, `ollama create`
6. (здесь, на VPS)     baseline_runner.py против ollama-модели → сравнить с cloud-baseline и с Kaggle after-метрикой
```

## Файлы

| Файл | Что делает |
|---|---|
| `data/train.jsonl` | 40 примеров для тюна (OpenAI chat JSONL) |
| `data/eval.jsonl` | 10 примеров для оценки (не пересекается с train) |
| `data/manifest.json` | провенанс каждого примера (`real`/`faq_derived`/`synthetic`) + доля грунтованных |
| `validate.py` | локальный валидатор датасета (структура, таксономия, дубли, сплит) — stdlib, без зависимостей |
| `baseline_runner.py` | провайдер-агностичный раннер (OpenAI-совместимый клиент): гоняет eval.jsonl против любой chat-модели (Ollama/DeepSeek/OpenAI), считает accuracy |
| `taxonomy.md` | описание категорий/приоритетов + правила разметки + критерии оценки «стало лучше» |
| `kaggle_finetune.ipynb` | ноутбук для Kaggle: LoRA-тюн Qwen2.5-3B + baseline/after сравнение + экспорт GGUF |
| `requirements.txt` | зависимости для локального запуска `baseline_runner.py` (`validate.py` — stdlib) |

## Как запускать

### 1. Валидация датасета (локально, на VPS)

```bash
cd experiments/finetune
python3 validate.py
```
Печатает сводку по распределению категорий/приоритетов и долю грунтованных (real+faq_derived)
примеров; ненулевой exit code при любой структурной ошибке.

### 2. Baseline (опционально, до Kaggle — для внешней точки сравнения)

```bash
pip install -r requirements.txt

# против локального Ollama (нужна уже установленная instruct-модель для сравнения):
python3 baseline_runner.py --base-url http://localhost:11434/v1 --model qwen2.5:3b

# против DeepSeek (ключ только из env, никогда не передавайте его аргументом в реальном запуске):
python3 baseline_runner.py --base-url https://api.deepseek.com/v1 --model deepseek-chat \
    --api-key-env DEEPSEEK_API_KEY
```
Результаты (ответы модели + метрики) пишутся в `baseline_responses.json`.

### 3. Fine-tune на Kaggle

1. Загрузите `data/train.jsonl` и `data/eval.jsonl` в Kaggle (Dataset или прямой upload в сессию).
2. Откройте `kaggle_finetune.ipynb`, включите GPU T4, поправьте пути `TRAIN_PATH`/`EVAL_PATH`.
3. **Run All.** Ноутбук сам снимает before/after метрику на одних и тех же 10 eval-примерах и
   печатает сравнительную таблицу.
4. Скачайте экспортированный GGUF (`q4_k_m`) из Kaggle Output.

### 4. Подключение на VPS (Ollama)

```bash
# Modelfile рядом со скачанным .gguf:
echo "FROM ~/models/jwp-ticket-classifier.gguf" > Modelfile
ollama create jwp-ticket-classifier -f Modelfile

# сравнение с cloud-baseline той же метрикой:
python3 baseline_runner.py --base-url http://localhost:11434/v1 --model jwp-ticket-classifier
```

## Критерии оценки

См. [`taxonomy.md`](./taxonomy.md) — раздел «Критерии оценки «стало лучше»»: per-field accuracy
(`category`, `priority`), доля валидного JSON-формата, exact-match accuracy; baseline vs tuned.

## Инварианты (унаследованы от `../../CLAUDE.md`)

- Секреты (`DEEPSEEK_API_KEY` и т.п.) — только из env/CLI-аргумента в момент запуска, никогда в
  датасете/ноутбуке/логах.
- Датасет — синтетика + грунтовка на реальных данных саппорта jWorkPlace (`tickets.json`, `faq.md`),
  без внешних приватных данных.
- Тренировка/сетевые вызовы отсюда **не запускаются автоматически** — только код подготовлен;
  `kaggle_finetune.ipynb` выполняется вручную на Kaggle, `baseline_runner.py` — вручную локально.
