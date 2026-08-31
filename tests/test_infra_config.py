"""infra/config.py 的 pytest 测试。

注意：必须用 `_env_file=None` 构造，避免读到开发者的真实 `.env`。
从仓库根目录运行：python -m pytest tests/test_infra_config.py
"""

import json
import os
from pathlib import Path

from src.infra.config import Settings, get_settings


def make_settings(monkeypatch=None, **overrides) -> Settings:
    if monkeypatch is not None:
        # 清掉 shell 环境中可能与配置字段同名的变量，保证测试确定性
        for key in list(os.environ):
            if key in Settings.model_fields:
                monkeypatch.delenv(key, raising=False)
    return Settings(_env_file=None, **overrides)


def test_defaults(monkeypatch):
    s = make_settings(monkeypatch)
    assert s.openai_base_url == "https://api.deepseek.com/v1"
    assert s.model_name == "deepseek-chat"
    assert s.llm_mode == "record"
    assert s.llm_concurrency == 4
    assert s.host == "127.0.0.1"  # NFR-9：仅监听本机
    assert ".obsidian" in s.exclude_dirs
    assert s.artifacts_dirname in s.exclude_dirs  # 生成物目录本身也要排除扫描


def test_env_override(monkeypatch):
    monkeypatch.setenv("LLM_MODE", "replay")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("VAULT_DIR", "/tmp/fake_vault")
    s = make_settings(monkeypatch)
    assert s.llm_mode == "replay"
    assert s.port == 9000
    assert s.vault_dir == Path("/tmp/fake_vault")


def test_list_env_json(monkeypatch):
    monkeypatch.setenv("IGNORE_PATHS", json.dumps(["私有/**", "**/日记-*.md"]))
    s = make_settings(monkeypatch)
    assert s.ignore_paths == ["私有/**", "**/日记-*.md"]


def test_price_table_json_and_fallback(monkeypatch):
    monkeypatch.setenv("PRICE_TABLE", json.dumps({"m1": [1.0, 2.0]}))
    s = make_settings(monkeypatch)
    assert s.price_of("m1") == (1.0, 2.0)
    # 未登记模型按最贵档估算（成本护栏宁可高估）
    assert s.price_of("unknown-model") == (1.0, 2.0)


def test_derived_paths(monkeypatch, tmp_path):
    s = make_settings(monkeypatch, vault_dir=tmp_path, data_dir=tmp_path / "d")
    assert s.db_path == tmp_path / "d" / "noteagent.db"
    assert s.chroma_dir == tmp_path / "d" / "chroma"
    assert s.runs_dir == tmp_path / "d" / "runs"
    assert s.artifacts_dir == tmp_path / "_noteagent"
    s.ensure_runtime_dirs()
    assert (tmp_path / "d" / "llm_recordings").is_dir()
    assert (tmp_path / "d" / "backups").is_dir()


def test_singleton():
    get_settings.cache_clear()
    assert get_settings() is get_settings()
