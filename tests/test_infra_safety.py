from pathlib import Path

import pytest

from src.infra.backup import BackupManager
from src.infra.config import Settings
from src.infra.safe_writer import SafeWriter


def test_preview_token_detects_external_content_change(tmp_path):
    note = tmp_path / "note.md"
    note.write_text("before", encoding="utf-8")
    writer = SafeWriter(BackupManager(tmp_path / "backups"), vault_dir=tmp_path)
    token = writer.preview_hash(note, "after")
    note.write_text("changed elsewhere", encoding="utf-8")
    with pytest.raises(ValueError, match="preview hash"):
        writer.apply(note, "after", confirm=True, preview_hash=token)


def test_backup_legacy_file_restore_and_manifest_listing(tmp_path):
    source = tmp_path / "note.md"
    source.write_text("old", encoding="utf-8")
    manager = BackupManager(tmp_path / "backups")
    backup_file = manager.backup(source)
    assert manager.list_manifests()
    target = tmp_path / "restored.md"
    assert manager.restore(backup_file, target) == [target]
    assert target.read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize("kwargs", [{"host": "0.0.0.0"}, {"port": 0}, {"price_table": {}}, {"price_table": {"m": (0, 1)}}])
def test_unsafe_or_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        Settings(_env_file=None, **kwargs)
