"""Валидация входной GitHub-ссылки (защита от SSRF/подстановки схем).

Инвариант безопасности (дизайн security-auditor): принимаем ТОЛЬКО публичные репозитории
github.com по строгому allowlist-регэкспу. Всё остальное — отказ, git на непроверенный
ввод не запускаем. Пре-чек размера/приватности — через GitHub API до клонирования.
"""
import re
from dataclasses import dataclass

import httpx

from app.config import get_settings

# Только https://github.com/<owner>/<repo>[.git][/]. owner/repo — безопасный алфавит, длина ≤100.
_GITHUB_RE = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9._-]{1,100})/(?P<repo>[A-Za-z0-9._-]{1,100})(?:\.git)?/?$"
)
# Явные маркеры опасного ввода — режем до регэкспа, чтобы дать понятную ошибку.
_DANGEROUS = ("..", "%", "#", "?", "@", "\\", " ", "\t", "\n")


class ValidationError(ValueError):
    """Ссылка не прошла валидацию (пользовательская ошибка → HTTP 400)."""


@dataclass(frozen=True)
class RepoRef:
    owner: str
    repo: str          # без суффикса .git
    url: str           # нормализованный https-URL для клона
    name: str          # owner/repo — человекочитаемое имя проекта


def parse_github_url(raw: str) -> RepoRef:
    """Разобрать и провалидировать ссылку. Бросает ValidationError при отказе."""
    url = (raw or "").strip()
    if not url:
        raise ValidationError("Пустая ссылка.")
    if not url.startswith("https://"):
        # Отсекаем file://, ssh://, git://, http://, scp-синтаксис git@…: без https не работаем.
        raise ValidationError("Ссылка должна начинаться с https://github.com/")
    for bad in _DANGEROUS:
        if bad in url:
            raise ValidationError("Недопустимые символы в ссылке.")
    m = _GITHUB_RE.match(url)
    if not m:
        raise ValidationError("Ожидается ссылка вида https://github.com/owner/repo")
    owner, repo = m.group("owner"), m.group("repo")
    repo = repo[:-4] if repo.endswith(".git") else repo
    return RepoRef(owner=owner, repo=repo, url=f"https://github.com/{owner}/{repo}.git", name=f"{owner}/{repo}")


async def precheck_repo(ref: RepoRef) -> None:
    """Пре-чек через GitHub API до клонирования: существует, публичный, в пределах размера.

    Без токена (публичные репо). При недоступности API не блокируем клон (лимит размера
    добьёт фильтр рабочего дерева в scan.py) — но приватность/404 ловим строго, если ответ есть.
    """
    settings = get_settings()
    api = f"https://api.github.com/repos/{ref.owner}/{ref.repo}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(api, headers={"Accept": "application/vnd.github+json"})
    except httpx.HTTPError:
        return  # сеть/таймаут — не валим подключение на пре-чеке, дальше отсекут лимиты клона/скана
    if resp.status_code == 404:
        raise ValidationError("Репозиторий не найден (или приватный).")
    if resp.status_code != 200:
        return  # прочие коды (rate-limit 403 и т.п.) не считаем фатальными для публичного клона
    data = resp.json()
    if data.get("private"):
        raise ValidationError("Приватные репозитории пока не поддерживаются.")
    size_kb = data.get("size") or 0
    if size_kb > settings.max_repo_mb * 1024:
        raise ValidationError(
            f"Репозиторий слишком большой ({size_kb // 1024} МБ, лимит {settings.max_repo_mb} МБ)."
        )
