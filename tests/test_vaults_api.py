"""仓库(vault)切换与注册表 API 测试。

范式参照 tests/test_m6_forest_api.py 的 `_env(tmp_path)`：每次用 tmp_path 建独立仓库
与数据目录，TestClient 从 app.state 读 TaskManager。
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.api.task_manager import TaskManager
from src.models.orm import Tree


def _env(tmp_path):
    vault_a = tmp_path / "vaultA"
    vault_b = tmp_path / "vaultB"
    for v in (vault_a, vault_b):
        v.mkdir()
        (v / "note.md").write_text("# 笔记\n内容", encoding="utf-8")
    tm = TaskManager(
        vault_a,
        tmp_path / "dataA" / "noteagent.db",
        tmp_path / "dataA" / "runs",
        tmp_path / "dataA" / "recordings",
    )
    client = TestClient(create_app(tm))
    return tm, client, str(vault_a), str(vault_b)


def _register(client, path, name=None):
    return client.post("/api/vaults/register", json={"path": path, "name": name})


def _switch(client, vid):
    return client.post(f"/api/vaults/{vid}/switch", json={"confirm": True})


def test_register_list_and_current(tmp_path):
    _, client, path_a, path_b = _env(tmp_path)

    # 首次运行已播种默认仓库（vaultA）
    cur = client.get("/api/vaults/current").json()
    assert cur["path"] == path_a
    assert cur["is_current"] is True
    assert cur["db_path"]

    # 登记仓库B
    r = _register(client, path_b, name="仓库B")
    assert r.status_code == 200
    vid = r.json()["id"]
    assert r.json()["name"] == "仓库B"

    data = client.get("/api/vaults").json()
    ids = [v["id"] for v in data["vaults"]]
    assert vid in ids
    assert data["current_id"] == cur["id"]


def test_switch_reads_new_vault_and_isolates_data(tmp_path):
    _, client, path_a, path_b = _env(tmp_path)

    r = _register(client, path_b, name="仓库B")
    vid = r.json()["id"]
    _switch(client, vid)

    cur = client.get("/api/vaults/current").json()
    assert cur["path"] == path_b

    # 切换后 app.state.tasks 指向 B 的引擎；向 B 的库种一棵树，forest 应读到
    from sqlalchemy.orm import Session
    tm_b = client.app.state.tasks
    # 数据隔离：B 的 db 落在其独立数据目录，与默认仓库(A)的 db 不同
    vault_b_record = client.app.state.repos.get(client.app.state.repos.current_id)
    assert str(tm_b.db_path) == str(Path(vault_b_record.data_dir) / "noteagent.db")
    assert str(tm_b.db_path) != str(client.app.state.repos.data_root / "noteagent.db")
    with Session(tm_b.pipeline().engine) as session:
        session.add(Tree(id="T-B", title="仓库B树", status="in_progress", confidence=0.9, run_id="rB"))
        session.commit()
    forest = client.get("/api/forest").json()
    assert forest["count"] >= 1
    assert any(t["title"] == "仓库B树" for t in forest["trees"])


def test_switch_blocked_when_active_run(tmp_path):
    tm, client, _, path_b = _env(tmp_path)
    tm.pipeline().rm.start_run(scope="test", trigger="test")  # 制造 active run
    _register(client, path_b)
    # 需要先拿到 B 的 id
    b = next(v for v in client.get("/api/vaults").json()["vaults"] if v["path"] == path_b)
    r = _switch(client, b["id"])
    assert r.status_code == 409
    assert r.json()["code"] == "run_active"


def test_register_invalid_and_duplicate(tmp_path):
    _, client, _, path_b = _env(tmp_path)
    data_root = client.app.state.repos.data_root

    # 不存在的目录 → 422
    bad = client.post("/api/vaults/register", json={"path": str(tmp_path / "no_such")})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_vault_dir"

    # 数据目录本身 → 422
    selfdir = client.post("/api/vaults/register", json={"path": str(data_root)})
    assert selfdir.status_code == 422 and selfdir.json()["code"] == "invalid_vault_dir"

    # 重复登记 → 409
    assert _register(client, path_b).status_code == 200
    dup = _register(client, path_b)
    assert dup.status_code == 409 and dup.json()["code"] == "vault_exists"


def test_switch_requires_confirmation(tmp_path):
    _, client, _, path_b = _env(tmp_path)
    _register(client, path_b)
    b = next(v for v in client.get("/api/vaults").json()["vaults"] if v["path"] == path_b)
    r = client.post(f"/api/vaults/{b['id']}/switch", json={"confirm": False})
    assert r.status_code == 409
    assert r.json()["code"] == "confirmation_required"


def test_remove_current_blocked_and_noncurrent_ok(tmp_path):
    _, client, path_a, path_b = _env(tmp_path)
    _register(client, path_b)
    cur = client.get("/api/vaults/current").json()

    # 移除当前仓库 → 409
    r = client.delete(f"/api/vaults/{cur['id']}")
    assert r.status_code == 409 and r.json()["code"] == "current_vault"

    # 切到 B 后移除 A（非当前）→ 成功
    b = next(v for v in client.get("/api/vaults").json()["vaults"] if v["path"] == path_b)
    _switch(client, b["id"])
    a = next(v for v in client.get("/api/vaults").json()["vaults"] if v["path"] == path_a)
    r = client.delete(f"/api/vaults/{a['id']}")
    assert r.status_code == 200
    ids = [v["id"] for v in client.get("/api/vaults").json()["vaults"]]
    assert a["id"] not in ids


def test_registry_persists_roundtrip(tmp_path):
    _, client, _, path_b = _env(tmp_path)
    _register(client, path_b, name="持久仓库")
    b = next(v for v in client.get("/api/vaults").json()["vaults"] if v["path"] == path_b)
    _switch(client, b["id"])

    from src.services.vault_registry import VaultRegistry
    data_root = client.app.state.repos.data_root
    reloaded = VaultRegistry(data_root)
    assert reloaded.current_id == b["id"]
    assert reloaded.get(b["id"]).name == "持久仓库"
    assert reloaded.registry_path.exists()
