from fastapi import FastAPI, APIRouter
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from config import Settings, settings

OPENAPI_TAGS = [
    {
        "name": "Auth",
        "description": "Client secret issuance and token exchange for publish APIs.",
    },
    {
        "name": "Issuers",
        "description": "Configured issuer instances from ``configs/issuers.yaml``.",
    },
    {
        "name": "Credentials",
        "description": "Publish, fetch, and refresh credentials.",
    },
    {
        "name": "Templates",
        "description": "Credential templates and OCA bundles by type and version.",
    },
    {
        "name": "Status-Lists",
        "description": "Bitstring status list credentials (revocation, suspension, refresh).",
    },
]


def build_app(cfg: Settings) -> FastAPI:
    title = cfg.PROJECT_TITLE
    if cfg.TEST_SUITE:
        title = f"{title} (test suite)"

    app = FastAPI(
        title=title,
        version=cfg.PROJECT_VERSION,
        openapi_tags=OPENAPI_TAGS if not cfg.TEST_SUITE else None,
    )

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
            authentication,
            credentials,
            issuers,
            status_lists,
            templates,
        )

        # OpenAPI section order follows OPENAPI_TAGS.
        api_router.include_router(authentication.router)
        api_router.include_router(issuers.router)
        api_router.include_router(credentials.router)
        api_router.include_router(templates.router)
        api_router.include_router(status_lists.router)

    app.include_router(api_router)
    return app


app = build_app(settings)
