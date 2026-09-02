import logging
import secrets
import time
from collections.abc import Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app import __version__
from app.api.agent import router as agent_router
from app.api.projects import router as projects_router
from app.api.qa import router as qa_router
from app.api.review import router as review_router
from app.api.system import router as system_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    application = FastAPI(title=settings.app_name, version=__version__)
    application.include_router(system_router, prefix=settings.api_prefix)
    application.include_router(qa_router, prefix=settings.api_prefix)
    application.include_router(review_router, prefix=settings.api_prefix)
    application.include_router(agent_router, prefix=settings.api_prefix)
    application.include_router(projects_router, prefix=settings.api_prefix)

    @application.middleware("http")
    async def request_trace(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"request_{secrets.token_hex(10)}"
        started = time.perf_counter()
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logging.getLogger("api.request").info(
            "request_completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": (time.perf_counter() - started) * 1000,
            },
        )
        return response

    @application.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        logging.getLogger(__name__).warning(
            exc.message,
            extra={"component": "api", "status": "error", "error_code": exc.code},
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logging.getLogger(__name__).exception(
            "unexpected_error",
            extra={"component": "api", "status": "error", "request_id": request_id},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "服务内部错误",
                    "details": {"request_id": request_id},
                }
            },
        )

    return application


app = create_app()


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
