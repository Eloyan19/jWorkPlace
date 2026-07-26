#!/usr/bin/env bash
# Одноразовая настройка self-hosted GitHub Actions раннера на VPS для Level 2 UI-smoke (job ui-smoke
# в .github/workflows/pr-tests.yml). Регистрирует раннер, ставит его systemd-сервисом и включает
# smoke-job репо-переменной ENABLE_SELF_HOSTED_SMOKE=true.
#
# Почему self-hosted: grounded-smoke (чат/поиск) работает только там, где живой backend :8200 +
# Ollama + DeepSeek + проиндексированные проекты — т.е. на этом VPS, не на облачном раннере.
#
# Безопасность: workflow ограничен `fork == false` — на раннере исполняется только код своих
# веток, не чужих форков. Раннер помечен labels self-hosted,linux.
#
# Запуск:  ./deploy/setup-smoke-runner.sh
# Идемпотентно: повторный запуск не перерегистрирует уже настроенный раннер.
set -euo pipefail

REPO="${REPO:-Eloyan19/jWorkPlace}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"
LABELS="${LABELS:-self-hosted,linux}"

log()  { printf '\n\033[1;34m▶ %s\033[0m\n' "$*"; }
die()  { printf '\n\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# ── 0. Проверки предусловий ──────────────────────────────────────────────────────────────────
command -v gh   >/dev/null || die "нет gh CLI (нужен для токена/переменной)."
command -v curl >/dev/null || die "нет curl."
command -v tar  >/dev/null || die "нет tar."
command -v node >/dev/null || die "нет node (раннеру нужен Node для JS-actions; на VPS ожидается v22)."
gh auth status >/dev/null 2>&1 || die "gh не залогинен: выполни 'gh auth login'."

# GitHub-раннер отказывается стартовать под root без явного разрешения.
if [ "$(id -u)" -eq 0 ]; then
  export RUNNER_ALLOW_RUNASROOT=1
  echo "  (root) выставлен RUNNER_ALLOW_RUNASROOT=1"
fi

ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
  x86_64|amd64) ARCH=x64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) die "неизвестная архитектура: $ARCH_RAW" ;;
esac

# ── 1. Скачать и распаковать раннер (если ещё нет) ────────────────────────────────────────────
if [ -f "$RUNNER_DIR/config.sh" ]; then
  log "Раннер уже скачан в $RUNNER_DIR — пропускаю загрузку."
else
  log "Определяю последнюю версию раннера и качаю ($ARCH)…"
  VER="$(gh api repos/actions/runner/releases/latest -q .tag_name | sed 's/^v//')"
  [ -n "$VER" ] || die "не удалось определить версию раннера."
  mkdir -p "$RUNNER_DIR"
  curl -fsSL -o "$RUNNER_DIR/runner.tar.gz" \
    "https://github.com/actions/runner/releases/download/v${VER}/actions-runner-linux-${ARCH}-${VER}.tar.gz"
  tar xzf "$RUNNER_DIR/runner.tar.gz" -C "$RUNNER_DIR"
  rm -f "$RUNNER_DIR/runner.tar.gz"
  echo "  распакован раннер v$VER"
fi

cd "$RUNNER_DIR"

# ── 2. Зарегистрировать (если ещё не сконфигурирован) ─────────────────────────────────────────
if [ -f "$RUNNER_DIR/.runner" ]; then
  log "Раннер уже сконфигурирован (.runner есть) — пропускаю config.sh."
else
  log "Получаю registration-token и регистрирую раннер на $REPO…"
  TOKEN="$(gh api -X POST "repos/${REPO}/actions/runners/registration-token" -q .token)"
  [ -n "$TOKEN" ] || die "не удалось получить registration-token (нужен доступ admin к репо)."
  ./config.sh --url "https://github.com/${REPO}" --token "$TOKEN" \
    --labels "$LABELS" --name "vps-$(hostname)" --unattended --replace
fi

# ── 3. Установить как systemd-сервис ─────────────────────────────────────────────────────────
log "Ставлю раннер systemd-сервисом…"
if [ -x ./svc.sh ]; then
  ./svc.sh install 2>/dev/null || echo "  (сервис, возможно, уже установлен)"
  ./svc.sh start
  ./svc.sh status || true
else
  die "нет svc.sh — распаковка раннера неполная."
fi

# ── 4. Включить smoke-job репо-переменной ────────────────────────────────────────────────────
log "Включаю ui-smoke: ENABLE_SELF_HOSTED_SMOKE=true в $REPO…"
gh variable set ENABLE_SELF_HOSTED_SMOKE --repo "$REPO" --body true

log "Готово ✅"
cat <<EOF

  Раннер зарегистрирован и запущен, ui-smoke включён.
  Проверь: https://github.com/${REPO}/settings/actions/runners
  Теперь на каждый не-форк PR: code-tests (облако) → ui-smoke (этот VPS) со скриншотами в артефактах.

  Снять раннер позже:  cd $RUNNER_DIR && sudo ./svc.sh stop && sudo ./svc.sh uninstall \\
                       && ./config.sh remove --token \$(gh api -X POST repos/${REPO}/actions/runners/remove-token -q .token)
  Выключить smoke:     gh variable set ENABLE_SELF_HOSTED_SMOKE --repo ${REPO} --body false
EOF
