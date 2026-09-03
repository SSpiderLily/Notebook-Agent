from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("record_progress", ROOT / "scripts/record-progress.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _devlog(tmp_path: Path) -> Path:
    root = tmp_path / "dev-log"
    root.mkdir()
    (root / "进度看板.md").write_text("# 看板\n\n## 时间线\n", encoding="utf-8")
    return root


def _args(root: Path, **updates):
    values = dict(event_id="EVT-001", stage="M6", title="查询 API", summary="完成森林查询", status="completed", tests="3 passed", commit="abc123", date="2026-09-03", dev_log=root)
    values.update(updates)
    return MODULE.argparse.Namespace(**values)


def test_record_creates_atomic_event_and_bidirectional_links(tmp_path):
    root = _devlog(tmp_path)
    path, created = MODULE.record(_args(root))
    assert created and path.is_file()
    event = path.read_text(encoding="utf-8")
    board = (root / "进度看板.md").read_text(encoding="utf-8")
    assert "进度看板：[[进度看板]]" in event
    assert "来源：[[EVT-001-查询-API]]" in board
    assert "EVT-001" in event and "3 passed" in board


def test_record_is_idempotent_and_preserves_history(tmp_path):
    root = _devlog(tmp_path)
    args = _args(root)
    MODULE.record(args)
    before = (root / "进度看板.md").read_text(encoding="utf-8")
    path, created = MODULE.record(args)
    assert not created and path.is_file()
    assert (root / "进度看板.md").read_text(encoding="utf-8") == before


def test_completed_requires_test_result(tmp_path):
    with pytest.raises(ValueError, match="--tests"):
        MODULE.record(_args(_devlog(tmp_path), tests=""))


def test_invalid_event_id_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="event-id"):
        MODULE.record(_args(_devlog(tmp_path), event_id="../bad"))


def test_board_existing_event_without_document_is_rejected(tmp_path):
    root = _devlog(tmp_path)
    board = root / "进度看板.md"
    board.write_text(board.read_text(encoding="utf-8") + "EVT-001\n", encoding="utf-8")
    with pytest.raises(ValueError, match="看板已包含"):
        MODULE.record(_args(root))
