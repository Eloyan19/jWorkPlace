"""Сборка DTO базы знаний из БД (Этап P1) — чистая функция, без LLM/сети/блокирующих вызовов
за пределами SQLite (вызывающий эндпоинт оборачивает в `to_thread`).

Разбиение концептов на `known`/`new` — персонализация: уже известные показываем одной строкой
(имя), новые — с полным раскрытием (`detail`) и цитатой-доказательством.
"""
import json

from app import db


def render(project_id: str) -> dict:
    """DTO `{status:"ready", overview, tech, concepts:{new, known}}` либо `{status:"error", ...}`,
    если выжимки ещё нет (вызывающий код обычно уже гарантирует её наличие перед вызовом)."""
    summary = db.get_summary(project_id)
    if summary is None:
        return {"status": "error", "reason": "no_summary"}

    new_concepts = []
    known_concepts = []
    for row in db.get_project_concepts(project_id):
        if row["known"]:
            known_concepts.append({"name": row["name"]})
            continue
        evidence = []
        if row["evidence"]:
            try:
                evidence = json.loads(row["evidence"])
            except (json.JSONDecodeError, TypeError):
                evidence = []
        new_concepts.append({"name": row["name"], "detail": row["detail"], "evidence": evidence})

    try:
        tech = json.loads(summary["tech"]) if summary["tech"] else []
    except (json.JSONDecodeError, TypeError):
        tech = []

    return {
        "status": "ready",
        "overview": summary["overview"],
        "tech": tech,
        "concepts": {"new": new_concepts, "known": known_concepts},
    }
