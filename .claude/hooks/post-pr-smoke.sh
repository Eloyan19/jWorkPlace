#!/usr/bin/env bash
# PostToolUse-хук (Слой A dev-флоу): после успешного `gh pr create` напоминает прогнать единый
# QA-цикл `/post-pr-check` (Level 1 код-тесты + Level 2 UI-smoke). Переносит правило «после PR —
# прогнать оба уровня и собрать отчёт» из прозы в железное напоминание harness (L0 «Автоматизм —
# через hooks»). Не блокирует (PR уже создан) — только инжектит напоминание в контекст модели.
#
# Механика: PostToolUse на Bash; если команда содержала `gh pr create` — печатаем напоминание в
# stderr и выходим кодом 2 (harness подаёт stderr обратно модели как контекст). Иначе тихо выходим.
set -euo pipefail

input=$(cat)
# jq на VPS нет — парсим stdin через python3 (гарантированно установлен).
cmd=$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || echo "")

# `git commit`/`git log` могут содержать строку "gh pr create" в тексте сообщения — это НЕ создание
# PR, гасим ложное срабатывание (матч по подстроке иначе ловит текст коммита).
case "$cmd" in
  *"git commit"*|*"git log"*|*"git show"*|*"echo "*) exit 0 ;;
esac
case "$cmd" in
  *"gh pr create"*) : ;;
  *) exit 0 ;;
esac

{
  echo "🧪 PR создан → прогони единый QA-цикл: /post-pr-check"
  echo "   Level 1 (pytest+vitest) + Level 2 (Playwright smoke со скриншотами) → сводный отчёт в swarm-report/."
  echo "   Если задеплоил новую фичу — /post-pr-check --refresh (обновит smoke под изменения и прогонит всё)."
} >&2
exit 2
