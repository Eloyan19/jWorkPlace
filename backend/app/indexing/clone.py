"""Безопасное клонирование чужого репо (read-only для индексации).

Инвариант безопасности (дизайн security-auditor): git-хуки чужого репо НЕ исполняем
(core.hooksPath=/dev/null), сторонние протоколы запрещены, редиректы off, кред-промпт не
вешает процесс (GIT_TERMINAL_PROMPT=0). Клон — shallow + blob:none (экономия диска/RAM);
это ломает git blame/log, но для read-only индекса приемлемо (см. CLAUDE.md). Токен GitHub
на этом шаге НЕ передаётся (публичные репо без auth).
"""
import shutil
import subprocess
from pathlib import Path

from app.config import get_settings

# Флаги подтверждены security-auditor. protocol.*.allow=never — чужой репо не утащит нас
# в локальные/ext-протоколы через сабмодули/редиректы.
_CLONE_FLAGS = [
    "--depth", "1",
    "--filter=blob:none",
    "--single-branch",
    "--no-tags",
    "-c", "core.hooksPath=/dev/null",
    "-c", "protocol.ext.allow=never",
    "-c", "protocol.file.allow=never",
    "-c", "http.followRedirects=false",
]
_GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/true", "GIT_CONFIG_NOSYSTEM": "1"}


class CloneError(RuntimeError):
    """Клонирование не удалось (сеть/таймаут/отказ git)."""


def clone_repo(url: str, project_id: str) -> tuple[Path, str]:
    """Склонировать url в $JWP_DATA_DIR/repos/<project_id>/. Возвращает (путь, head_sha).

    Каталог перезаписывается (идемпотентность reclone). Права 0700 — чужие файлы не видны
    другим пользователям машины. При таймауте/ошибке каталог удаляется, бросается CloneError.
    """
    settings = get_settings()
    dest = settings.repos_dir / project_id
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["git", "clone", *_CLONE_FLAGS, url, str(dest)]
    try:
        subprocess.run(
            cmd,
            env={**_GIT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True,
            text=True,
            timeout=settings.clone_timeout_s,
            check=True,
        )
        dest.chmod(0o700)
        head_sha = _rev_parse(dest)
        return dest, head_sha
    except subprocess.TimeoutExpired:
        shutil.rmtree(dest, ignore_errors=True)
        raise CloneError(f"Таймаут клонирования ({settings.clone_timeout_s} с).")
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(dest, ignore_errors=True)
        # stderr git может содержать URL, но не токен (мы его не передаём). Обрезаем на всякий.
        detail = (exc.stderr or "").strip().splitlines()[-1:] or ["git clone failed"]
        raise CloneError(f"Не удалось склонировать: {detail[0]}")


def _rev_parse(repo_dir: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        env=_GIT_ENV, capture_output=True, text=True, timeout=15, check=True,
    )
    return result.stdout.strip()


def pull_repo(project_id: str) -> str:
    """git pull для инкрементального reindex. Возвращает новый head_sha."""
    settings = get_settings()
    dest = settings.repos_dir / project_id
    if not dest.exists():
        raise CloneError("Клон отсутствует — нужен полный reclone.")
    try:
        subprocess.run(
            ["git", "-C", str(dest), *_flatten_config(), "pull", "--depth", "1", "--no-tags"],
            env={**_GIT_ENV, "PATH": "/usr/bin:/bin:/usr/local/bin"},
            capture_output=True, text=True, timeout=settings.clone_timeout_s, check=True,
        )
        return _rev_parse(dest)
    except subprocess.TimeoutExpired:
        raise CloneError(f"Таймаут git pull ({settings.clone_timeout_s} с).")
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()[-1:] or ["git pull failed"]
        raise CloneError(f"Не удалось обновить: {detail[0]}")


def _flatten_config() -> list[str]:
    return [
        "-c", "core.hooksPath=/dev/null",
        "-c", "protocol.ext.allow=never",
        "-c", "protocol.file.allow=never",
        "-c", "http.followRedirects=false",
    ]
