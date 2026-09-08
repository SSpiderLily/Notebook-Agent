"""仓库(vault)管理与切换接口（新）。

- 列表 / 当前仓库 / 登记 / 切换 / 移除
- 切换与移除为状态变更，要求请求体带 `confirm:true`（与 reset/writeback 破坏性端点一致）
- 切换在「当前仓库有 active run」时返回 409，避免旧任务继续写旧仓库
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from src.api.schemas import VaultRegisterRequest, VaultSwitchRequest
from src.services.vault_registry import Vault, VaultError, VaultRegistry

router = APIRouter(prefix="/api/vaults", tags=["vaults"])


def get_registry(request: Request) -> VaultRegistry:
    return request.app.state.repos


def _vault_out(v: Vault, *, is_current: bool = False) -> dict:
    out = v.to_dict()
    out["is_current"] = is_current
    return out


def _confirmation_required() -> None:
    raise HTTPException(
        status_code=409,
        detail={"code": "confirmation_required", "message": "需要确认才能执行该操作", "detail": None},
    )


def _handle(exc: VaultError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": str(exc), "detail": None},
    )


@router.get("")
def list_vaults(repos: VaultRegistry = Depends(get_registry)):
    return {
        "vaults": [_vault_out(v, is_current=(v.id == repos.current_id)) for v in repos.list()],
        "current_id": repos.current_id,
        "count": len(repos.list()),
    }


@router.get("/current")
def current_vault(repos: VaultRegistry = Depends(get_registry)):
    v = repos.current_vault()
    if v is None:
        raise HTTPException(status_code=404, detail={"code": "vault_not_found", "message": "未登记任何仓库", "detail": None})
    tm = repos.current()
    return {
        **_vault_out(v, is_current=True),
        "db_path": str(tm.db_path),
    }


@router.post("/register")
def register_vault(body: VaultRegisterRequest, repos: VaultRegistry = Depends(get_registry)):
    try:
        v = repos.register(body.path, name=body.name)
    except VaultError as exc:
        raise _handle(exc)
    return _vault_out(v)


@router.post("/{vault_id}/switch")
def switch_vault(vault_id: str, body: VaultSwitchRequest, repos: VaultRegistry = Depends(get_registry)):
    if not body.confirm:
        _confirmation_required()
    try:
        v = repos.switch(vault_id)
    except VaultError as exc:
        raise _handle(exc)
    return {**_vault_out(v, is_current=True), "db_path": str(repos.current().db_path)}


@router.delete("/{vault_id}")
def remove_vault(vault_id: str, repos: VaultRegistry = Depends(get_registry)):
    try:
        repos.remove(vault_id)
    except VaultError as exc:
        raise _handle(exc)
    return {"ok": True, "removed": vault_id}