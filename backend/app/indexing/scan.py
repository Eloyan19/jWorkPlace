"""Скан рабочего дерева до индексации: фильтры файлов + скан секретов (gitleaks).

Инвариант безопасности (дизайн security-auditor + rag-indexing-engineer): скан секретов
ГЕЙТИТ до эмбеддинга. Файлы с чувствительными именами исключаются целиком; строки с находками
gitleaks вырезаются на этапе чанкинга (см. secret_ranges). Отфильтрованное не попадает ни в
files-как-индексируемое, ни в chunks/embed_cache, ни в логи (логируем только file:line + правило).
"""
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings
from app.indexing.langs import lang_for

logger = logging.getLogger("jworkplace.scan")

# Каталоги, которые не обходим (vendored/сборка/служебные).
_SKIP_DIRS = {
    ".git", "node_modules", "vendor", "dist", "build", "target", "__pycache__",
    ".venv", "venv", ".next", ".nuxt", ".idea", ".vscode", "bower_components",
    "third_party", ".gradle", ".mvn", "Pods", ".terraform",
}
# Имена/суффиксы vendored-файлов (шум, не код проекта).
_VENDORED_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "cargo.lock",
    "poetry.lock", "composer.lock", "gemfile.lock", "go.sum",
    "uv.lock", "pipfile.lock", "flake.lock", "packages.lock.json",
    "podfile.lock", "mix.lock", "bun.lockb",
}
# Чувствительные имена — исключаем ЦЕЛИКОМ (секреты не должны попасть в индекс).
_SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".crt")
_SENSITIVE_PREFIXES = ("id_rsa", "id_ed25519", "id_dsa")
_SENSITIVE_EXACT = {".npmrc", "credentials", ".netrc", ".pgpass"}
# Бинарные/медиа-суффиксы — отсекаем до чтения содержимого.
_BINARY_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg", ".webp", ".pdf",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".rar", ".7z", ".jar", ".war",
    ".mp3", ".mp4", ".wav", ".mov", ".avi", ".mkv", ".webm", ".flac",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a", ".class", ".wasm",
    ".pyc", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)


class ScanError(RuntimeError):
    """Скан не удался (репо превысил лимиты)."""


@dataclass
class ScanResult:
    file_rows: list[dict] = field(default_factory=list)
    # path -> список диапазонов (start_line, end_line) с секретами (1-based, включительно).
    secret_ranges: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


def _is_sensitive(name: str) -> bool:
    low = name.lower()
    if low in _SENSITIVE_EXACT:
        return True
    if low.startswith(".env"):
        return True
    if any(low.startswith(p) for p in _SENSITIVE_PREFIXES):
        return True
    return any(low.endswith(s) for s in _SENSITIVE_SUFFIXES)


def _is_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return True


