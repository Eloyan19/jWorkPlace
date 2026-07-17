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

TARGET="${1:-all}"   # all | backend | frontend

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
  ( cd "$REPO"/frontend && npm ci --silent && npm run build )
  mkdir -p "$WWW"
  rm -rf "${WWW:?}"/assets           # чистим старые хэш-ассеты, чтобы не копились
  cp -r "$REPO"/frontend/dist/* "$WWW"/
}

case "$TARGET" in
  all)      redeploy_backend; redeploy_frontend ;;
  backend)  redeploy_backend ;;
  frontend) redeploy_frontend ;;
  *) echo "Неизвестный аргумент: $TARGET (ожидается: all | backend | frontend)" >&2; exit 2 ;;
esac

echo "→ проверка health"
sleep 2
if curl -fsS "$HEALTH_URL"; then
  echo; echo "✅ redeploy OK"
else
  echo; echo "❌ health не отвечает — смотри: journalctl -u jworkplace.service -n 50" >&2
  exit 1
fi
