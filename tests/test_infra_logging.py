"""infra/logging.py 的 pytest 测试。从仓库根目录运行：
python -m pytest tests/test_infra_logging.py
"""

import json

from src.infra.logging import (
    add_run_log_file,
    bind_run,
    remove_sink,
    setup_logging,
)
from src.infra.config import Settings


def make_settings(tmp_path) -> Settings:
    return Settings(_env_file=None, vault_dir=tmp_path, data_dir=tmp_path / "d", logs_dir=tmp_path / "logs")


def test_json_log_file_and_run_binding(tmp_path):
    settings = make_settings(tmp_path)
    setup_logging(settings)
    run_log_id = add_run_log_file("run-test-1", settings)
    try:
        with bind_run("run-test-1", stage="collect"):
            from loguru import logger

            logger.info("你好 NoteAgent")
    finally:
        remove_sink(run_log_id)

    main_log = settings.logs_dir / "noteagent.log"
    run_log = settings.logs_dir / "runs" / "run-test-1.log"
    assert main_log.exists()
    assert run_log.exists()

    record = json.loads(main_log.read_text(encoding="utf-8").splitlines()[-1])
    assert record["record"]["message"] == "你好 NoteAgent"
    assert record["record"]["extra"]["run_id"] == "run-test-1"
    assert record["record"]["extra"]["stage"] == "collect"

    # 每 Run 独立日志同样收到该条
    assert "你好 NoteAgent" in run_log.read_text(encoding="utf-8")


def test_setup_logging_idempotent(tmp_path):
    settings = make_settings(tmp_path)
    setup_logging(settings)
    setup_logging(settings)  # 重复调用不叠加 sink / 不报错
    from loguru import logger

    logger.info("重复初始化后仍可写")
    assert (settings.logs_dir / "noteagent.log").exists()
