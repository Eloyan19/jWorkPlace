"""Тесты базы знаний (генератор + каскад дедупа + рендер): сбор материала, fail-closed
валидация (evidence/redact), каскад matching (slug/эмбеддинг/серая зона→судья), персист,
рендер known/new. LLM и `embeddings.embed_query` мокаются (без сети); чтение файлов и SQLite —
настоящие через изолированный `data_dir`. Секреты в тестах — синтетические.
"""
import asyncio
import json

import numpy as np

from app import db
from app.chat import grounding
from app.config import get_settings
from app.indexing import embeddings
from app.knowledge import generator, matching, render

PID = "abc123def456"


def _project_ready(head_sha: str = "sha1") -> None:
    db.create_project(PID, "https://github.com/o/r", "o/r", db.STATUS_READY)
    db.set_head_sha(PID, head_sha)


def _write_repo_file(rel_path: str, content: str) -> None:
    path = get_settings().repos_dir / PID / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _no_hybrid(monkeypatch) -> None:
    """Пробы hybrid_search не нужны большинству тестов — материал и так собран вручную."""
    monkeypatch.setattr(generator.hybrid, "hybrid_search", lambda pid, q, k: [])


class _ScriptedLlm:
    """Мок LlmService: отдаёт заготовленные ответы по порядку вызовов (см. test_chat.py)."""

    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls = 0
        self.last_messages: list[dict] | None = None

    async def chat(self, messages, *, response_format=None, temperature=0.0, max_tokens=1024):
        self.calls += 1
        self.last_messages = messages
        return self._replies[min(self.calls - 1, len(self._replies) - 1)]


# --- collect_material ---


