from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.api.app import create_app
from src.api.task_manager import TaskManager
from src.models.orm import Event, Note, Tree, TreeNode


def _env(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "project.md").write_text("# Project\n推进", encoding="utf-8")
    tm = TaskManager(vault, tmp_path / "db.sqlite", tmp_path / "runs", tmp_path / "recordings")
    pipeline = tm.pipeline()
    with Session(pipeline.engine) as session:
        session.add_all([
            Note(id="n1", path="project.md", filename="project.md", vault_status="active"),
            Event(id=1, note_id="n1", extraction_id=1, content="发起项目", time_clue="2026-09-01", status_clue="进行中", order_in_note=0),
            Event(id=2, note_id="n1", extraction_id=1, content="推进项目", time_clue="2026-09-02", status_clue="", order_in_note=1),
            Tree(id="T-1", title="项目树", root_note_id="n1", status="in_progress", confidence=0.8, evidence='["e"]', narrative="综述" , run_id="r1"),
        ])
        session.flush()
        session.add_all([
            TreeNode(id=1, tree_id="T-1", event_id=1, note_id="n1", parent_id=None, order=0, confidence=0.9, evidence='["root"]', origin="agent"),
            TreeNode(id=2, tree_id="T-1", event_id=2, note_id="n1", parent_id=1, order=1, confidence=0.7, evidence='["child"]', origin="human"),
            Tree(id="T-2", title="低置信树", status="dangling_suspected", confidence=0.3, run_id="r1"),
        ])
        session.commit()
    return tm, TestClient(create_app(tm))


def test_forest_filters_and_tree_detail(tmp_path):
    tm, client = _env(tmp_path)
    response = client.get("/api/forest", params={"status": "in_progress", "min_confidence": 0.5})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["trees"][0]["id"] == "T-1"

    detail = client.get("/api/trees/T-1")
    assert detail.status_code == 200
    body = detail.json()
    assert body["node_count"] == 2
    assert body["nodes"][1]["parent_id"] == 1
    assert body["nodes"][0]["note"]["obsidian_uri"] == "obsidian://open?path=project.md"


def test_tree_timeline_and_not_found(tmp_path):
    _, client = _env(tmp_path)
    response = client.get("/api/trees/T-1/timeline")
    assert response.status_code == 200
    assert [item["event"]["content"] for item in response.json()["items"]] == ["发起项目", "推进项目"]

    missing = client.get("/api/trees/no-such")
    assert missing.status_code == 404
    assert missing.json()["code"] == "tree_not_found"
