from __future__ import annotations

import json

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.api.app import create_app
from src.api.task_manager import TaskManager
from src.models.orm import Extraction, Note, Tree, TreeNode


def _client(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "a.md").write_text("---\ntags: [旧标签]\n---\n\n# A\n正文。\n", encoding="utf-8")
    (vault / "b.md").write_text("# B\n\n内容。\n", encoding="utf-8")
    tm = TaskManager(vault, tmp_path / "db.sqlite", tmp_path / "runs", tmp_path / "recordings")
    with Session(tm.pipeline().engine) as session:
        session.add_all([
            Note(id="n-a", path="a.md", filename="a.md"),
            Note(id="n-b", path="b.md", filename="b.md"),
            Extraction(note_id="n-a", run_id="r1", title="A", summary="", raw_json="{}", candidate_tags=json.dumps(["机器学习", "旧标签"], ensure_ascii=False)),
            Extraction(note_id="n-b", run_id="r1", title="B", summary="", raw_json="{}", candidate_tags=json.dumps(["#标签B"], ensure_ascii=False)),
            Tree(id="T-1", title="树一", status="in_progress", confidence=0.8, verified=True, locked=True, run_id="r1"),
            Tree(id="T-2", title="草稿", status="in_progress", confidence=0.5, verified=False, run_id="r1"),
        ])
        session.flush()
        session.add_all([
            TreeNode(id=1, tree_id="T-1", event_id=1, note_id="n-a", order=0),
            TreeNode(id=2, tree_id="T-1", event_id=2, note_id="n-b", order=1),
            TreeNode(id=3, tree_id="T-2", event_id=3, note_id="n-a", order=0),
        ])
        session.commit()
    return tm, TestClient(create_app(tm))


def test_tags_preview_and_confirm_only_add(tmp_path):
    tm, client = _client(tmp_path)
    preview = client.post("/api/writeback/preview", json={"kind": "tags"})
    assert preview.status_code == 200
    data = preview.json()
    assert data["kind"] == "tags" and data["count"] == 1  # n-a 有真实候选标签
    item = data["items"][0]
    assert item["note_id"] == "n-a"
    assert "+tags" in item["diff"] or "-tags" in item["diff"] or "机器学习" in item["diff"]
    assert item["applied"] is False

    no_confirm = client.post(f"/api/writeback/jobs/{data['id']}/confirm", json={"confirm": False})
    assert no_confirm.status_code == 400
    assert no_confirm.json()["code"] == "confirmation_required"
    assert (tm.vault_dir / "a.md").read_text(encoding="utf-8").startswith("---")

    applied = client.post(f"/api/writeback/jobs/{data['id']}/confirm", json={"confirm": True})
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"
    assert all(i["applied"] for i in applied.json()["items"])
    content = (tm.vault_dir / "a.md").read_text(encoding="utf-8")
    assert "机器学习" in content
    assert "旧标签" in content  # 只增不删：既有标签保留
    # 幂等：再次 confirm 不报错、不重复备份
    again = client.post(f"/api/writeback/jobs/{data['id']}/confirm", json={"confirm": True})
    assert again.status_code == 200


def test_links_preview_only_verified_trees(tmp_path):
    tm, client = _client(tmp_path)
    preview = client.post("/api/writeback/preview", json={"kind": "links"})
    assert preview.status_code == 200
    data = preview.json()
    # T-1 已确认：n-a 与 n-b 互链；T-2 未确认不参与
    by_note = {i["note_id"]: i for i in data["items"]}
    assert set(by_note) == {"n-a", "n-b"}
    a = (tm.vault_dir / "a.md").read_text(encoding="utf-8")
    # 确认前不落盘
    assert "关联笔记" not in a

    client.post(f"/api/writeback/jobs/{data['id']}/confirm", json={"confirm": True})
    b = (tm.vault_dir / "b.md").read_text(encoding="utf-8")
    assert "[[a.md" in b  # n-b 链接到 n-a
    # 草稿树 T-2 的成员 n-a 不应因此额外链接（n-a 已在 T-1 处理），非 verified 不新增
    assert (tm.vault_dir / "a.md").read_text(encoding="utf-8").count("关联笔记") == 1


def test_backup_created_and_restore(tmp_path):
    tm, client = _client(tmp_path)
    original = (tm.vault_dir / "a.md").read_text(encoding="utf-8")
    job = client.post("/api/writeback/preview", json={"kind": "tags"}).json()
    client.post(f"/api/writeback/jobs/{job['id']}/confirm", json={"confirm": True})
    backups = client.get("/api/writeback/backups").json()
    assert backups["count"] >= 1
    backup_id = backups["backups"][0]["id"]

    bad = client.post(f"/api/writeback/backups/{backup_id}/restore", json={"confirm": False})
    assert bad.status_code == 400
    restored = client.post(f"/api/writeback/backups/{backup_id}/restore", json={"confirm": True})
    assert restored.status_code == 200
    assert (tm.vault_dir / "a.md").read_text(encoding="utf-8") == original


def test_preview_detects_external_change(tmp_path):
    tm, client = _client(tmp_path)
    job = client.post("/api/writeback/preview", json={"kind": "tags"}).json()
    (tm.vault_dir / "a.md").write_text("外部修改了文件", encoding="utf-8")
    applied = client.post(f"/api/writeback/jobs/{job['id']}/confirm", json={"confirm": True})
    assert applied.status_code == 200
    # 令牌校验失败：该项记录失败，job 进入 failed/partially_applied，文件不被改写
    assert applied.json()["status"] in ("failed", "partially_applied")
    assert (tm.vault_dir / "a.md").read_text(encoding="utf-8") == "外部修改了文件"