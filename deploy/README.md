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

## Redeploy (после обновления кода)

```bash
cd /root/repos/jWorkPlace
git pull   # или локальные изменения уже в рабочем дереве

# backend, если менялись зависимости
cd backend && .venv/bin/pip install -r requirements.txt && cd ..

# проставить GIT_SHA в юнит (см. ниже) перед рестартом, если хотим видеть его в /api/health

sudo systemctl restart jworkplace.service
systemctl status jworkplace.service

# фронт, если менялся
cd frontend && npm ci && npm run build && cd ..
sudo cp -r frontend/dist/* /var/www/jworkplace/

curl -s https://jwork.jorchik.com/api/health
```

## Как проставить GIT_SHA в юнит

`GET /api/health` берёт версию из env `GIT_SHA`; если пусто — падает на
`git rev-parse --short HEAD`, а если и это недоступно — на `"dev"`. На деплое можно
зафиксировать точный sha релиза явно:

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