def _blob_shas(repo_dir: Path) -> dict[str, str]:
    """path -> git blob sha (по HEAD-дереву). Стабильный ключ для инкрементальности/кэша."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "ls-tree", "-r", "HEAD"],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return {}
    mapping: dict[str, str] = {}
    for line in out.splitlines():
        # формат: "<mode> blob <sha>\t<path>"
        meta, _, path = line.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            mapping[path] = parts[2]
    return mapping


def scan_repo(repo_dir: Path) -> ScanResult:
    """Обойти рабочее дерево, применить фильтры, просканировать секреты. Записать file_rows."""
    settings = get_settings()
    blob_shas = _blob_shas(repo_dir)
    result = ScanResult()
    total_bytes = 0
    count = 0

    for path in sorted(repo_dir.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(repo_dir).as_posix()
        parts = rel.split("/")
        if any(p in _SKIP_DIRS for p in parts[:-1]):
            continue
        name = parts[-1]
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total_bytes += size
        count += 1
        if count > settings.max_files:
            raise ScanError(f"Слишком много файлов (>{settings.max_files}).")
        if total_bytes > settings.max_repo_mb * 1024 * 1024:
            raise ScanError(f"Рабочее дерево превысило {settings.max_repo_mb} МБ.")

        row = {
            "path": rel,
            "blob_sha": blob_shas.get(rel, ""),
            "lang": lang_for(rel),
            "size": size,
            "is_binary": 0,
            "is_vendored": 0,
            "excluded": 0,
        }
        if _is_sensitive(name):
            row["excluded"] = 1
        elif name.lower() in _VENDORED_NAMES:
            row["is_vendored"] = 1
        elif rel.lower().endswith(_BINARY_SUFFIXES):
            row["is_binary"] = 1
        elif size > settings.max_file_bytes:
            row["is_binary"] = 1  # трактуем как непригодный к индексации (слишком большой)
        elif _is_binary(path):
            row["is_binary"] = 1
        else:
            # текстовый кандидат — добьём проверкой числа строк
            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").count("\n") + 1
                if lines > settings.max_file_lines:
                    row["is_vendored"] = 1  # сгенерированный/минифицированный — не в индекс
            except OSError:
                row["is_binary"] = 1

        # blob_sha без git ls-tree (например shallow без HEAD-дерева) — считаем сами по содержимому
        if not row["blob_sha"] and not row["is_binary"] and not row["excluded"]:
            row["blob_sha"] = _git_hash_object(repo_dir, rel)
        result.file_rows.append(row)

    result.secret_ranges = _gitleaks_scan(repo_dir)
    return result


def _git_hash_object(repo_dir: Path, rel: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_dir), "hash-object", rel],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def _gitleaks_scan(repo_dir: Path) -> dict[str, list[tuple[int, int]]]:
    """gitleaks detect --no-git по рабочему дереву. Возвращает path -> диапазоны строк с секретами.

    Fail-closed: без бинаря gitleaks (при settings.require_gitleaks) индексацию прерываем —
    иначе секреты чужого репо проскочат по содержимому. Значения секретов НИКОГДА не логируем.
    """
    if shutil.which("gitleaks") is None:
        if get_settings().require_gitleaks:
            raise ScanError("gitleaks не установлен — скан секретов невозможен (fail-closed).")
        logger.warning("gitleaks не найден, require_gitleaks=False — скан по содержимому пропущен")
        return {}
    repo_abs = repo_dir.resolve()
    ranges: dict[str, list[tuple[int, int]]] = {}
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tmp:
        report = tmp.name
    try:
        subprocess.run(
            ["gitleaks", "detect", "--no-git", "--no-banner",
             "--source", str(repo_abs), "--report-format", "json", "--report-path", report],
            capture_output=True, text=True, timeout=180, check=False,  # exit 1 = находки есть, это норма
        )
        try:
            findings = json.loads(Path(report).read_text() or "[]")
        except (json.JSONDecodeError, OSError):
            findings = []
        for f in findings:
            raw = f.get("File", "")
            if not raw:
                continue
            # Приводим путь находки к репо-относительному (тот же ключ, что path в chunker).
            # Если путь не разрешается внутрь репо — прерываем: молча потерять диапазон
            # секрета = fail-open, недопустимо.
            try:
                rel = (repo_abs / raw).resolve().relative_to(repo_abs).as_posix() if not Path(raw).is_absolute() \
                    else Path(raw).resolve().relative_to(repo_abs).as_posix()
            except ValueError:
                raise ScanError(f"gitleaks вернул путь вне репозитория ({raw}) — прерываю ради безопасности.")
            start = int(f.get("StartLine", 1))       # gitleaks 1-based (совпадает с нашими start_line)
            end = int(f.get("EndLine", f.get("StartLine", 1)))
            ranges.setdefault(rel, []).append((start, end))
            logger.info("gitleaks: секрет в %s:%d (%s)", rel, start, f.get("RuleID", "?"))
    finally:
        Path(report).unlink(missing_ok=True)
    return ranges
