"""GitHub-запись (Этап 3b): валидация PAT, писабельный клон, push, `gh pr create`.

Главный носитель must-fix security-auditor (см. PLAN.md/CLAUDE.md, раздел «Инварианты
безопасности»): PAT шифруется at rest (Fernet), git получает токен ТОЛЬКО через env
(`GIT_CONFIG_*` → `http.extraHeader`, никогда argv/URL — иначе утечёт в `.git/config`/reflog/
`/proc/<pid>/cmdline`), `gh` получает токен через `GH_TOKEN` env. Писабельный клон — отдельный
от read-only `repos/<pid>` (индексация) каталог `worktrees/<pid>`, удаляется после PR. Push —
строго явным refspec на новую ветку `jworkplace/<slug>`, без `--force/--all/--mirror`; в
default-ветку (main/master) не пишем никогда. Все git/gh stderr и PR title/body (могут
содержать LLM-генерируемый текст) проходят `redact` перед логом/исключением/API-вызовом.
"""
import base64
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

import httpx

from app.chat.grounding import redact
from app.config import Settings, fernet, get_settings
from app.indexing.validation import RepoRef

# --- шифрование PAT at rest ---


def encrypt_token(settings: Settings, token: str) -> bytes:
    return fernet(settings).encrypt(token.encode("utf-8"))


def decrypt_token(settings: Settings, enc: bytes) -> str:
    return fernet(settings).decrypt(enc).decode("utf-8")


# --- валидация PAT против конкретного репозитория ---


async def validate_token(ref: RepoRef, token: str) -> bool:
    """Один `GET /repos/{owner}/{repo}` с `Bearer`. Принимаем ⟺ у токена есть `push` НА ЭТОТ
    репозиторий (`permissions.push`) И ответ действительно про него (`full_name` совпадает —
    fine-grained PAT, выданный на другой репо тем же токеном-подобным значением, не пройдёт).
    `owner/repo` берём из `ref` (провалидирован при подключении проекта), не из пользовательского
    ввода. Любая сетевая/протокольная ошибка → False (fail-closed), токен и тело не логируем.

    Оговорка (best-effort): `permissions.push` отражает РОЛЬ аккаунта в репо, а не гранулярный scope
    fine-grained PAT. Владелец репо с токеном без Contents/Pull-requests:write всё равно даст push=true
    здесь, но реальный `git push`/`gh pr create` в open_pr упадёт → PR не создастся (fail-closed).
    Поэтому это отсев заведомо непригодных токенов, а не гарантия успеха PR. Полную гарантию даёт
    только фактический push; надёжного API для чтения scope fine-grained токена GitHub не даёт."""
    api = f"https://api.github.com/repos/{ref.owner}/{ref.repo}"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                api,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if resp.status_code != 200:
            return False
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return False
    full_name = str(data.get("full_name", ""))
    can_push = bool((data.get("permissions") or {}).get("push"))
    return can_push and full_name.lower() == ref.name.lower()


# --- git env-hardening (как indexing/clone.py) ---

_GIT_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "/bin/true",
    "GIT_CONFIG_NOSYSTEM": "1",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}
_GIT_HARDENING = [
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.ext.allow=never",
    "-c", "protocol.file.allow=never",
    "-c", "http.followRedirects=false",
]
_CLONE_FLAGS = ["--depth", "1", "--single-branch", "--no-tags", *_GIT_HARDENING]

# Бот-identity коммита — не светим личные данные пользователя, не зависим от global git config.
_BOT_NAME = "jWorkPlace"
_BOT_EMAIL = "noreply@jorchik.com"


class GithubError(RuntimeError):
    """PR-флоу не удался (клон/apply/push/gh). Сообщение уже redacted — безопасно для клиента."""


def _token_git_env(token: str) -> dict:
    """PAT — ТОЛЬКО через `http.extraHeader` в env (must-fix #3): не argv, не URL. `GIT_CONFIG_*`
    добавляет анонимный config-override поверх `_GIT_ENV`, не трогая `.git/config` на диске."""
    basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    return {
        **_GIT_ENV,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraHeader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
    }


def _run(cmd: list[str], *, env: dict, cwd: Path | None = None, input_: str | None = None, timeout: int = 60) -> str:
    """Обёртка subprocess.run с redacted-ошибкой (никогда сырой stderr в исключении/логе)."""
    try:
        result = subprocess.run(
            cmd, cwd=cwd, env=env, input=input_, capture_output=True, text=True,
            timeout=timeout, check=True,
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        raise GithubError(f"таймаут: {' '.join(cmd[:2])}")
    except subprocess.CalledProcessError as exc:
        detail = redact((exc.stderr or "").strip()).splitlines()[-1:] or ["сбой команды"]
        raise GithubError(f"{cmd[1] if len(cmd) > 1 else cmd[0]}: {detail[0]}")


# --- транслитерация + slug ветки ---

_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
    "я": "ya",
}
_SLUG_NONALNUM = re.compile(r"[^a-z0-9]+")
_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")


def _transliterate(text: str) -> str:
    out = []
    for ch in text.lower():
        if ch in _CYRILLIC:
            out.append(_CYRILLIC[ch])
        else:
            out.append(unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode("ascii") or ch)
    return "".join(out)


