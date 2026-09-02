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
    # 已结束 run → 409 run_not_active
    resp = client.post("/api/tasks/run", json={})
    run_id = resp.json()["id"]
    _wait_finished(tm, run_id)
    r = client.post(f"/api/tasks/{run_id}/cancel")
    assert r.status_code == 409
    assert r.json()["code"] == "run_not_active"
    # 运行中且已注册协作取消信号 → 200 accepted（响应基于 tm.cancel 结果）
    lock_run = tm.pipeline().rm.start_run(trigger="api")
    tm._cancel_events[lock_run.id] = threading.Event()
    r2 = client.post(f"/api/tasks/{lock_run.id}/cancel")
    assert r2.status_code == 200
    assert r2.json()["accepted"] is True
    tm.pipeline().rm.finish_run(lock_run.id, "cancelled")


def test_cancel_depends_on_result(env, client):
    """cancel 端点以 tm.cancel 的返回值为准：未注册信号则视为取消失败(409)。"""
    tm = env["tm"]
    lock_run = tm.pipeline().rm.start_run(trigger="api")
    assert tm.cancel(lock_run.id) is False
    resp = client.post(f"/api/tasks/{lock_run.id}/cancel")
    assert resp.status_code == 409
    assert resp.json()["code"] == "cancel_failed"
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


def test_host_port_and_missing(client):
    # 允许主机名带任意端口：端口剥离后仍是合法本机 host → 放行
    assert client.get("/api/health", headers={"Host": "testserver:8686"}).status_code == 200
    # 非法主机名带端口 → 拒绝
    resp = client.get("/api/health", headers={"Host": "evil.example.com:80"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden_host"
    # 缺失 Host（空串）→ 拒绝
    resp = client.get("/api/health", headers={"Host": ""})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden_host"


def test_origin_same_origin_allowed(client):
    # Origin 与请求 Host 同源（host 为 testserver）→ 放行
    resp = client.get("/api/health", headers={"Origin": "http://testserver"})
    assert resp.status_code == 200
    # 同主机不同端口 → 仍同源放行
    resp = client.get("/api/health", headers={"Origin": "http://testserver:8080"})
    assert resp.status_code == 200
    # 跨源（合法 host 携带外部 Origin）→ 拒绝
    resp = client.get("/api/health", headers={"Origin": "http://127.0.0.1:1234"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "forbidden_origin"
    # 非标准 scheme / null 源 → 拒绝
    assert client.get("/api/health", headers={"Origin": "null"}).status_code == 403
    assert client.get("/api/health", headers={"Origin": "javascript:alert(1)"}).status_code == 403


def test_validation_error_422(client):
    # scope 期望 str|None，传入数字触发参数校验失败
    resp = client.post("/api/tasks/run", json={"scope": 123})
    assert resp.status_code == 422
    body = resp.json()
    assert body["code"] == "validation_error"
    assert "message" in body
    # detail 为字段级错误，不泄露内部实现
    assert "secret" not in json.dumps(body)


def test_500_detail_redacted(client):
    # raise_server_exceptions=False 让未处理异常走 500 响应（默认 True 会在 TestClient 内重新抛出）
    quiet = TestClient(client.app, raise_server_exceptions=False)

    def boom(run_id):
        raise RuntimeError("secret internal detail")

    quiet.app.state.tasks.get = boom
    resp = quiet.get("/api/tasks/x")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "internal_error"
    assert "secret internal detail" not in json.dumps(body)
    assert body["detail"] is None


def test_sse_headers_and_heartbeat(env, client):
    tm = env["tm"]
    client.post("/api/tasks/run", json={})
    run = tm.current()
    assert run is not None
    with client.stream("GET", f"/api/tasks/{run.id}/stream") as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("x-accel-buffering") == "no"
        # 读全文：含阶段推送、done 事件与心跳注释
        body = resp.read().decode("utf-8")
    assert '"event": "done"' in body
    assert '"stage": "extract"' in body
    assert '"status": "done"' in body


def test_sse_heartbeat_on_idle(env):
    """空闲期（阶段无变化）应周期性发 `: ping` 心跳保活。"""
    tm = env["tm"]
    # 直接创建一个 running 但阶段从不变化的 Run：流会持续到 1.5s 后协作收尾为 cancelled
    run = tm.pipeline().rm.start_run(trigger="api")

    def _finish_later():
        time.sleep(1.5)
        tm.pipeline().rm.finish_run(run.id, "cancelled")

    threading.Thread(target=_finish_later, daemon=True).start()
    quiet = TestClient(create_app(env["tm"]))
    body = quiet.get(f"/api/tasks/{run.id}/stream").text
    assert ": ping" in body  # 空闲心跳确实发出
    assert '"event": "done"' in body
    assert '"status": "cancelled"' in body