from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="jWorkPlace backend")

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
    return app


app = create_app()
