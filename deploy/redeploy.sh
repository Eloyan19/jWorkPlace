#!/usr/bin/env bash
# Redeploy jWorkPlace на VPS jorchik.com (Этап 0+). Локально, не по SSH.
# Идемпотентно: можно запускать сколько угодно раз.
#
# Главное, ради чего скрипт существует: НЕ забыть проставить GIT_SHA в systemd-юнит,
# иначе GET /api/health вернёт version="dev" или sha от git rev-parse из-под сервиса.
#
# Использование:
#   deploy/redeploy.sh            # backend + frontend (полный redeploy)
#   deploy/redeploy.sh backend    # только backend (deps + GIT_SHA + рестарт)
#   deploy/redeploy.sh frontend   # только пересборка и выкладка фронта
set -euo pipefail

REPO=/root/repos/jWorkPlace
UNIT=/etc/systemd/system/jworkplace.service
WWW=/var/www/jworkplace
HEALTH_URL=https://jwork.jorchik.com/api/health
NGINX_SITE=/etc/nginx/sites-available/jwork.jorchik.com
NGINX_RATELIMIT=/etc/nginx/conf.d/jworkplace-ratelimit.conf

TARGET="${1:-all}"   # all | backend | frontend | nginx

# Читаем GATE_TOKEN из backend/.env (в git не попадает). Барьер публичного URL.
gate_token() {
  grep -E '^GATE_TOKEN=' "$REPO"/backend/.env | cut -d= -f2- || true
}

redeploy_backend() {
  echo "→ backend: GIT_SHA + зависимости + рестарт"
  local sha
  sha=$(git -C "$REPO" rev-parse --short HEAD)
  # Проставляем точный sha релиза в юнит (в git-версии юнита поле пустое — sha живёт только здесь).
  sed -i "s/^Environment=GIT_SHA=.*/Environment=GIT_SHA=${sha}/" "$UNIT"
  echo "  GIT_SHA=${sha}"
  "$REPO"/backend/.venv/bin/pip install -q -r "$REPO"/backend/requirements.txt
  systemctl daemon-reload
  systemctl restart jworkplace.service
}

redeploy_frontend() {
  echo "→ frontend: сборка + выкладка в $WWW"
  # VITE_API_TOKEN — токен-барьер /api/*; попадает в бандл (это барьер, не секрет).
  ( cd "$REPO"/frontend && npm ci --silent && VITE_API_TOKEN="$(gate_token)" npm run build )
  mkdir -p "$WWW"
  rm -rf "${WWW:?}"/assets           # чистим старые хэш-ассеты, чтобы не копились
  cp -r "$REPO"/frontend/dist/* "$WWW"/
}

redeploy_nginx() {
  echo "→ nginx: ratelimit-зоны + инжект токена в сайт-конфиг + reload"
  cp "$REPO"/deploy/nginx-jworkplace-ratelimit.conf "$NGINX_RATELIMIT"
  # Реальный токен НЕ в git: инжектим из backend/.env в живой конфиг вместо плейсхолдера.
  sed "s/__GATE_TOKEN__/$(gate_token)/g" "$REPO"/deploy/nginx-jworkplace.conf > "$NGINX_SITE"
  nginx -t
  systemctl reload nginx
}

case "$TARGET" in
  all)      redeploy_backend; redeploy_frontend; redeploy_nginx ;;
  backend)  redeploy_backend ;;
  frontend) redeploy_frontend ;;
  nginx)    redeploy_nginx ;;
  *) echo "Неизвестный аргумент: $TARGET (ожидается: all | backend | frontend | nginx)" >&2; exit 2 ;;
esac

echo "→ проверка health"
sleep 2
if curl -fsS "$HEALTH_URL"; then
  echo; echo "✅ redeploy OK"
else
  echo; echo "❌ health не отвечает — смотри: journalctl -u jworkplace.service -n 50" >&2
  exit 1
fi
