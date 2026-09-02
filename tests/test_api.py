"""src/api FastAPI/SSE 的最小闭环测试。从仓库根目录运行：
.venv/bin/python -m pytest tests/test_api.py -q
"""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.task_manager import TaskManager
from src.core.extraction import ExtractionDraft
from src.infra.llm_gateway import LLMGateway
from src.infra.run_manager import RunAlreadyActiveError


def _make_recording(recordings, note, model="test"):
    """用真实 structured 指纹生成抽取回放，保证 replay 命中 schema 名的键。"""
    prompt = f"请提炼以下笔记为 JSON（title, summary, keywords, candidate_tags, events）；笔记路径：{note['relative_path']}\n{note['content']}"
    gw = LLMGateway(recordings, mode="record", model=model, transport=lambda _: json.dumps(
        {"title": "A", "summary": "推进项目", "keywords": [], "candidate_tags": [],
         "events": [{"content": "推进项目", "order_in_note": 0}]}
    ))
    gw.structured(prompt, ExtractionDraft)
    return gw


@pytest.fixture
def env(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "a.md").write_text("# A\n推进项目\n", encoding="utf-8")
    db = tmp_path / "noteagent.db"
    runs = tmp_path / "runs"
    recordings = tmp_path / "recordings"; recordings.mkdir()

    # 用 Collector 采集到的同一行生成回放（relative_path 决定 prompt 指纹）
    from src.data.collection import Collector
    note = Collector(vault).collect()[0]
    _make_recording(recordings, note)

    tm = TaskManager(vault, db, runs, recordings, mode="replay")
    return {"tmp_path": tmp_path, "tm": tm, "recordings": recordings}


@pytest.fixture
def client(env):
    return TestClient(create_app(env["tm"]))


def _wait_finished(tm, run_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = tm.get(run_id)
        if run and run.status != "running":
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} 未在 {timeout}s 内结束: status={tm.get(run_id).status if tm.get(run_id) else None}")


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_preview_returns_estimate(client):
    resp = client.post("/api/tasks/preview", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["notes"] == 1
    assert data["characters"] > 0
    assert data["calls"] == 1
    assert data["estimated_cost_cny"] >= 0


def test_run_and_get_done(env, client):
    tm = env["tm"]
    resp = client.post("/api/tasks/run", json={})
    assert resp.status_code == 200
    run_id = resp.json()["id"]
    assert resp.json()["status"] == "running"

    run = _wait_finished(tm, run_id)
    assert run.status == "done"

    detail = client.get(f"/api/tasks/{run_id}").json()
    assert detail["status"] == "done"
    stages = {s["stage"]: s for s in detail["stages"]}
    assert stages["collect"]["status"] == "done"
    assert stages["extract"]["status"] == "done"

    assert client.get("/api/tasks/nowhere").status_code == 404


def test_current_returns_active_or_null(client):
    resp = client.get("/api/tasks/current")
    assert resp.status_code == 200


def test_mutex_409_and_http_409(env, client):
    tm = env["tm"]
    # 直接创建一个 running Run 作为互斥锁（不结束），保证 409 确定性
    lock_run = tm.pipeline().rm.start_run(trigger="api")
    with pytest.raises(RunAlreadyActiveError):
        tm.start()
    resp = client.post("/api/tasks/run", json={})
    assert resp.status_code == 409
    assert resp.json()["code"] == "task_active"
    # 清理锁，避免影响同 env 其它用法的语义（此处仅本地校验）
    tm.pipeline().rm.finish_run(lock_run.id, "cancelled")


def test_cancel_lifecycle(env, client):
    tm = env["tm"]
    # 未知 run → 404
    assert client.post("/api/tasks/nope/cancel").status_code == 404
    # 已结束 run → 409
    resp = client.post("/api/tasks/run", json={})
    run_id = resp.json()["id"]
    _wait_finished(tm, run_id)
    assert client.post(f"/api/tasks/{run_id}/cancel").status_code == 409
    # 运行中 run（用锁占据）→ accepted
    lock_run = tm.pipeline().rm.start_run(trigger="api")
    r2 = client.post(f"/api/tasks/{lock_run.id}/cancel")
    assert r2.status_code == 200
    assert r2.json()["accepted"] is True
    tm.pipeline().rm.finish_run(lock_run.id, "cancelled")


def test_sse_stream_reports_stages(env, client):
    tm = env["tm"]
    client.post("/api/tasks/run", json={})
    # 通过 GET current 拿到 run_id（流会伴随运行结束而关闭，一次性读取全文）
    run = tm.current()
    assert run is not None
    body = client.get(f"/api/tasks/{run.id}/stream").text
    assert '"event": "done"' in body
    assert '"stage": "extract"' in body
    assert '"status": "done"' in body


def test_local_only_host_guard(client):
    resp = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden_host"
    resp = client.get("/api/health", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden_origin"