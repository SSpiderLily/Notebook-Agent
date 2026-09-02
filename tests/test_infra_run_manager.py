"""infra/run_manager.py 的 pytest 测试。从仓库根目录运行：
python -m pytest tests/test_infra_run_manager.py
"""

import threading

import pytest

from src.infra.run_manager import (
    IllegalStageTransitionError,
    RunAlreadyActiveError,
    RunFinishedError,
    RunManager,
    UnknownStageError,
)
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


# ── 第一批核心状态基础设施：原子互斥 / 原子 bump / 阶段迁移 / 终态保护 / WAL / 孤儿恢复 ──

def test_sqlite_journal_is_wal_and_busy_timeout(rm):
    with rm.engine.connect() as c:
        mode = c.exec_driver_sql("PRAGMA journal_mode").scalar()
        timeout = c.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert str(mode).lower() == "wal"
    assert timeout >= 5000


def test_mutex_is_atomic_under_concurrency(tmp_path):
    """并发触发 start_run：BEGIN IMMEDIATE 原子互斥，恰有一个成功，其余抛 RunAlreadyActiveError。"""
    manager = RunManager(tmp_path / "noteagent.db")
    n = 16
    barrier = threading.Barrier(n)
    ok, failed = [], []

    def worker():
        barrier.wait()
        try:
            manager.start_run()
            ok.append(1)
        except RunAlreadyActiveError:
            failed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(ok) == 1 and len(failed) == n - 1
    assert manager.get_active_run() is not None  # 锁已由唯一成功者占据


def test_bump_items_is_atomic_server_side(rm):
    run = rm.start_run()
    rm.set_stage(run.id, "extract", "running")
    # total 为覆盖式，done/failed 为原子增量
    rm.bump_items(run.id, "extract", total=10)
    rm.bump_items(run.id, "extract", done=4, failed=1)
    row = rm.bump_items(run.id, "extract", done=3)
    assert (row.items_total, row.items_done, row.items_failed) == (10, 7, 1)
    # 再开一个会话读库确认已持久化（非仅内存对象）
    assert rm.get_stages(run.id)[2].items_done == 7


def test_bump_items_negative_rejected(rm):
    run = rm.start_run()
    with pytest.raises(ValueError):
        rm.bump_items(run.id, "extract", done=-1)


def test_legal_stage_transitions(rm):
    run = rm.start_run()
    rm.set_stage(run.id, "init", "running")
    rm.set_stage(run.id, "init", "done")
    rm.set_stage(run.id, "associate", "skipped")  # 未启动阶段直接跳过（extract 失败分支）
    stages = {s.stage: s for s in rm.get_stages(run.id)}
    assert stages["init"].status == "done"
    assert stages["associate"].status == "skipped"


def test_illegal_stage_transitions_rejected(rm):
    run = rm.start_run()
    rm.set_stage(run.id, "extract", "running")
    rm.set_stage(run.id, "extract", "done")
    # 终态阶段不可回退到 running
    with pytest.raises(IllegalStageTransitionError):
        rm.set_stage(run.id, "extract", "running")
    # failed 亦为终态，不可再转 running
    rm.set_stage(run.id, "associate", "running")
    rm.set_stage(run.id, "associate", "failed")
    with pytest.raises(IllegalStageTransitionError):
        rm.set_stage(run.id, "associate", "running")


def test_terminal_run_protects_stage_and_items(rm):
    run = rm.start_run()
    rm.set_stage(run.id, "extract", "running")
    rm.finish_run(run.id, status="done")
    with pytest.raises(RunFinishedError):
        rm.set_stage(run.id, "extract", "running")
    with pytest.raises(RunFinishedError):
        rm.bump_items(run.id, "extract", done=1)


def test_finish_run_is_idempotent_on_terminal(rm):
    run = rm.start_run()
    rm.finish_run(run.id, status="done")
    again = rm.finish_run(run.id, status="failed")  # 重复 finish 为 no-op，不报错
    assert again.status == "done"
    assert rm.get_active_run() is None


def test_recover_orphans_api(tmp_path):
    """孤儿恢复 API：遗留 running 被回收，释放互斥锁，running 阶段被标记 failed。"""
    rm1 = RunManager(tmp_path / "noteagent.db", recover_orphans_on_startup=False)
    run = rm1.start_run()
    rm1.set_stage(run.id, "collect", "running")
    rm1.set_stage(run.id, "collect", "done")
    rm1.set_stage(run.id, "extract", "running")  # 中断遗留
    # 过期进程"消失"，新 manager 显式回收
    rm2 = RunManager(tmp_path / "noteagent.db", recover_orphans_on_startup=False)
    assert rm2.recover_orphans() == 1
    assert rm2.get_active_run() is None
    run2 = rm2.get_run(run.id)
    assert run2.status == "failed"
    stages = {s.stage: s for s in rm2.get_stages(run.id)}
    assert stages["collect"].status == "done"  # 已完成阶段保留
    assert stages["extract"].status == "failed"  # running 遗留被标 failed
    # 恢复后互斥锁释放，可再启动
    rm2.start_run()


def test_recover_orphans_on_startup_default(tmp_path):
    """启动策略：构造 RunManager 默认回收孤儿，避免僵尸锁阻塞新任务（FR-8）。"""
    rm1 = RunManager(tmp_path / "noteagent.db", recover_orphans_on_startup=False)
    rm1.start_run()
    rm2 = RunManager(tmp_path / "noteagent.db")  # 默认 recover_orphans_on_startup=True
    assert rm2.get_active_run() is None
    rm2.start_run()
