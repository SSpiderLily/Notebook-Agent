"""仓库(vault)注册表：多仓库登记、数据隔离、运行时切换与持久化。

本地 Web 服务默认只服务一个 vault（`Settings.vault_dir`）。为支持"切换仓库"，
用本注册表维护一个持久化的仓库清单（`data/vaults.json`），每个仓库拥有独立的
数据目录（`data/<vault_id>/`），互不污染数据库/向量库/运行记录/备份。

切换机制：替换 `app.state.tasks` 指向目标仓库的 `TaskManager`（现有路由每次请求
都从 `app.state.tasks` 读取，替换后零改动即可读到新仓库）。切换前必须确认当前仓库
没有进行中的整理任务（active run），否则拒绝，避免旧任务继续写旧仓库。

安全边界：`register` 只接受已存在的本地目录，拒绝 data 目录/日志/.git 等自引用路径；
`switch`/破坏性操作需在路由层要求 `confirm:true`；`remove` 只移除注册项，不删除
任何 vault 内容或其数据目录。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


class VaultError(RuntimeError):
    """仓库相关异常基类。"""

    code = "vault_error"
    status = 400


class VaultNotFoundError(VaultError):
    code = "vault_not_found"
    status = 404


class VaultExistsError(VaultError):
    code = "vault_exists"
    status = 409


class InvalidVaultPathError(VaultError):
    code = "invalid_vault_dir"
    status = 422


class RunActiveError(VaultError):
    code = "run_active"
    status = 409


class CurrentVaultError(VaultError):
    code = "current_vault"
    status = 409


@dataclass
class Vault:
    """一个已登记仓库。"""

    id: str
    path: str
    name: str
    data_dir: str
    added_at: str = ""
    is_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "name": self.name,
            "data_dir": self.data_dir,
            "added_at": self.added_at,
            "is_default": self.is_default,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Vault":
        return cls(
            id=d["id"],
            path=d["path"],
            name=d.get("name", ""),
            data_dir=d.get("data_dir", ""),
            added_at=d.get("added_at", ""),
            is_default=bool(d.get("is_default", False)),
        )


def _vault_id(path: Path) -> str:
    """由绝对路径生成稳定的仓库 id：路径名 slug + 路径短哈希。"""
    base = re.sub(r"[^a-z0-9_-]+", "-", path.name.lower()).strip("-") or "vault"
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


class VaultRegistry:
    """持久化的多仓库注册表；负责登记、隔离、切换与当前 TaskManager 的持有。"""

    def __init__(
        self,
        data_root: Path | str,
        *,
        tm_factory: Callable[[Vault], Any] | None = None,
        app: Any | None = None,
    ) -> None:
        self.data_root = Path(data_root).resolve()
        self.registry_path = self.data_root / "vaults.json"
        self.tm_factory = tm_factory
        self._app = app
        self._store: dict[str, Any] = {"current_id": None, "vaults": {}}
        self._current_tm: Any = None
        self._load()

    # ── 存储读写 ──
    def _load(self) -> None:
        if self.registry_path.exists():
            try:
                data = json.loads(self.registry_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._store["current_id"] = data.get("current_id")
                    self._store["vaults"] = {k: v for k, v in data.get("vaults", {}).items()}
            except (json.JSONDecodeError, OSError):
                # 损坏的注册表按空处理，不影响启动
                self._store = {"current_id": None, "vaults": {}}

    def _persist(self) -> None:
        _atomic_write(self.registry_path, json.dumps(self._store, ensure_ascii=False, indent=2))

    # ── 查询 ──
    def list(self) -> list[Vault]:
        vaults = [Vault.from_dict(d) for d in self._store["vaults"].values()]
        return sorted(vaults, key=lambda v: (not v.is_default, v.name or v.path))

    def get(self, vault_id: str) -> Vault | None:
        d = self._store["vaults"].get(vault_id)
        return Vault.from_dict(d) if d else None

    @property
    def current_id(self) -> str | None:
        return self._store.get("current_id")

    def current_vault(self) -> Vault | None:
        return self.get(self.current_id) if self.current_id else None

    def current(self) -> Any:
        """返回当前仓库的 TaskManager（惰性构造）。"""
        if self._current_tm is None:
            self.ensure_current()
        return self._current_tm

    def ensure_current(self) -> None:
        """按 current_id 构造当前仓库的 TM（供注册表已有 current 时恢复）。"""
        cur = self.current_vault()
        if cur is None:
            raise VaultNotFoundError("未登记任何仓库")
        self._current_tm = self.tm_factory(cur)
        if self._app is not None:
            self._app.state.tasks = self._current_tm

    def bind_current(self, tm: Any) -> None:
        """把已构造好的 TM 绑定为当前（复用调用方传入的实例，避免重复建库）。"""
        self._current_tm = tm
        if self._app is not None:
            self._app.state.tasks = tm

    # ── 登记 / 切换 / 移除 ──
    def seed_default(self, path: Path | str, *, data_dir: Path | str | None = None, name: str | None = None) -> Vault:
        """首次运行时把当前配置的 vault 播种为默认+当前仓库（沿用其现有数据目录）。"""
        vault = self._make_vault(path, data_dir=data_dir, name=name, is_default=True, allow_inside_data=True)
        self._store["vaults"][vault.id] = vault.to_dict()
        self._store["current_id"] = vault.id
        self._persist()
        return vault

    def register(self, path: Path | str, *, name: str | None = None) -> Vault:
        vault = self._make_vault(path, name=name)
        if vault.id in self._store["vaults"]:
            raise VaultExistsError(f"仓库已登记: {vault.path}")
        self._store["vaults"][vault.id] = vault.to_dict()
        self._persist()
        return vault

    def switch(self, vault_id: str) -> Vault:
        vault = self.get(vault_id)
        if vault is None:
            raise VaultNotFoundError(f"仓库不存在: {vault_id}")
        if self._current_tm is not None and self._current_tm.current() is not None:
            raise RunActiveError("当前仓库有进行中的任务，请先等待完成或取消后再切换")
        new_tm = self.tm_factory(vault)
        self._store["current_id"] = vault.id
        self._persist()
        self._current_tm = new_tm
        if self._app is not None:
            self._app.state.tasks = new_tm
        return vault

    def remove(self, vault_id: str) -> None:
        if vault_id not in self._store["vaults"]:
            raise VaultNotFoundError(f"仓库不存在: {vault_id}")
        if vault_id == self.current_id:
            raise CurrentVaultError("当前仓库不能移除，请先切换到其它仓库")
        del self._store["vaults"][vault_id]
        self._persist()

    # ── 内部 ──
    def _make_vault(self, path: Path | str, *, data_dir: Path | str | None = None, name: str | None = None, is_default: bool = False, allow_inside_data: bool = False) -> Vault:
        resolved = Path(path).expanduser().resolve()
        if not resolved.is_dir():
            raise InvalidVaultPathError(f"路径不是存在的目录: {resolved}")
        if not allow_inside_data and (self.data_root == resolved or self.data_root in resolved.parents):
            raise InvalidVaultPathError("不允许将数据目录或其内部路径登记为仓库")
        if any(part in {".git", "logs"} for part in resolved.parts):
            raise InvalidVaultPathError("不允许将 .git / logs 目录登记为仓库")
        vid = _vault_id(resolved)
        if data_dir is None:
            data_dir = self.data_root / vid
        target = Path(data_dir)
        target.mkdir(parents=True, exist_ok=True)
        return Vault(
            id=vid,
            path=str(resolved),
            name=(name or resolved.name).strip() or resolved.name,
            data_dir=str(target.resolve()),
            added_at=_now(),
            is_default=is_default,
        )
