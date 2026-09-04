"""FastAPI 应用工厂（DESIGN.md 3.3 / 3.6 Web 层）。

- `create_app(tasks)` 绑定 TaskManager 到 app.state，挂载 tasks 路由
- 本机安全护栏（NFR-9 / DESIGN.md 五）：仅放行 localhost/127.0.0.1（及测试 host）的 Host，
  Host 缺失/携带端口时均严格解析主机名；校验 Origin（若携带）必须为标准 http(s) 源
  且与请求 Host 同源，防止跨站/伪装请求
- 统一错误结构 `{code, message, detail}`；参数校验 422、未处理异常 500 均脱敏不泄露内部信息
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from src.api.tasks import router as tasks_router
from src.api.forest import router as forest_router
from src.api.adjustments import router as adjustments_router
from src.api.writeback import router as writeback_router
from src.api.task_manager import TaskManager

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "testserver", "test"}


def _host_of(value: str) -> str:
    """Return a normalized hostname (去掉端口、处理 IPv6 与畸形值，安全容错)."""
    try:
        return (urlsplit("//" + value).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _allowed_host(value: str) -> bool:
    return _host_of(value) in _ALLOWED_HOSTS


def local_only_middleware(app: FastAPI):
    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host：缺失（含空串）直接拒绝；解析主机名时剥离端口，避免端口绕过白名单
        host = request.headers.get("host", "")
        if not host or not _allowed_host(host):
            return JSONResponse(
                status_code=403,
                content={"code": "forbidden_host", "message": "仅允许本机访问", "detail": host or None},
            )
        host_name = _host_of(host)

        # Origin：标准 http(s) 解析、拒绝 opaque/null（非标准 scheme 或缺失主机名），
        # 并与请求 Host 的主机名同源，防止在合法 Host 下携带跨站 Origin 的伪装请求。
        origin = request.headers.get("origin")
        if origin:
            parsed = urlsplit(origin)
            origin_host = _host_of(parsed.netloc)
            if parsed.scheme not in {"http", "https"} or not origin_host or origin_host != host_name:
                return JSONResponse(
                    status_code=403,
                    content={"code": "forbidden_origin", "message": "Origin 不允许", "detail": origin},
                )
        return await call_next(request)
    return app


def create_app(tasks: TaskManager, frontend_dist: Path | None = None) -> FastAPI:
    app = FastAPI(title="NoteAgent API", version="0.1.0")
    app.state.tasks = tasks
    app.include_router(tasks_router)
    app.include_router(forest_router)
    app.include_router(adjustments_router)
    app.include_router(writeback_router)
    local_only_middleware(app)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        """参数/请求体验证失败统一为 422 `{code, message, detail}`（detail 仅含字段级错误，不泄露内部）。"""
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": "请求参数校验失败",
                "detail": exc.errors(),
            },
        )

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
        # 500 脱敏：不把内部异常字符串/堆栈回给客户端，仅返回通用错误
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "服务内部错误", "detail": None},
        )

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    # 静态托管前端构建产物（DESIGN.md 2.1/3.3）：与 API 同源托管 SPA，前端用相对
    # 路径调 /api/*，避免跨源 Origin 违反本机护栏。必须挂在全部 API 路由之后再注册，
    # 让 /api/* 保持优先级；目录缺失时仅提供 API 并告警，不阻断服务。
    if frontend_dist is None:
        from src.infra.config import get_settings as _get_settings
        frontend_dist = _get_settings().frontend_dist
    if Path(frontend_dist).is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:
        logger.warning(f"未找到前端构建产物 {frontend_dist}，仅提供 API（开发期请先 npm run build）")

    return app
