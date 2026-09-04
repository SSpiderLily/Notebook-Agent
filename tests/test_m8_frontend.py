"""M8 前端静态托管：同源托管 SPA，API 优先级保持，目录缺失不阻断。

验证 DESIGN.md 3.3「静态托管」：
- /api/* 路由优先级高于静态 catch-all
- dist 存在时 / 返回 index.html
- dist 缺失时 API 仍可用、根路径不崩
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.task_manager import TaskManager


def _tm(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "a.md").write_text("# A\n\n正文。\n", encoding="utf-8")
    return TaskManager(vault, tmp_path / "db.sqlite", tmp_path / "runs", tmp_path / "recordings")


def _dist(tmp_path, content="<h1>NoteAgent</h1>"):
    dist = tmp_path / "dist"; dist.mkdir()
    (dist / "index.html").write_text(content, encoding="utf-8")
    return dist


def test_api_routes_take_precedence_over_static(tmp_path):
    """静态 mount 不吞掉 /api 路由：health 200、不存在的树返回 API 404 而非 index.html。"""
    dist = _dist(tmp_path)
    client = TestClient(create_app(_tm(tmp_path), frontend_dist=dist))
    assert client.get("/api/health").status_code == 200
    r = client.get("/api/trees/does-not-exist")
    assert r.status_code == 404
    assert r.json().get("code") == "tree_not_found"


def test_static_serves_index_when_dist_present(tmp_path):
    """dist 存在时 / 返回 index.html（SPA 外壳）。"""
    dist = _dist(tmp_path, "<h1>NoteAgent SPA</h1>")
    client = TestClient(create_app(_tm(tmp_path), frontend_dist=dist))
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "NoteAgent SPA" in r.text


def test_missing_dist_api_only(tmp_path):
    """dist 缺失时仅提供 API：health 200、根路径不崩（404/非 HTML 均可，关键是服务在线）。"""
    client = TestClient(create_app(_tm(tmp_path), frontend_dist=tmp_path / "not-exist"))
    assert client.get("/api/health").status_code == 200
    r = client.get("/")
    assert r.status_code == 404
