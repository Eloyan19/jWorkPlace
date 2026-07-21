"""Прогресс индексации: колбэк в embed_documents + поля progress_* в DTO проекта."""
import numpy as np
from starlette.testclient import TestClient

from app import db
from app.indexing import embeddings
from app.main import create_app


def test_embed_documents_reports_progress(data_dir, monkeypatch):
    # Мокаем сетевой вызов Ollama — детерминированный вектор, без сети.
    fake = np.ones(embeddings.EMBED_DIM, dtype="float32")
    monkeypatch.setattr(embeddings, "_embed_call", lambda client, text: fake)

    seen: list[int] = []
    _vecs, kept = embeddings.embed_documents(["", "", ""], ["a", "b", "c"], progress_cb=seen.append)

    assert kept == [0, 1, 2]
    assert 0 in seen              # прогресс начался с нуля
    assert seen[-1] == 3          # финальный тик = число чанков


def test_projects_dto_includes_progress(data_dir):
    db.create_project("prog1", "u", "n", db.STATUS_INDEXING)
    db.set_progress("prog1", 5, 20)

    body = TestClient(create_app()).get("/api/projects").json()
    p = next(x for x in body if x["id"] == "prog1")
    assert p["progress_done"] == 5
    assert p["progress_total"] == 20
    assert "github_token_enc" not in p          # токен по-прежнему не утекает
