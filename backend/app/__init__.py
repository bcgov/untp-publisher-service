from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import Settings, settings


def build_app(cfg: Settings) -> FastAPI:
    title = cfg.PROJECT_TITLE
    if cfg.TEST_SUITE:
        title = f"{title} (test suite)"

    app = FastAPI(title=title, version=cfg.PROJECT_VERSION)

    if not cfg.TEST_SUITE:
        app.mount("/static", StaticFiles(directory="app/static"), name="static")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    api_router = APIRouter()

    @api_router.get("/server/status", tags=["Server"], include_in_schema=False)
    async def server_status():
        cfg.LOGGER.info("Server status OK!")
        return JSONResponse(status_code=200, content={"status": "ok"})

    if cfg.TEST_SUITE:
        from app.routers import test_suite

        api_router.include_router(test_suite.router)
    else:
        from app.routers import (
            admin_data,
            admin_ui,
            authentication,
            credentials,
            related_resources,
            registrations,
        )

        api_router.include_router(authentication.router)
        api_router.include_router(registrations.router)
        api_router.include_router(credentials.router)
        api_router.include_router(related_resources.router)
        api_router.include_router(admin_data.router)
        api_router.include_router(admin_ui.router)

    app.include_router(api_router)
    return app


app = build_app(settings)
