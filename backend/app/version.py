"""Версия сборки для /api/health.

Приоритет: env GIT_SHA (проставляется на деплое, см. deploy/README.md) ->
`git rev-parse --short HEAD` (локальная разработка) -> "dev" (git недоступен).
"""
import os
import subprocess
from functools import lru_cache


@lru_cache
def get_version() -> str:
    env_sha = os.environ.get("GIT_SHA", "").strip()
    if env_sha:
        return env_sha

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        sha = result.stdout.strip()
        if sha:
            return sha
    except (subprocess.SubprocessError, OSError, FileNotFoundError):
        pass

    return "dev"
