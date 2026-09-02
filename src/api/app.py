"""FastAPI 应用工厂（DESIGN.md 3.3 / 3.6 Web 层）。

- `create_app(tasks)` 绑定 TaskManager 到 app.state，挂载 tasks 路由
- 本机安全护栏（NFR-9 / DESIGN.md 五）：仅放行 localhost/127.0.0.1（及测试 host）的 Host，
  校验 Origin（若携带）同源，防止跨站/伪装请求
- 统一错误结构 `{code, message, detail}`
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from src.api.tasks import router as tasks_router
from src.api.task_manager import TaskManager

# 允许的 Host（不含端口）：本机访问 + 测试客户端默认 host
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "testserver", "test"}
_ALLOWED_ORIGIN_PORTS = {""}  # Origin 不强制（curl/本地跳转），存在则须同 host


def _host_of(host_header: str) -> str:
    return host_header.split(":", 1)[0].strip().lower()


def local_only_middleware(app: FastAPI):
    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        if host and _host_of(host) not in _ALLOWED_HOSTS:
            return JSONResponse(
                status_code=403,
                content={"code": "forbidden_host", "message": "仅允许本机访问", "detail": host},
            )
        origin = request.headers.get("origin")
        if origin:
            # Origin 形如 http://localhost:5173，取 host 部分校验
            origin_host = origin.split("//", 1)[-1].split("/", 1)[0]
            if origin_host and _host_of(origin_host) not in _ALLOWED_HOSTS:
                return JSONResponse(
                    status_code=403,
                    content={"code": "forbidden_origin", "message": "Origin 不允许", "detail": origin},
                )
        return await call_next(request)
    return app


def create_app(tasks: TaskManager) -> FastAPI:
    app = FastAPI(title="NoteAgent API", version="0.1.0")
    app.state.tasks = tasks
    app.include_router(tasks_router)
    local_only_middleware(app)

    @app.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": "http_error", "message": str(detail), "detail": None},
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "服务内部错误", "detail": str(exc)},
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app
