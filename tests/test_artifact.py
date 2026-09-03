import hashlib
from pathlib import Path

from src.core.artifact import ArtifactRenderer, obsidian_link, safe_filename
from src.services.artifact import ArtifactService


def test_render_tree_and_overview_special_links():
    renderer = ArtifactRenderer()
    tree = {"id": "T/[danger]#1", "title": "项目树", "status": "dangling_suspected", "confidence": .4, "evidence": ["无后续"], "narrative": "从想法到当前状态。"}
    nodes = [{"id": "n1", "event_id": 1, "note_id": "note", "order": 0, "confidence": .8, "evidence": ["同日"]}]
    events = {1: {"content": "推进任务", "time_clue": "2026-09-01", "status_clue": "进行中"}}
    notes = {"note": {"path": "项目/[阶段] #1.md", "filename": "[阶段] #1.md"}}
    page = renderer.render_tree(tree, nodes, events, notes)
    assert "来龙去脉" in page and "推进任务" in page and "dangling_suspected" in page
    assert "obsidian://open" in page and "\\[阶段\\]" in page
    overview = renderer.render_overview([tree], {tree["id"]: "trees/T-danger-1.md"}, "run-1")
    assert "森林总览" in overview and "T-danger-1.md" in overview
    assert safe_filename("../a[b]#c") == "a-b-c"


def test_artifact_service_writes_idempotently_and_versions(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    service = ArtifactService(vault, tmp_path / "backups", versions_keep=3)
    tree = {"id": "T-1", "title": "树", "status": "in_progress", "confidence": .8}
    first = service.generate([tree], {"T-1": [{"event_id": 1, "note_id": "n", "order": 0, "confidence": .8, "evidence": []}]}, {1: {"content": "事件"}}, {"n": {"path": "a.md", "filename": "a.md"}}, "run-1")
    assert len(first["artifacts"]) == 2
    page = vault / "_noteagent" / "trees" / "T-1.md"
    overview = vault / "_noteagent" / "overview.md"
    assert page.is_file() and overview.is_file()
    old_hash = hashlib.sha256(page.read_bytes()).hexdigest()
    second = service.generate([tree], {"T-1": [{"event_id": 1, "note_id": "n", "order": 0, "confidence": .9, "evidence": []}]}, {1: {"content": "事件"}}, {"n": {"path": "a.md", "filename": "a.md"}}, "run-2")
    assert second["artifacts"][0]["changed"] is True
    assert hashlib.sha256(page.read_bytes()).hexdigest() != old_hash
    third = service.generate([tree], {"T-1": [{"event_id": 1, "note_id": "n", "order": 0, "confidence": .9, "evidence": []}]}, {1: {"content": "事件"}}, {"n": {"path": "a.md", "filename": "a.md"}}, "run-3")
    assert third["artifacts"][0]["changed"] is False
    assert third["artifacts"][1]["changed"] is True  # 总览保留最新 run_id
    assert not (vault / "a.md").exists()
