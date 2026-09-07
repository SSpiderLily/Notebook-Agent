"""M9 可观测查询接口。"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from src.api.task_manager import TaskManager
from src.models.orm import Run, Stage, LLMCall, Note

router = APIRouter(prefix="/api/observe", tags=["observe"])

def tm(request: Request) -> TaskManager: return request.app.state.tasks

def engine(request: Request): return tm(request).pipeline().engine

def run_json(r, stages=()):
    return {"id":r.id,"status":r.status,"scope":r.scope,"trigger":r.trigger,"started_at":r.started_at,"finished_at":r.finished_at,"cost_est":r.cost_est,"stages":[{"stage":s.stage,"status":s.status,"items_total":s.items_total,"items_done":s.items_done,"items_failed":s.items_failed,"error":s.error} for s in stages]}

@router.get("/runs")
def runs(request: Request, status: str|None=None, limit: int=50, offset: int=0):
    limit=max(1,min(limit,200)); offset=max(0,offset)
    with Session(engine(request)) as s:
        q=select(Run).order_by(Run.started_at.desc()).offset(offset).limit(limit)
        if status: q=q.where(Run.status==status)
        rows=list(s.scalars(q)); return [run_json(r) for r in rows]

@router.get("/runs/{run_id}")
def run_detail(run_id: str, request: Request):
    with Session(engine(request)) as s:
        r=s.get(Run,run_id)
        if not r: raise HTTPException(404, detail={"code":"run_not_found","message":"Run 不存在","detail":None})
        return run_json(r,s.scalars(select(Stage).where(Stage.run_id==run_id)).all())

@router.get("/llm-calls")
def llm_calls(request: Request, run_id: str|None=None, stage: str|None=None, status: str|None=None, limit:int=50, offset:int=0):
    with Session(engine(request)) as s:
        q=select(LLMCall).order_by(LLMCall.id.desc()).offset(max(offset,0)).limit(max(1,min(limit,200)))
        if run_id:q=q.where(LLMCall.run_id==run_id)
        if stage:q=q.where(LLMCall.stage==stage)
        if status:q=q.where(LLMCall.status==status)
        return [{"id":x.id,"run_id":x.run_id,"stage":x.stage,"caller":x.caller,"model":x.model,"prompt_tokens":x.prompt_tokens,"completion_tokens":x.completion_tokens,"cost_est":x.cost_est,"retries":x.retries,"status":x.status,"digest":x.digest} for x in s.scalars(q)]

@router.get("/failures")
def failures(request: Request):
    with Session(engine(request)) as s:
        stages=s.scalars(select(Stage).where((Stage.items_failed>0)|(Stage.status=="failed"))).all()
        calls=s.scalars(select(LLMCall).where(LLMCall.status!="ok")).all()
        return {"stages":[{"run_id":x.run_id,"stage":x.stage,"failed":x.items_failed,"error":x.error} for x in stages],"llm_calls":[{"id":x.id,"run_id":x.run_id,"stage":x.stage,"status":x.status} for x in calls]}

@router.get("/vault-status")
def vault_status(request: Request):
    with Session(engine(request)) as s:
        rows=s.scalars(select(Note)).all(); counts={k:sum(1 for n in rows if n.vault_status==k) for k in ("active","missing","ignored")}
        return {"counts":counts,"total":len(rows),"notes":[{"id":n.id,"path":n.path,"status":n.vault_status} for n in rows if n.vault_status!="active"]}
