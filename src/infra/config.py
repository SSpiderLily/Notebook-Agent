"""集中配置（DESIGN.md 2.1 infra/config.py）。

设计原则"配置即代码"：路径/模型/排除项/忽略 glob/费率/阈值全部经
pydantic-settings 集中管理，`.env.example` 与本文件同步维护。
访问方式统一用 `src.infra.config.get_settings()` 单例。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator

# 默认费率表（估算值，单位：元/百万 token，[输入, 输出]）。
# 仅用于成本护栏与试算预估，实际以服务商账单为准；切换模型时在 .env 覆盖。
DEFAULT_PRICE_TABLE: dict[str, tuple[float, float]] = {
    "deepseek-chat": (2.0, 8.0),
    "deepseek-reasoner": (4.0, 16.0),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # 旧 .env 中的遗留键不报错
    )

    # ── LLM（OpenAI 兼容 API；键名与旧 settings.py 保持兼容）──
    openai_api_key: str = ""
    openai_base_url: str = "https://api.deepseek.com/v1"
    model_name: str = "deepseek-chat"
    embedding_model_name: str = "text-embedding-ada-002"

    # RECORD=真实调用并落台账；REPLAY=按指纹离线回放（DESIGN.md 3.2）
    llm_mode: Literal["record", "replay"] = "record"
    llm_concurrency: int = 4  # 并发信号量
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 5  # 网络错误/429/5xx 指数退避次数（schema 错误单独计数）
    llm_schema_fix_retries: int = 2  # 结构化输出修复式重试次数
    llm_run_cost_cap_cny: float = 20.0  # 单 Run 成本上限，超限暂停任务

    # 费率表，JSON 格式，如 PRICE_TABLE='{"deepseek-chat": [2.0, 8.0]}'
    price_table: dict[str, tuple[float, float]] = DEFAULT_PRICE_TABLE

    # ── 路径 ──
    vault_dir: Path = Path("./notebooks")  # Obsidian 仓库（开发调试用样例仓库）
    data_dir: Path = Path("./data")
    logs_dir: Path = Path("./logs")
    artifacts_dirname: str = "_noteagent"  # vault 内生成物专用目录（FR-6）
    frontend_dist: Path = Path("./frontend/dist")  # 前端构建产物目录（DESIGN.md 3.3 静态托管）

    # ── 采集排除与忽略（DESIGN.md 4.4，优先级 frontmatter > glob > 目录）──
    exclude_dirs: list[str] = [
        ".obsidian",
        ".git",
        ".venv",
        "__pycache__",
        "node_modules",
        "_noteagent",
    ]
    ignore_paths: list[str] = []  # glob 模式，如 "私有/**"、"**/日记-*.md"

    # ── 备份与产物版本（FR-10/FR-11）──
    backup_keep: int = 5  # 写回备份保留最近 N 次
    artifact_versions_keep: int = 5  # 产物版本保留最近 N 版

    # ── 本地 Web 服务（NFR-9：仅监听本机）──
    host: str = "127.0.0.1"
    port: int = 8686

    # ── 树重建 Agent 护栏（DESIGN.md 6.1）──
    agent_max_steps: int = 12  # 每个事件最大步数
    confidence_review_threshold: float = 0.6  # 低于此置信度进入人工复核队列

    @field_validator('host')
    @classmethod
    def valid_host(cls, value):
        if value not in {'127.0.0.1', 'localhost', '::1'}:
            raise ValueError('host 必须为本机地址')
        return value

    @field_validator('price_table')
    @classmethod
    def valid_prices(cls, value):
        if not value or any(len(pair) != 2 or any(float(rate) <= 0 for rate in pair) for pair in value.values()):
            raise ValueError('费率必须为正数且不可为空')
        return value

    @field_validator('llm_run_cost_cap_cny', 'llm_timeout_s')
    @classmethod
    def valid_positive_float(cls, value):
        if value <= 0: raise ValueError('数值配置必须为正数')
        return value

    @field_validator('port')
    @classmethod
    def valid_port(cls, value):
        if not 1 <= value <= 65535: raise ValueError('port 必须在 1..65535')
        return value

    @field_validator('llm_concurrency', 'llm_max_retries', 'llm_schema_fix_retries', 'backup_keep', 'artifact_versions_keep', 'agent_max_steps')
    @classmethod
    def valid_positive(cls, value):
        if value < 0: raise ValueError('数值配置不可为负')
        return value

    @field_validator('confidence_review_threshold')
    @classmethod
    def valid_confidence(cls, value):
        if not 0 <= value <= 1: raise ValueError('置信度必须在 0..1')
        return value

    # ── 派生路径（运行时目录统一从这里取，避免散落拼接）──
    @property
    def db_path(self) -> Path:
        return self.data_dir / "noteagent.db"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def runs_dir(self) -> Path:
        return self.data_dir / "runs"

    @property
    def llm_recordings_dir(self) -> Path:
        return self.data_dir / "llm_recordings"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def artifacts_dir(self) -> Path:
        return self.vault_dir / self.artifacts_dirname

    def ensure_runtime_dirs(self) -> None:
        """创建全部运行时目录（幂等）。"""
        for d in (
            self.data_dir,
            self.chroma_dir,
            self.runs_dir,
            self.llm_recordings_dir,
            self.backups_dir,
            self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    def price_of(self, model: str) -> tuple[float, float]:
        """查询模型费率（元/百万 token），未登记的模型按最贵档估算以保守护栏。"""
        if model in self.price_table:
            return self.price_table[model]
        return (
            max(p[0] for p in self.price_table.values()),
            max(p[1] for p in self.price_table.values()),
        )


@lru_cache
def get_settings() -> Settings:
    """配置单例。测试中如需隔离可用 get_settings.cache_clear()。"""
    return Settings()
