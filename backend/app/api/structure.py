"""Эндпоинт структуры проекта (Задание 1): GET /api/projects/{id}/structure.

Отдаёт дерево индексируемых файлов + символы из БД (`db.project_tree`) — детерминированный вывод
индекса, БЕЗ LLM/RAG. Это мета-запрос о проекте, поэтому он НЕ идёт через `hybrid_search`+abstain-гейт
чата (там бы зарезался: «структура» — не чанк кода). Прямой ответ из индекса.
"""
from fastapi import APIRouter, HTTPException

from app import db

router = APIRouter(prefix="/api/projects")


@router.get("/{project_id}/structure")
def project_structure(project_id: str) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] != db.STATUS_READY:
        raise HTTPException(status_code=409, detail="Проект ещё не готов — дождитесь индексации.")

    tree = db.project_tree(project_id)
    return {
        "project_id": project_id,
        "name": row["name"],
        "file_count": len(tree),
        "symbol_count": sum(len(f["symbols"]) for f in tree),
        "files": tree,
    }
