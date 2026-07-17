# Деплой jWorkPlace на VPS jorchik.com

Мы работаем **прямо на проде** (тот же хост, что обслуживает `jorchik.com`/`llm.jorchik.com`) —
все операции ниже локальные, не по SSH. Секреты — только в `backend/.env` (в git не попадает,
см. `.gitignore`). `$JWP_DATA_DIR` (по умолчанию `/var/lib/jworkplace`) — вне git-дерева.

## Первичный деплой (Этап 0)

Порядок важен: backend поднимаем раньше nginx, чтобы `/api/*` сразу отвечал при первом заходе.

### 1. Backend: venv + .env

```bash
cd /root/repos/jWorkPlace/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# отредактировать .env: при необходимости DEEPSEEK_API_KEY, CORS_ORIGINS и т.д.
# (на Этапе 0 достаточно значений по умолчанию)
```

### 2. systemd-юнит

```bash
sudo cp /root/repos/jWorkPlace/deploy/jworkplace.service /etc/systemd/system/jworkplace.service
sudo systemctl daemon-reload
sudo systemctl enable --now jworkplace.service
systemctl status jworkplace.service   # ожидаем active (running)
curl -s http://127.0.0.1:8200/api/health   # {"status":"ok","version":"..."}
```

### 3. Фронт: сборка и выкладка

```bash
cd /root/repos/jWorkPlace/frontend
npm ci
npm run build
sudo mkdir -p /var/www/jworkplace
sudo cp -r dist/* /var/www/jworkplace/
```

(Фронт Этапа 0 создаётся `frontend-developer`; шаг актуален, когда `frontend/` появится.)

### 4. nginx: server-блок

```bash
sudo cp /root/repos/jWorkPlace/deploy/nginx-jworkplace.conf /etc/nginx/sites-available/jwork.jorchik.com
sudo ln -s /etc/nginx/sites-available/jwork.jorchik.com /etc/nginx/sites-enabled/jwork.jorchik.com
sudo nginx -t
sudo systemctl reload nginx
curl -s http://jwork.jorchik.com/api/health   # ещё без TLS, но уже должно ответить ok
```

### 5. TLS через certbot

```bash
sudo certbot --nginx -d jwork.jorchik.com
curl -s https://jwork.jorchik.com/api/health   # {"status":"ok",...}, валидный TLS
```

DNS уже резолвится (`jwork.jorchik.com` -> `202.71.13.114`, wildcard `*.jorchik.com`) —
дополнительная A-запись не требуется.

## Проверка после первичного деплоя

```bash
systemctl is-active jworkplace.service        # active
curl -s https://jwork.jorchik.com/api/health  # {"status":"ok","version":"<sha>"}
```

Ручная проверка: открыть `https://jwork.jorchik.com` в браузере — страница jWorkPlace,
замок TLS в адресной строке, индикатор «backend online» зелёный.

## Этап 1 — индексация (первичная настройка на VPS)

Разово, кроме кода (уже в `redeploy.sh`):

### 1. gitleaks (скан секретов до индексации)

```bash
cd /tmp && curl -sL https://github.com/gitleaks/gitleaks/releases/download/v8.30.1/gitleaks_8.30.1_linux_x64.tar.gz -o gitleaks.tar.gz
tar xzf gitleaks.tar.gz gitleaks && sudo install -m 0755 gitleaks /usr/local/bin/gitleaks && gitleaks version
```

Без gitleaks сервис не падает (деградация до фильтра чувствительных имён + warning в журнале),
но полноценный скан секретов чужого репо требует бинаря.

### 2. Data-dir (`$JWP_DATA_DIR`, вне git)

```bash
sudo mkdir -p /var/lib/jworkplace && sudo chmod 700 /var/lib/jworkplace
```

Сюда сервис (от root) кладёт `jworkplace.sqlite`, `repos/<id>/` (клоны), `indexes/<id>/index.faiss`.

### 3. Токен-барьер публичного URL

`GATE_TOKEN` в `backend/.env` (`openssl rand -hex 32`, в git не попадает). Его использует:
- **nginx** — гейт на `/api/*` кроме `/api/health` (плейсхолдер `__GATE_TOKEN__` в
  `deploy/nginx-jworkplace.conf` → инжектится в живой конфиг на деплое);
- **фронт** — как `VITE_API_TOKEN` (пробрасывает `redeploy.sh` при сборке; это барьер, не секрет).

Rate-limit зоны — `deploy/nginx-jworkplace-ratelimit.conf` → `/etc/nginx/conf.d/`.
Всё это ставит идемпотентно `deploy/redeploy.sh nginx` (и `all`). Открыть UI можно так:
`https://jwork.jorchik.com/?token=<GATE_TOKEN>` (токен осядет в localStorage).

### 4. Зависимости индексации

`requirements.txt` тянет `tree_sitter`, `tree_sitter_language_pack`, `faiss-cpu`, `numpy`
(ставит `redeploy.sh backend`). Ollama `nomic-embed-text` на `:11434` уже поднят.

## Redeploy (после обновления кода)

Одной командой — скрипт `deploy/redeploy.sh` (идемпотентный, проставляет `GIT_SHA` в юнит,
чтобы этот шаг не терялся вручную):

```bash
cd /root/repos/jWorkPlace
git pull                        # или локальные изменения уже в рабочем дереве
deploy/redeploy.sh              # backend + frontend, затем проверка health
# deploy/redeploy.sh backend    # только backend (deps + GIT_SHA + рестарт)
# deploy/redeploy.sh frontend   # только пересборка и выкладка фронта
```

Скрипт делает: `GIT_SHA=$(git rev-parse --short HEAD)` → в юнит; `pip install` backend-зависимостей;
`daemon-reload` + `restart jworkplace.service`; `npm ci && npm run build` + выкладка в `/var/www/jworkplace`
(со чисткой старых хэш-ассетов); финальный `curl` к `https://jwork.jorchik.com/api/health`.

## Как проставить GIT_SHA в юнит (что делает скрипт под капотом)

`GET /api/health` берёт версию из env `GIT_SHA`; если пусто — падает на
`git rev-parse --short HEAD`, а если и это недоступно — на `"dev"`. В git-версии юнита поле
`Environment=GIT_SHA=` пустое (нельзя закоммитить sha в самого себя) — точный sha релиза
проставляется в `/etc/systemd/system/jworkplace.service` на деплое. Скрипт делает это так:

```bash
SHA=$(cd /root/repos/jWorkPlace && git rev-parse --short HEAD)
sudo sed -i "s/^Environment=GIT_SHA=.*/Environment=GIT_SHA=${SHA}/" /etc/systemd/system/jworkplace.service
sudo systemctl daemon-reload
sudo systemctl restart jworkplace.service
```

## Troubleshooting

- `systemctl status jworkplace.service` красный -> `journalctl -u jworkplace.service -n 50`.
- `curl http://127.0.0.1:8200/api/health` не отвечает -> проверить venv/зависимости,
  `ExecStart` путь к `.venv/bin/uvicorn`, права на `WorkingDirectory`.
- nginx `502` на `/api/*` -> backend не поднят или слушает не на `127.0.0.1:8200`.
- nginx `nginx -t` fails -> проверить, что `server_name` не конфликтует с другим
  `sites-enabled`-файлом (не мёржить с `jorchik.com`/`llm.jorchik.com`).
