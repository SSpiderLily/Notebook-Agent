from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.api.app import create_app
from src.api.task_manager import TaskManager
from src.models.orm import Event, Note, Tree, TreeNode


def _client(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "project.md").write_text("# Project\n推进", encoding="utf-8")
    tm = TaskManager(vault, tmp_path / "db.sqlite", tmp_path / "runs", tmp_path / "recordings")
    with Session(tm.pipeline().engine) as session:
        session.add_all([
            Note(id="n1", path="project.md", filename="project.md"),
            Event(id=1, note_id="n1", extraction_id=1, content="root", order_in_note=0),
            Event(id=2, note_id="n1", extraction_id=1, content="child", order_in_note=1),
            Tree(id="T-1", title="项目", status="in_progress", confidence=0.8, run_id="r1"),
            Tree(id="T-2", title="其他", status="in_progress", confidence=0.8, run_id="r1"),
        ])
        session.flush()
        session.add_all([
            TreeNode(id=1, tree_id="T-1", event_id=1, note_id="n1", order=0),
            TreeNode(id=2, tree_id="T-1", event_id=2, note_id="n1", order=1),
            TreeNode(id=3, tree_id="T-2", event_id=3, note_id="n1", order=0),
        ])
        session.commit()
    return tm, TestClient(create_app(tm))


def test_adjust_status_title_and_move_are_persisted(tmp_path):
    tm, client = _client(tmp_path)
    status = client.post("/api/trees/T-1/adjust", json={"action": "set_status", "payload": {"status": "complete"}})
    assert status.status_code == 200
    title = client.post("/api/trees/T-1/adjust", json={"action": "retitle", "payload": {"title": "新项目"}})
    assert title.status_code == 200
    move = client.post("/api/trees/T-1/adjust", json={"action": "move", "payload": {"node_id": 2, "parent_id": 1, "order": 3}})
    assert move.status_code == 200

    detail = client.get("/api/trees/T-1").json()
    assert detail["status"] == "complete"
    assert detail["title"] == "新项目"
    assert next(n for n in detail["nodes"] if n["id"] == 2)["parent_id"] == 1
    assert client.get("/api/adjustments").json()["count"] == 3


def test_invalid_move_and_reorg_confirmation(tmp_path):
    _, client = _client(tmp_path)
    cross_tree = client.post("/api/trees/T-1/adjust", json={"action": "move", "payload": {"node_id": 1, "parent_id": 3}})
    assert cross_tree.status_code == 422
    no_confirm = client.post("/api/trees/T-1/reorg", json={"confirm": False, "payload": {"action": "merge"}})
    assert no_confirm.status_code == 400
    assert no_confirm.json()["code"] == "confirmation_required"
    confirmed = client.post("/api/trees/T-1/reorg", json={"confirm": True, "payload": {"action": "merge"}})
    assert confirmed.status_code == 200
    assert confirmed.json()["adjustment"]["action"] == "reorg"


def test_regenerate_selected_tree_writes_artifacts(tmp_path):
    tm, client = _client(tmp_path)
    response = client.post("/api/trees/T-1/regenerate")
    assert response.status_code == 200
    assert {item["kind"] for item in response.json()["artifacts"]} == {"tree_page", "overview"}
    assert (tm.vault_dir / "_noteagent/trees/T-1.md").is_file()
    assert (tm.vault_dir / "_noteagent/overview.md").is_file()


def test_adjustment_get_and_revert(tmp_path):
    _, client = _client(tmp_path)
    created = client.post("/api/trees/T-1/adjust", json={"action": "retitle", "payload": {"title": "版本二"}}).json()["adjustment"]
    item = client.get(f"/api/adjustments/{created['id']}")
    assert item.status_code == 200
    reverted = client.delete(f"/api/adjustments/{created['id']}")
    assert reverted.status_code == 200
    assert reverted.json()["status"] == "reverted"
