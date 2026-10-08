from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from mentor import health
from mentor.security import SecurityHeadersMiddleware
from mentor.web import STATIC_DIR
from mentor.web import routes as web_routes


def create_app() -> FastAPI:
    app = FastAPI(title="Mentor", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(health.router)
    app.include_router(web_routes.router)
    return app


app = create_app()
