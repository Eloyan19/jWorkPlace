#!/usr/bin/env bash
# PreToolUse-гейт (Слой A dev-флоу): блокирует `git commit` в jWorkPlace, если в индексе 2+
# ЛОГИЧЕСКИХ файла (backend/app|frontend/src) без свежего вердикта ревью. Переносит правило
# «после 2+ логических файлов — /code-review» из прозы CLAUDE.md в железную гарантию harness
# (см. L0 «Автоматизм — через hooks, не через текст»).
#
# Вердикт-артефакт: swarm-report/<branch>-review.md, ПЕРВАЯ строка = PASS. Свежесть — по mtime:
# если любой staged-файл изменён ПОСЛЕ вердикта, ревью считается устаревшим (block). Разовый
# обход — токен [skip-review] в тексте коммита (для доки/тривиала/самого вердикта).
#
# Ограничение v1 (честно): свежесть по mtime — эвристика, не доказательство, что ревьюился именно
# текущий diff. Точную привязку к git-tree вердикта вводим при созревании конвенции.
set -euo pipefail

REPO="/root/repos/jWorkPlace"
input=$(cat)
# jq на VPS нет — парсим stdin через python3 (гарантированно установлен).
cmd=$(printf '%s' "$input" | python3 -c 'import sys,json; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))' 2>/dev/null || echo "")

# Только реальный git commit; не трогаем amend и явный обход.
case "$cmd" in
  *"git commit"*) : ;;
  *) exit 0 ;;
esac
case "$cmd" in
  *"[skip-review]"*|*"--amend"*) exit 0 ;;
esac

cd "$REPO" 2>/dev/null || exit 0

mapfile -t staged < <(git diff --cached --name-only -- 'backend/app' 'frontend/src' 2>/dev/null || true)
[ "${#staged[@]}" -ge 2 ] || exit 0

branch=$(git branch --show-current 2>/dev/null || echo "")
verdict="swarm-report/${branch}-review.md"

block() {
  {
    echo "🔴 Review-гейт: коммит ${#staged[@]} логических файлов заблокирован. $1"
    echo "   Прогони /code-review, запиши ПЕРВОЙ строкой PASS в ${verdict}, затем коммить."
    echo "   Разовый обход: добавь [skip-review] в текст коммита."
  } >&2
  exit 2
}

[ -f "$verdict" ] || block "нет вердикта ${verdict}."
first=$(head -n1 "$verdict" 2>/dev/null || echo "")
[[ "$first" == PASS* ]] || block "первая строка ${verdict} не PASS (сейчас: '${first:0:40}')."

verdict_mt=$(stat -c %Y "$verdict" 2>/dev/null || echo 0)
for f in "${staged[@]}"; do
  [ -f "$f" ] || continue
  fmt=$(stat -c %Y "$f" 2>/dev/null || echo 0)
  [ "$fmt" -gt "$verdict_mt" ] && block "вердикт устарел: ${f} изменён после ревью."
done
exit 0
