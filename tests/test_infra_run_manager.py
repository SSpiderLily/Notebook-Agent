"""infra/run_manager.py 的 pytest 测试。从仓库根目录运行：
python -m pytest tests/test_infra_run_manager.py
"""

import pytest

from src.infra.run_manager import RunAlreadyActiveError, RunManager, UnknownStageError
from src.models.orm import STAGE_ORDER


@pytest.fixture
def rm(tmp_path) -> RunManager:
    return RunManager(tmp_path / "noteagent.db")


def test_start_run_creates_run_and_stages(rm):
    run = rm.start_run(scope="全仓库", trigger="api")
    assert run.status == "running"
    stages = rm.get_stages(run.id)
    assert [s.stage for s in stages] == STAGE_ORDER
    assert all(s.status == "pending" for s in stages)


def test_mutex(rm):
    rm.start_run()
    with pytest.raises(RunAlreadyActiveError):
        rm.start_run()


def test_finish_run_releases_mutex_and_fails_running_stages(rm):
    run = rm.start_run()
    rm.set_stage(run.id, "collect", "running")
    rm.set_stage(run.id, "collect", "done")
    rm.set_stage(run.id, "extract", "running")  # 中断遗留

    rm.finish_run(run.id, status="failed")

    assert rm.get_active_run() is None  # 互斥锁已释放
    stages = {s.stage: s for s in rm.get_stages(run.id)}
    assert stages["collect"].status == "done"
    assert stages["extract"].status == "failed"  # running 遗留被标 failed
    assert rm.get_run(run.id).finished_at is not None

    # 释放后可以再启动
    rm.start_run()


def test_finish_run_rejects_bad_status(rm):
    run = rm.start_run()
    with pytest.raises(ValueError):
        rm.finish_run(run.id, status="running")


def test_stage_transitions_and_items(rm):
    run = rm.start_run()
    rm.set_stage(run.id, "extract", "running")
    row = rm.bump_items(run.id, "extract", total=10)
    row = rm.bump_items(run.id, "extract", done=4, failed=1)
    assert (row.items_total, row.items_done, row.items_failed) == (10, 4, 1)
    rm.set_stage(run.id, "extract", "failed", error="部分条目失败")

    stages = {s.stage: s for s in rm.get_stages(run.id)}
    assert stages["extract"].status == "failed"
    assert stages["extract"].error == "部分条目失败"
    assert stages["extract"].started_at is not None
    assert stages["extract"].finished_at is not None


def test_set_stage_rejects_unknown(rm):
    run = rm.start_run()
    with pytest.raises(UnknownStageError):
        rm.set_stage(run.id, "not_a_stage", "running")
    with pytest.raises(ValueError):
        rm.set_stage(run.id, "collect", "weird")


def test_get_last_run_for_resume(rm):
    assert rm.get_last_run() is None
    run1 = rm.start_run()
    rm.finish_run(run1.id, status="done", cost_est=1.5)
    run2 = rm.start_run()

    assert rm.get_last_run(status="done").id == run1.id  # 断点续跑读取的对象
    assert rm.get_last_run().id == run2.id
    assert rm.list_runs()[0].cost_est == 1.5