def _slugify(text: str) -> str:
    slug = _SLUG_NONALNUM.sub("-", _transliterate(text)).strip("-")
    return slug[:40].strip("-")


def _branch_slug(summary: str, instruction: str) -> str:
    """`^[a-z0-9-]{1,40}$` из summary/instruction (транслит кириллицы); нет пригодного текста
    (пусто/неалфавитный ввод) → детерминированный fallback по времени (must-fix #5)."""
    for candidate in (summary, instruction):
        slug = _slugify(candidate or "")
        if slug and _SLUG_RE.match(slug):
            return slug
    return f"edit-{int(time.time())}"


def _commit_message(summary: str, instruction: str) -> str:
    title = redact((summary or "").strip()).splitlines()[:1]
    title_text = title[0].strip() if title else ""
    if not title_text:
        title_text = redact((instruction or "").strip())[:72].splitlines()[0] or "Правка jWorkPlace"
    body = redact((instruction or "").strip())
    return f"{title_text}\n\n{body}" if body and body != title_text else title_text


# --- writable-клон + PR ---


def open_pr(project_id: str, ref: RepoRef, token: str, diff: str, summary: str, instruction: str) -> str:
    """Открыть PR из свежей рабочей ветки. Порядок (must-fix #4-6):

    1. писабельный клон default-ветки в `worktrees/<pid>` (0700, БЕЗ `--filter=blob:none` — партиал-
       клон не гарантирует все блобы на диске для `git apply`/`commit`), удаляется в `finally`;
    2. `git apply` уже серверно-собранного и сверенного diff (не `--check` — реально применяем);
    3. новая ветка `jworkplace/<slug>` от default; коммит от имени бота (`--no-verify`);
    4. push явным refspec `HEAD:refs/heads/jworkplace/<slug>` (никогда `--force/--all/--mirror`,
       никогда в default-ветку);
    5. `gh pr create` (`GH_TOKEN` env) → URL PR.

    Индекс-клон `repos/<pid>` (read-only, используется для чата/поиска) не трогаем.
    """
    settings = get_settings()
    dest = settings.worktrees_dir / project_id
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Каталог создаём с 0700 ДО клонирования: чужие файлы не мелькают с umask-правами в окне
    # между `git clone` и `chmod` (клон идёт в уже существующий пустой каталог).
    dest.mkdir(mode=0o700)
    dest.chmod(0o700)  # гарантия против umask, срезающего биты у mkdir

    token_env = _token_git_env(token)
    try:
        _run(["git", "clone", *_CLONE_FLAGS, ref.url, str(dest)], env=token_env, timeout=settings.clone_timeout_s)

        default_branch = _run(
            ["git", "-C", str(dest), "symbolic-ref", "--short", "HEAD"], env=_GIT_ENV, timeout=15
        ).strip()

        if not diff.strip():
            raise GithubError("пустой diff — нечего применять")
        _run(["git", "-C", str(dest), *_GIT_HARDENING, "apply", "-"], env=_GIT_ENV, input_=diff, timeout=30)

        slug = _branch_slug(summary, instruction)
        branch = f"jworkplace/{slug}"
        _run(["git", "-C", str(dest), "checkout", "-b", branch], env=_GIT_ENV, timeout=15)
        _run(["git", "-C", str(dest), "add", "-A"], env=_GIT_ENV, timeout=30)

        commit_env = {
            **_GIT_ENV,
            "GIT_AUTHOR_NAME": _BOT_NAME, "GIT_AUTHOR_EMAIL": _BOT_EMAIL,
            "GIT_COMMITTER_NAME": _BOT_NAME, "GIT_COMMITTER_EMAIL": _BOT_EMAIL,
        }
        message = _commit_message(summary, instruction)
        _run(
            ["git", "-C", str(dest), *_GIT_HARDENING, "commit", "--no-verify", "--file", "-"],
            env=commit_env, input_=message, timeout=30,
        )

        # Явный refspec на новую ветку — гарантия против случайного push в default (must-fix #5).
        _run(
            ["git", "-C", str(dest), *_GIT_HARDENING, "push", "origin", f"HEAD:refs/heads/{branch}"],
            env=token_env, timeout=settings.clone_timeout_s,
        )

        # gh (в отличие от git) читает свой конфиг из $HOME/$XDG_CONFIG_HOME — без HOME падает
        # на старте ещё до авторизации; сам токен передаём отдельно через GH_TOKEN, не отсюда.
        gh_env = {
            "PATH": _GIT_ENV["PATH"],
            "HOME": os.environ.get("HOME", "/root"),
            "GH_TOKEN": token,
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
        }
        title, _, body = message.partition("\n\n")
        stdout = _run(
            [
                "gh", "pr", "create",
                "--repo", ref.name,
                "--base", default_branch,
                "--head", branch,
                "--title", title or "Правка jWorkPlace",
                "--body", body or title,
            ],
            env=gh_env, cwd=dest, timeout=60,
        )
        lines = [ln.strip() for ln in stdout.strip().splitlines() if ln.strip()]
        if not lines:
            raise GithubError("gh pr create не вернул ссылку на PR")
        return lines[-1]
    finally:
        shutil.rmtree(dest, ignore_errors=True)