def test_collect_material_includes_manifest_readme_and_skeleton(data_dir, monkeypatch):
    _project_ready()
    _no_hybrid(monkeypatch)
    db.replace_files(PID, [
        {"path": "requirements.txt", "blob_sha": "s1", "lang": None, "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
        {"path": "README.md", "blob_sha": "s2", "lang": None, "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
    ])
    _write_repo_file("requirements.txt", "fastapi==0.100.0\n")
    _write_repo_file("README.md", "# Demo\nDemo project using FastAPI.\n")

    material = generator.collect_material(PID)
    files = {m["file"] for m in material}
    assert "requirements.txt" in files
    assert "README.md" in files
    assert "<структура-репозитория>" in files


def test_collect_material_skips_excluded_manifest(data_dir, monkeypatch):
    """Файлы, помеченные gitleaks (excluded=1), не читаются в материал выжимки — даже манифест."""
    _project_ready()
    _no_hybrid(monkeypatch)
    db.replace_files(PID, [
        {"path": "requirements.txt", "blob_sha": "s1", "lang": None, "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 1},
    ])
    _write_repo_file("requirements.txt", "fastapi==0.100.0\n")

    material = generator.collect_material(PID)
    assert "requirements.txt" not in {m["file"] for m in material}


def test_collect_material_dedups_by_file_and_start_line(data_dir, monkeypatch):
    _project_ready()
    db.replace_files(PID, [])
    monkeypatch.setattr(
        generator.hybrid, "hybrid_search",
        lambda pid, q, k: [{"file": "src/a.py", "symbol": "f", "lang": "python",
                             "start_line": 1, "end_line": 2, "text": "def f(): pass",
                             "citation": "src/a.py::f::L1-2"}] * 2,
    )
    material = generator.collect_material(PID)
    keys = [(m["file"], m["start_line"]) for m in material]
    assert len(keys) == len(set(keys))


# --- _validate_concept (fail-closed) ---


def test_validate_concept_drops_without_valid_evidence(data_dir):
    _project_ready()
    _write_repo_file("src/a.py", "x = 1\n")
    material = [{"file": "src/a.py", "symbol": None, "lang": "python",
                 "start_line": 1, "end_line": 1, "text": "x = 1"}]
    item = {
        "slug": "foo", "name": "Foo", "category": "pattern", "description": "d",
        "evidence": [{"id": 1, "quote": "does not exist in file"}],
    }
    assert generator._validate_concept(item, material, PID) is None


def test_validate_concept_accepts_valid_code_evidence(data_dir):
    _project_ready()
    _write_repo_file("src/a.py", "def foo():\n    pass\n")
    material = [{"file": "src/a.py", "symbol": "foo", "lang": "python",
                 "start_line": 1, "end_line": 2, "text": "def foo():\n    pass"}]
    item = {
        "slug": "foo-func", "name": "Foo", "category": "pattern", "description": "d",
        "evidence": [{"id": 1, "quote": "def foo():"}],
    }
    concept = generator._validate_concept(item, material, PID)
    assert concept is not None
    assert concept["evidence"][0]["quote"] == "def foo():"
    assert concept["evidence"][0]["citation"] == "src/a.py::foo::L1-2"


def test_validate_concept_redacts_secret_in_description(data_dir):
    """Третий барьер: LLM могла процитировать/сформулировать секрет в description — redact
    на выходе перед записью, даже если материал на входе уже был redact'нут."""
    _project_ready()
    _write_repo_file("src/a.py", "def foo():\n    pass\n")
    material = [{"file": "src/a.py", "symbol": "foo", "lang": "python",
                 "start_line": 1, "end_line": 2, "text": "def foo():\n    pass"}]
    item = {
        "slug": "foo-func", "name": "Foo", "category": "pattern",
        "description": "Конфиг содержит api_key=abcd1234efgh5678ijkl9012mnop3456",
        "evidence": [{"id": 1, "quote": "def foo():"}],
    }
    concept = generator._validate_concept(item, material, PID)
    assert concept is not None
    assert "abcd1234efgh5678ijkl9012mnop3456" not in concept["description"]
    assert "[REDACTED]" in concept["description"]


def test_first_valid_evidence_quote_is_redacted_and_bounded(data_dir):
    """Инвариант (защита от будущего рефактора, SecReview): evidence.quote всегда проходит
    redact() и никогда не длиннее _MAX_QUOTE_LEN, даже если файл содержит секрет прямо внутри
    дословно совпадающей цитаты."""
    secret = "abcd1234efgh5678ijkl9012mnop3456"
    _project_ready()
    _write_repo_file("src/a.py", f"TOKEN = '{secret}'\n")
    excerpt = grounding.redact(f"TOKEN = '{secret}'\n")  # то, что реально увидит модель
    material = [{"file": "src/a.py", "symbol": None, "lang": "python",
                 "start_line": 1, "end_line": 1, "text": excerpt}]
    item = {
        "slug": "token-const", "name": "Token", "category": "pattern", "description": "d",
        "evidence": [{"id": 1, "quote": excerpt.strip()}],
    }
    concept = generator._validate_concept(item, material, PID)
    assert concept is not None
    quote = concept["evidence"][0]["quote"]
    assert secret not in quote
    assert len(quote) <= generator._MAX_QUOTE_LEN


def test_validate_concept_rejects_bad_category(data_dir):
    _project_ready()
    _write_repo_file("src/a.py", "def foo():\n    pass\n")
    material = [{"file": "src/a.py", "symbol": "foo", "lang": "python",
                 "start_line": 1, "end_line": 2, "text": "def foo():\n    pass"}]
    item = {
        "slug": "foo-func", "name": "Foo", "category": "marketing-buzzword",
        "description": "d", "evidence": [{"id": 1, "quote": "def foo():"}],
    }
    assert generator._validate_concept(item, material, PID) is None


def test_validate_concept_accepts_evidence_from_synthetic_skeleton_block(data_dir):
    """Концепт, цитирующий ТОЛЬКО синтетический скелет-блок (структура/языки репо, не файл на
    диске) — должен проходить валидацию, а не молча дропаться (Fix A)."""
    _project_ready()
    skeleton = generator._skeleton_material([
        {"path": "main.py", "lang": "python", "symbols": []},
    ])
    assert skeleton["synthetic"] is True
    material = [skeleton]
    item = {
        "slug": "python-lang", "name": "Python", "category": "technology",
        "description": "Проект написан на Python.",
        "evidence": [{"id": 1, "quote": "Языки: python (1)"}],
    }
    concept = generator._validate_concept(item, material, PID)
    assert concept is not None
    assert concept["evidence"][0]["quote"] == "Языки: python (1)"


def test_validate_concept_drops_evidence_quote_over_max_len(data_dir):
    """Fix C (SecReview L-1): evidence с quote длиннее _MAX_QUOTE_LEN отбрасывается, не
    сохраняется в БД."""
    _project_ready()
    long_line = "x" * (generator._MAX_QUOTE_LEN + 1)
    _write_repo_file("src/a.py", f"{long_line}\n")
    material = [{"file": "src/a.py", "symbol": None, "lang": "python",
                 "start_line": 1, "end_line": 1, "text": long_line}]
    item = {
        "slug": "long-quote", "name": "Long", "category": "pattern", "description": "d",
        "evidence": [{"id": 1, "quote": long_line}],
    }
    assert generator._validate_concept(item, material, PID) is None


def test_validate_concept_rejects_bad_slug(data_dir):
    _project_ready()
    _write_repo_file("src/a.py", "def foo():\n    pass\n")
    material = [{"file": "src/a.py", "symbol": "foo", "lang": "python",
                 "start_line": 1, "end_line": 2, "text": "def foo():\n    pass"}]
    item = {
        "slug": "Not A Slug!", "name": "Foo", "category": "pattern",
        "description": "d", "evidence": [{"id": 1, "quote": "def foo():"}],
    }
    assert generator._validate_concept(item, material, PID) is None


# --- matching cascade ---


def _vec(a: float, b: float) -> np.ndarray:
    v = np.zeros(embeddings.EMBED_DIM, dtype="float32")
    v[0] = a
    v[1] = b
    return v


def test_match_exact_slug_no_embedding_call(monkeypatch):
    def _must_not_be_called(text):
        raise AssertionError("embed_query не должен вызываться при точном совпадении slug")
    monkeypatch.setattr(embeddings, "embed_query", _must_not_be_called)

    catalog = [{"id": 1, "slug": "fastapi", "name": "FastAPI", "description": "d",
                "known": 1, "embedding": None}]
    result = matching.match("fastapi", "FastAPI", "d2", catalog)
    assert result == {
        "status": "exact", "concept_id": 1, "known": True,
        "embedding": None, "gray_candidate_slug": None,
    }


def test_match_embedding_threshold(monkeypatch):
    catalog = [{"id": 2, "slug": "known-thing", "name": "Known", "description": "d",
                "known": 0, "embedding": _vec(1.0, 0.0).tobytes()}]
    monkeypatch.setattr(embeddings, "embed_query", lambda text: _vec(0.9, np.sqrt(1 - 0.81)))
    result = matching.match("new-slug", "New", "desc", catalog)
    assert result["status"] == "embed"
    assert result["concept_id"] == 2
    assert result["known"] is False


def test_match_gray_zone(monkeypatch):
    catalog = [{"id": 3, "slug": "known-thing", "name": "Known", "description": "d",
                "known": 0, "embedding": _vec(1.0, 0.0).tobytes()}]
    monkeypatch.setattr(embeddings, "embed_query", lambda text: _vec(0.8, 0.6))
    result = matching.match("new-slug", "New", "desc", catalog)
    assert result["status"] == "gray"
    assert result["concept_id"] is None
    assert result["gray_candidate_slug"] == "known-thing"


def test_match_new_below_gray_zone(monkeypatch):
    catalog = [{"id": 4, "slug": "known-thing", "name": "Known", "description": "d",
                "known": 0, "embedding": _vec(1.0, 0.0).tobytes()}]
    monkeypatch.setattr(embeddings, "embed_query", lambda text: _vec(0.0, 1.0))
    result = matching.match("new-slug", "New", "desc", catalog)
    assert result["status"] == "new"
    assert result["concept_id"] is None


def test_match_new_on_empty_catalog(monkeypatch):
    monkeypatch.setattr(embeddings, "embed_query", lambda text: _vec(1.0, 0.0))
    result = matching.match("only-slug", "Only", "desc", [])
    assert result["status"] == "new"


def test_judge_gray_zone_fail_closed_on_bad_json():
    class _BadLlm:
        async def chat(self, *a, **k):
            return "не json совсем"

    result = asyncio.run(matching.judge_gray_zone(_BadLlm(), [("a", "da", "b", "db")]))
    assert result == {}


def test_judge_gray_zone_parses_decisions():
    class _Llm:
        async def chat(self, *a, **k):
            return json.dumps({"decisions": [{"new_slug": "a", "known_slug": "b", "same": True}]})

    result = asyncio.run(matching.judge_gray_zone(_Llm(), [("a", "da", "b", "db")]))
    assert result == {"a": True}


def test_judge_gray_zone_parses_markdown_wrapped_response():
    """Fix B: DeepSeek иногда оборачивает JSON в markdown-код-блок несмотря на
    response_format=json_object — толерантный парс (_loads_tolerant) должен это пережить."""
    class _Llm:
        async def chat(self, *a, **k):
            payload = json.dumps({"decisions": [{"new_slug": "a", "known_slug": "b", "same": True}]})
            return f"```json\n{payload}\n```"

    result = asyncio.run(matching.judge_gray_zone(_Llm(), [("a", "da", "b", "db")]))
    assert result == {"a": True}


def test_judge_gray_zone_wraps_descriptions_in_nonce_delimiters():
    """Fix B (SecReview M-1): описания идут судье в нонс-делимитерах, не голым {!r}-текстом —
    контент репо не может текстово подделать границу блока."""
    captured = {}

    class _Llm:
        async def chat(self, messages, *a, **k):
            captured["messages"] = messages
            return json.dumps({"decisions": [{"new_slug": "a", "known_slug": "b", "same": True}]})

    asyncio.run(matching.judge_gray_zone(_Llm(), [("a", "desc-new", "b", "desc-known")]))

    user_content = captured["messages"][1]["content"]
    assert "<<<CODE nonce=" in user_content
    assert "CODE nonce=" in user_content and ">>>" in user_content
    assert "desc-new" in user_content
    assert "desc-known" in user_content
    # slug'и не обёрнуты в делимитеры (детерминированные, не из репо)
    assert "new_slug='a'" in user_content


def test_judge_gray_zone_empty_pairs_skips_llm_call():
    class _MustNotBeCalled:
        async def chat(self, *a, **k):
            raise AssertionError("судья не должен вызываться без серых пар")

    result = asyncio.run(matching.judge_gray_zone(_MustNotBeCalled(), []))
    assert result == {}


# --- generate(): оркестрация целиком ---


def test_generate_happy_path_new_technology_concept(data_dir, monkeypatch):
    _project_ready(head_sha="sha1")
    db.replace_files(PID, [
        {"path": "README.md", "blob_sha": "s1", "lang": None, "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
    ])
    _write_repo_file("README.md", "# Demo\nDemo project using FastAPI.\n")
    _no_hybrid(monkeypatch)

    reply = json.dumps({
        "overview": "Демо-проект на FastAPI.",
        "concepts": [{
            "slug": "fastapi", "name": "FastAPI", "category": "technology",
            "description": "Веб-фреймворк для API.",
            "evidence": [{"id": 2, "quote": "Demo project using FastAPI."}],
        }],
    })
    fake_llm = _ScriptedLlm([reply])
    monkeypatch.setattr(generator, "get_llm", lambda settings: fake_llm)

    result = asyncio.run(generator.generate(PID))
    assert result == {"ok": True}
    assert fake_llm.calls == 1  # серой зоны нет -> судья не вызывается

    summary = db.get_summary(PID)
    assert summary["head_sha"] == "sha1"
    assert summary["overview"] == "Демо-проект на FastAPI."
    assert json.loads(summary["tech"]) == ["FastAPI"]

    concepts = db.get_project_concepts(PID)
    assert len(concepts) == 1
    assert concepts[0]["name"] == "FastAPI"
    assert concepts[0]["known"] == 0

    catalog = db.catalog_concepts()
    assert len(catalog) == 1
    assert catalog[0]["slug"] == "fastapi"


def test_generate_invalid_json_is_fail_closed(data_dir, monkeypatch):
    _project_ready()
    _no_hybrid(monkeypatch)
    fake_llm = _ScriptedLlm(["точно не json"])
    monkeypatch.setattr(generator, "get_llm", lambda settings: fake_llm)

    result = asyncio.run(generator.generate(PID))
    assert result == {"ok": False, "reason": "invalid_json"}
    assert db.get_summary(PID) is None


def test_generate_invalid_structure_is_fail_closed(data_dir, monkeypatch):
    _project_ready()
    _no_hybrid(monkeypatch)
    fake_llm = _ScriptedLlm([json.dumps({"overview": "x"})])  # нет concepts
    monkeypatch.setattr(generator, "get_llm", lambda settings: fake_llm)

    result = asyncio.run(generator.generate(PID))
    assert result == {"ok": False, "reason": "invalid_structure"}
    assert db.get_summary(PID) is None


def test_generate_llm_failure_is_fail_closed(data_dir, monkeypatch):
    _project_ready()
    _no_hybrid(monkeypatch)

    class _Boom:
        async def chat(self, *a, **k):
            raise generator.LlmError("обрезан по длине дважды подряд")

    monkeypatch.setattr(generator, "get_llm", lambda settings: _Boom())

    result = asyncio.run(generator.generate(PID))
    assert result == {"ok": False, "reason": "generation_failed"}
    assert db.get_summary(PID) is None


def test_generate_no_concepts_without_valid_evidence_still_saves_overview(data_dir, monkeypatch):
    """Overview валиден, но единственный концепт без подтверждаемой цитаты -> concepts пуст,
    но overview всё равно сохраняется (fail-closed — по КОНЦЕПТУ, не по всему ответу)."""
    _project_ready()
    _no_hybrid(monkeypatch)
    reply = json.dumps({
        "overview": "Overview без концептов.",
        "concepts": [{
            "slug": "ghost", "name": "Ghost", "category": "feature", "description": "d",
            "evidence": [{"id": 1, "quote": "выдуманная цитата, которой нет в материале"}],
        }],
    })
    fake_llm = _ScriptedLlm([reply])
    monkeypatch.setattr(generator, "get_llm", lambda settings: fake_llm)

    result = asyncio.run(generator.generate(PID))
    assert result == {"ok": True}
    assert db.get_project_concepts(PID) == []
    assert db.get_summary(PID)["overview"] == "Overview без концептов."


def test_generate_gray_zone_resolved_by_batch_judge(data_dir, monkeypatch):
    """Серая зона (0.75-0.85 косинуса) -> ОДИН батч-вызов судьи разрешает матч на уже
    существующий концепт вместо минтинга дубликата."""
    _project_ready(head_sha="sha1")
    db.replace_files(PID, [
        {"path": "README.md", "blob_sha": "s1", "lang": None, "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
    ])
    _write_repo_file("README.md", "# Demo\nDemo project using FastAPI.\n")
    _no_hybrid(monkeypatch)

    known_emb = _vec(1.0, 0.0)
    cid = db.insert_concept("rest-api", "REST API", "pattern", "уже известный паттерн",
                            known_emb.tobytes(), "other-project")
    monkeypatch.setattr(embeddings, "embed_query", lambda text: _vec(0.8, 0.6))  # серая зона

    reply = json.dumps({
        "overview": "Демо-проект.",
        "concepts": [{
            "slug": "http-api", "name": "HTTP API", "category": "pattern",
            "description": "Похожий паттерн HTTP API.",
            "evidence": [{"id": 2, "quote": "Demo project using FastAPI."}],
        }],
    })
    judge_reply = json.dumps({
        "decisions": [{"new_slug": "http-api", "known_slug": "rest-api", "same": True}]
    })
    fake_llm = _ScriptedLlm([reply, judge_reply])
    monkeypatch.setattr(generator, "get_llm", lambda settings: fake_llm)

    result = asyncio.run(generator.generate(PID))
    assert result == {"ok": True}
    assert fake_llm.calls == 2  # генерация + один батч-вызов судьи

    concepts = db.get_project_concepts(PID)
    assert len(concepts) == 1
    assert concepts[0]["concept_id"] == cid  # смэтчилось на существующий, не дубликат

    catalog = db.catalog_concepts()
    assert len(catalog) == 1  # новый концепт НЕ заминчен


def test_generate_gray_zone_rejected_by_judge_mints_new_concept(data_dir, monkeypatch):
    _project_ready(head_sha="sha1")
    db.replace_files(PID, [
        {"path": "README.md", "blob_sha": "s1", "lang": None, "size": 10,
         "is_binary": 0, "is_vendored": 0, "excluded": 0},
    ])
    _write_repo_file("README.md", "# Demo\nDemo project using FastAPI.\n")
    _no_hybrid(monkeypatch)

    known_emb = _vec(1.0, 0.0)
    db.insert_concept("rest-api", "REST API", "pattern", "уже известный паттерн",
                       known_emb.tobytes(), "other-project")
    monkeypatch.setattr(embeddings, "embed_query", lambda text: _vec(0.8, 0.6))

    reply = json.dumps({
        "overview": "Демо-проект.",
        "concepts": [{
            "slug": "http-api", "name": "HTTP API", "category": "pattern",
            "description": "Похожий, но другой паттерн.",
            "evidence": [{"id": 2, "quote": "Demo project using FastAPI."}],
        }],
    })
    judge_reply = json.dumps({
        "decisions": [{"new_slug": "http-api", "known_slug": "rest-api", "same": False}]
    })
    fake_llm = _ScriptedLlm([reply, judge_reply])
    monkeypatch.setattr(generator, "get_llm", lambda settings: fake_llm)

    result = asyncio.run(generator.generate(PID))
    assert result == {"ok": True}

    catalog = db.catalog_concepts()
    assert {row["slug"] for row in catalog} == {"rest-api", "http-api"}  # новый заминчен отдельно


# --- db: mark_concepts_known / delete_project ---


def test_mark_concepts_known_idempotent(data_dir):
    _project_ready()
    cid = db.insert_concept("x", "X", "technology", "d", None, PID)
    db.link_project_concept(PID, cid, "detail", None)

    db.mark_concepts_known(PID)
    row = db.get_concept_by_slug("x")
    assert row["known"] == 1
    known_at_1 = row["known_at"]

    db.mark_concepts_known(PID)  # повторно — идемпотентно
    row2 = db.get_concept_by_slug("x")
    assert row2["known_at"] == known_at_1


def test_delete_project_clears_summary_and_links_keeps_global_concept(data_dir):
    _project_ready()
    cid = db.insert_concept("y", "Y", "pattern", "d", None, PID)
    db.link_project_concept(PID, cid, "detail", json.dumps([]))
    db.save_summary(PID, "sha1", "overview", "[]")

    db.delete_project(PID)

    assert db.get_summary(PID) is None
    assert db.get_project_concepts(PID) == []
    assert db.get_concept_by_slug("y") is not None  # глобальный каталог сохранён


# --- render ---


def test_render_splits_new_and_known_across_projects(data_dir):
    """Персонализация: концепт, помеченный known в одном проекте, во ВТОРОМ проекте (где он
    снова встретился) рендерится как «известен», а не «новый»."""
    _project_ready()
    other_pid = "0" * 12
    db.create_project(other_pid, "u2", "n2", db.STATUS_READY)

    cid_known = db.insert_concept("known-thing", "Known Thing", "technology", "d2", None, other_pid)
    db.link_project_concept(other_pid, cid_known, "d2", None)
    db.mark_concepts_known(other_pid)

    cid_new = db.insert_concept("new-thing", "New Thing", "feature", "d1", None, PID)
    db.link_project_concept(PID, cid_new, "раскрытие концепта", json.dumps([{"citation": "a::b::L1-1", "quote": "q"}]))
    db.link_project_concept(PID, cid_known, "d2", None)
    db.save_summary(PID, "sha1", "overview text", json.dumps(["Known Thing"]))

    dto = render.render(PID)
    assert dto["status"] == "ready"
    assert dto["overview"] == "overview text"
    assert dto["tech"] == ["Known Thing"]

    names_new = {c["name"] for c in dto["concepts"]["new"]}
    names_known = {c["name"] for c in dto["concepts"]["known"]}
    assert names_new == {"New Thing"}
    assert names_known == {"Known Thing"}

    new_item = next(c for c in dto["concepts"]["new"] if c["name"] == "New Thing")
    assert new_item["detail"] == "раскрытие концепта"
    assert new_item["evidence"] == [{"citation": "a::b::L1-1", "quote": "q"}]


def test_render_no_summary_returns_error_status(data_dir):
    _project_ready()
    dto = render.render(PID)
    assert dto == {"status": "error", "reason": "no_summary"}
