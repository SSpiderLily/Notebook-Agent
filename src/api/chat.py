from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from src.agents.qa import QAService
from src.api.schemas import ChatMessageRequest, ChatSessionRequest
from src.api.task_manager import TaskManager

router = APIRouter(prefix="/api/chat", tags=["chat"])

def service(request: Request):
    tm: TaskManager = request.app.state.tasks
    pipeline = tm.pipeline()
    return QAService(pipeline.engine, pipeline.gateway, pipeline.collector.vault)

def missing():
    return HTTPException(status_code=404, detail={"code":"session_not_found","message":"会话不存在","detail":None})

@router.post("/sessions")
def create(body: ChatSessionRequest, qa: QAService = Depends(service)):
    return qa.create_session(body.title)

@router.get("/sessions/{session_id}/messages")
def list_messages(session_id: str, qa: QAService = Depends(service)):
    try: return {"session_id": session_id, "messages": qa.messages(session_id)}
    except LookupError: raise missing()

@router.post("/sessions/{session_id}/messages")
def send(session_id: str, body: ChatMessageRequest, qa: QAService = Depends(service)):
    try:
        if body.content.startswith("/"): return qa.command(session_id, body.content.strip())
        return qa.answer(session_id, body.content)
    except LookupError: raise missing()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"code":"unsupported_command","message":str(exc),"detail":None}) from exc

@router.delete("/sessions/{session_id}")
def clear(session_id: str, qa: QAService = Depends(service)):
    try: return qa.command(session_id, "/clear")
    except LookupError: raise missing()
