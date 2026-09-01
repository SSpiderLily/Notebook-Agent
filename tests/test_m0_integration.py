import json
from pydantic import BaseModel
from src.infra.stage_io import StageIO, StageArtifactError
from src.infra.llm_gateway import LLMGateway, LLMReplayMiss
from src.infra.backup import BackupManager
from src.infra.safe_writer import SafeWriter

class Answer(BaseModel):
    ok: bool

def test_stage_io_atomic_versioned(tmp_path):
    io = StageIO(tmp_path / "runs")
    path = io.write("r1", "collect", {"count": 2})
    assert io.read("r1", "collect") == {"count": 2}
    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    data["schema_version"] = 99
    path.write_text(json.dumps(data))
    try:
        io.read("r1", "collect")
        assert False
    except StageArtifactError:
        pass

def test_gateway_replay_and_miss(tmp_path):
    prompt = "hello"
    gw_record = LLMGateway(tmp_path, mode="record", transport=lambda _: '{"ok": true}')
    assert gw_record.structured(prompt, Answer).ok
    gw_replay = LLMGateway(tmp_path, mode="replay")
    assert gw_replay.structured(prompt, Answer).ok
    try:
        gw_replay.chat("missing")
        assert False
    except LLMReplayMiss:
        pass

def test_safe_writer_preview_backup_idempotent(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("old", encoding="utf-8")
    writer = SafeWriter(BackupManager(tmp_path / "backups"))
    assert "-old" in writer.preview(target, "new")
    assert writer.apply(target, "new", confirm=True)
    assert target.read_text() == "new"
    assert not writer.apply(target, "new", confirm=True)
    assert list((tmp_path / "backups").glob("*/note.md"))
