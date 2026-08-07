# LLM Gateway — результаты прогона

Бэкенд: **real DeepSeek** · кейсов: **12** · совпало с ожиданием: **12/12**

## Что поймали

| # | Кейс | mode | Ожидание | Итог | ✓ | Находки |
|---|------|------|----------|------|---|---------|
| C1 | AWS access key | block | block | block | ✅ | AWS_ACCESS_KEY |
| C2 | OpenAI sk- key | block | block | block | ✅ | OPENAI_API_KEY |
| C3 | GitHub ghp_ token | block | block | block | ✅ | GITHUB_TOKEN |
| C4 | Номер карты (Luhn) | block | block | block | ✅ | CREDIT_CARD |
| C5 | Телефон | block | block | block | ✅ | PHONE |
| C6 | Email | block | block | block | ✅ | EMAIL |
| C7 | Чистый промпт | block | pass | pass | ✅ | — |
| C8 | Маскирование (mask) | mask | mask | mask | ✅ | OPENAI_API_KEY |
| C9 | Base64-секрет | block | block | block | ✅ | BASE64_SECRET |
| C10 | Split-секрет 'sk-'+'proj-' | mask | block | block | ✅ | OPENAI_API_KEY |
| C11 | Вывод: галлюц. секрет | block | out_block | out_block | ✅ | OUTPUT_AWS_ACCESS_KEY |
| C12 | Вывод: утечка system prompt | block | out_block | out_block | ✅ | SYSTEM_PROMPT_LEAK |

## Что осознанно пропускаем (границы защиты)

| # | Кейс | Почему |
|---|------|--------|
| M1 | Секрет разбит по ДВУМ запросам | gateway stateless → фрагменты не склеиваются; нужна корреляция сессии/окна |
| M2 | Стеганография вне base64 (hex/ROT13/gzip) и 76-символьный wrapped-base64 | декодируем только сплошной base64; иные кодировки и перенос строк внутри блоба пропускаем |
| M3 | AWS secret access key (40-симв. base64) | ловим AKIA access-key-id; 40-символьный secret даёт много FP, не детектим |
| M4 | Split иными разделителями ('sk-pr'/'oj-…', '|', '()') | дефраг убирает только кавычки/+/пробелы; но и LLM видит секрет разорванным |
| M5 | Перефраз system-prompt 'своими словами' | canary ловит дословную утечку; секретен только canary, инструкции benign → перефраз = medium-флаг, не блок |

## Учёт стоимости (пример)

```json
{
  "tokens_in": 35,
  "tokens_out": 282,
  "cost_usd": 0.00031965
}
```

## Логи перехваченных секретов

`logs/intercepted_secrets.jsonl` — только preview (`sk-pr***`), сырой секрет не пишем.

```json
{"ip": "testclient", "action": "blocked", "reason": "secret_detected", "findings": [{"type": "AWS_ACCESS_KEY", "preview": "AKIA***(20 симв.)", "channel": "plain", "severity": "high"}], "ts": 1786097821.8121521}
{"ip": "testclient", "action": "blocked", "reason": "secret_detected", "findings": [{"type": "OPENAI_API_KEY", "preview": "skpr***(22 симв.)", "channel": "plain", "severity": "high"}], "ts": 1786097821.8183372}
{"ip": "testclient", "action": "blocked", "reason": "secret_detected", "findings": [{"type": "GITHUB_TOKEN", "preview": "ghp_***(24 симв.)", "channel": "plain", "severity": "high"}], "ts": 1786097821.8235433}
{"ip": "testclient", "action": "blocked", "reason": "secret_detected", "findings": [{"type": "CREDIT_CARD", "preview": "4539***(16 симв.)", "channel": "plain", "severity": "high"}], "ts": 1786097821.8284662}
{"ip": "testclient", "action": "blocked", "reason": "secret_detected", "findings": [{"type": "PHONE", "preview": "+141***(12 симв.)", "channel": "plain", "severity": "medium"}], "ts": 1786097821.833008}
{"ip": "testclient", "action": "blocked", "reason": "secret_detected", "findings": [{"type": "EMAIL", "preview": "al***@example.com", "channel": "plain", "severity": "medium"}], "ts": 1786097821.8377557}
{"ip": "testclient", "action": "masked", "findings": [{"type": "OPENAI_API_KEY", "preview": "skpr***(22 симв.)", "channel": "plain", "severity": "high"}], "ts": 1786097832.4399767}
{"ip": "testclient", "action": "blocked", "reason": "secret_detected", "findings": [{"type": "BASE64_SECRET", "preview": "base64→OPENAI_API_KEY", "channel": "base64", "severity": "high"}], "ts": 1786097836.6095116}
```
