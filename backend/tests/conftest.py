"""Общие фикстуры: изолированный $JWP_DATA_DIR на временном каталоге для каждого теста."""
import pytest

from app import db
from app.config import get_settings


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Указать JWP_DATA_DIR на tmp и инициализировать чистую БД. Сбрасывает кэш настроек."""
    monkeypatch.setenv("JWP_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    db.init_db()
    yield tmp_path
    get_settings.cache_clear()
