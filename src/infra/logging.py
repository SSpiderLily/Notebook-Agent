"""结构化日志（DESIGN.md 3.4）：loguru JSON 行落盘 + 每 Run 独立日志。

用法：
    setup_logging()                      # 应用启动时调用一次
    with bind_run(run_id):               # 该上下文内所有日志自动带 run_id
        logger.info("...")
    sink = add_run_log_file(run_id)      # 整次运行独立落 logs/runs/<run_id>.log
    ...
    remove_sink(sink)                    # 运行结束时移除
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from loguru import logger

from src.infra.config import Settings, get_settings


def setup_logging(settings: Settings | None = None) -> None:
    """初始化全局日志：控制台人类可读 + 文件 JSON 行（按大小轮转）。幂等可重复调用。"""
    settings = settings or get_settings()
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        lambda msg: print(msg, end=""),
        level="INFO",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | "
        "{extra[run_id]} | {extra[stage]} | {message}",
    )
    logger.add(
        settings.logs_dir / "noteagent.log",
        level="DEBUG",
        rotation="10 MB",
        retention=5,
        serialize=True,  # JSON 行，便于检索与回放
        enqueue=True,  # 多线程/asyncio 下写入安全
    )
    # 让 run_id/stage 字段在未绑定时取默认值而不是报错
    logger.configure(extra={"run_id": "-", "stage": "-"})


@contextmanager
def bind_run(run_id: str, stage: str = "") -> Iterator[None]:
    """在上下文内为所有日志绑定安全的 run_id（与可选 stage）。"""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(run_id)):
        raise ValueError("非法 run_id")
    if stage and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(stage)):
        raise ValueError("非法 stage")
    extra = {"run_id": str(run_id)}
    if stage:
        extra["stage"] = str(stage)
    with logger.contextualize(**extra):
        yield


def add_run_log_file(run_id: str, settings: Settings | None = None) -> int:
    """为单次运行添加独立日志文件，返回 sink id（结束时用 remove_sink 移除）。"""
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(run_id)):
        raise ValueError("非法 run_id")
    settings = settings or get_settings()
    run_logs_dir = settings.logs_dir / "runs"
    run_logs_dir.mkdir(parents=True, exist_ok=True)
    return logger.add(
        run_logs_dir / f"{run_id}.log",
        level="DEBUG",
        serialize=True,
        enqueue=True,
    )


def remove_sink(sink_id: int) -> None:
    logger.remove(sink_id)
