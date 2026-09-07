from src.services.artifact import ArtifactService


def test_artifact_versions_and_rollback(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    service = ArtifactService(vault, tmp_path / "backups", versions_keep=3)
    tree = {"id":"T-1", "title":"树", "status":"in_progress", "confidence":.8}
    args = ({"T-1":[{"event_id":1,"note_id":"n","order":0,"confidence":.8,"evidence":[]}]}, {1:{"content":"事件"}}, {"n":{"path":"a.md","filename":"a.md"}})
    service.generate([tree], *args, "run-1")
    tree["title"] = "更新树"
    service.generate([tree], *args, "run-2")
    path = "_noteagent/trees/T-1.md"
    versions = service.list_versions(path)
    assert any(v["version"] == 1 and not v["current"] for v in versions)
    result = service.rollback(path, 1)
    assert result["changed"] is True
    assert "更新树" not in (vault / path).read_text()
    assert not (vault / "a.md").exists()


def test_artifact_rollback_rejects_outside_path(tmp_path):
    service = ArtifactService(tmp_path / "vault", tmp_path / "backups")
    try:
        service.list_versions("../secret.md")
    except ValueError:
        pass
    else:
        raise AssertionError("outside artifact path must be rejected")
