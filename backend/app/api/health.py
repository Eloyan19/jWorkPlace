from fastapi import APIRouter

from app.version import get_version

router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": get_version()}
