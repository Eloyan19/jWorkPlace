"""Эндпоинты проектов: подключение репо, список, детали, reindex.

Токен-барьер публичного URL делает nginx (кроме /api/health) — backend его не дублирует.
Индексация — фоновая (pipeline.schedule); статусы отдаём поллингом GET /api/projects/{id}.
"""
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import db
from app.indexing import pipeline
from app.indexing.validation import ValidationError, parse_github_url, precheck_repo

router = APIRouter(prefix="/api/projects")


class CreateProjectRequest(BaseModel):
    url: str


def _project_dto(row) -> dict:
    return {
        "id": row["id"],
        "url": row["url"],
        "name": row["name"],
        "status": row["status"],
        "error": row["error"],
        "indexed_at": row["indexed_at"],
        "head_sha": row["head_sha"],
    }


@router.post("")
async def create_project(req: CreateProjectRequest) -> dict:
    try:
        ref = parse_github_url(req.url)
        await precheck_repo(ref)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    project_id = uuid.uuid4().hex[:12]
    db.create_project(project_id, ref.url, ref.name, db.STATUS_CLONING)
    pipeline.schedule(project_id, ref.url)
    return {"project_id": project_id, "status": db.STATUS_CLONING}


@router.get("")
def list_projects() -> list[dict]:
    return [_project_dto(r) for r in db.list_projects()]


@router.get("/{project_id}")
def get_project(project_id: str) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    return _project_dto(row)


@router.post("/{project_id}/reindex")
async def reindex_project(project_id: str) -> dict:
    row = db.get_project(project_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if row["status"] in db.IN_PROGRESS_STATUSES:
        raise HTTPException(status_code=409, detail="Проект уже обрабатывается.")
    db.set_status(project_id, db.STATUS_CLONING)
    pipeline.schedule(project_id, row["url"], reindex=True)
    return {"status": db.STATUS_CLONING}
