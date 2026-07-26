import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db
from app.api.agent import router as agent_router
from app.api.chat import router as chat_router
from app.api.edit import router as edit_router
from app.api.health import router as health_router
from app.api.knowledge import router as knowledge_router
from app.api.projects import router as projects_router
from app.api.review import router as review_router
from app.api.search import router as search_router
from app.api.structure import router as structure_router
from app.api.support import router as support_router
from app.config import get_settings

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    # Проекты, застрявшие в in-progress после рестарта, → error (индексация не выжила рестарт).
    recovered = db.recover_stuck()
    if recovered:
        logging.getLogger("jworkplace").info("восстановлено застрявших проектов: %d", recovered)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="jWorkPlace backend", lifespan=lifespan)

    # CORS-middleware подключаем только если явно заданы origin'ы.
    # Никогда allow_origins=["*"] — браузер ходит только в свой backend,
    # открытая CORS-политика тут не нужна и не должна появиться случайно.
    if settings.cors_origins_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health_router)
    app.include_router(projects_router)
    app.include_router(search_router)
    app.include_router(chat_router)
    app.include_router(edit_router)
    app.include_router(review_router)
    app.include_router(structure_router)
    app.include_router(support_router)
    app.include_router(agent_router)
    app.include_router(knowledge_router)
    return app


app = create_app()
