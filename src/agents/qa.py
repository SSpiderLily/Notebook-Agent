from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.infra.llm_gateway import LLMGateway
from src.models.orm import ChatMessage, ChatSession, Event, Note, Tree, TreeNode, now_iso


class QAService:
    def __init__(self, engine, gateway: LLMGateway, vault: Path | str | None = None):
        self.engine, self.gateway, self.vault = engine, gateway, Path(vault) if vault else None

    def create_session(self, title="新会话"):
        with Session(self.engine) as db:
            item = ChatSession(id=str(uuid.uuid4()), title=title or "新会话")
            db.add(item); db.commit(); db.refresh(item)
            return self.session_out(item)

    def session_out(self, item):
        return {"id": item.id, "title": item.title, "summary": item.summary, "created_at": item.created_at, "updated_at": item.updated_at}

    def messages(self, session_id):
        with Session(self.engine) as db:
            if db.get(ChatSession, session_id) is None: raise LookupError(session_id)
            rows = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id)).all()
            return [self.message_out(x) for x in rows]

    def message_out(self, item):
        return {"id": item.id, "role": item.role, "content": item.content, "citations": json.loads(item.citations or "[]"), "created_at": item.created_at}

    def search_forest(self, query):
        with Session(self.engine) as db:
            term = f"%{query}%"
            trees = db.scalars(select(Tree).where(or_(Tree.title.ilike(term), Tree.narrative.ilike(term)))).all()
            return [{"id": t.id, "title": t.title, "status": t.status, "confidence": t.confidence} for t in trees]

    def get_tree(self, tree_id):
        with Session(self.engine) as db:
            tree = db.get(Tree, tree_id)
            if not tree: return None
            nodes = db.scalars(select(TreeNode).where(TreeNode.tree_id == tree_id).order_by(TreeNode.order)).all()
            return {"id": tree.id, "title": tree.title, "status": tree.status, "narrative": tree.narrative, "nodes": [{"event_id": n.event_id, "note_id": n.note_id, "order": n.order} for n in nodes]}

    def list_dangling(self):
        with Session(self.engine) as db:
            return [{"id": t.id, "title": t.title, "status": t.status} for t in db.scalars(select(Tree).where(Tree.status.in_(("dangling_confirmed", "dangling_suspected")))).all()]

    def list_recent_runs(self):
        from src.models.orm import Run
        with Session(self.engine) as db:
            return [{"id": r.id, "status": r.status, "started_at": r.started_at} for r in db.scalars(select(Run).order_by(Run.started_at.desc()).limit(10)).all()]

    def _citations(self, text, hits):
        cites = []
        for hit in hits[:5]:
            cites.append({"type": "tree", "id": hit["id"], "title": hit["title"], "uri": f"obsidian://open?vault=NoteAgent&file=_noteagent%2Ftrees%2F{hit['id']}.md"})
        return cites

    def answer(self, session_id, prompt):
        with Session(self.engine) as db:
            session = db.get(ChatSession, session_id)
            if not session: raise LookupError(session_id)
            history = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.id.desc()).limit(12)).all()[::-1]
            hits = self.search_forest(prompt)
            context = "\n".join(f"{x['title']} ({x['status']})" for x in hits)
            transcript = "\n".join(f"{x.role}: {x.content}" for x in history)
            text = self.gateway.chat(f"你是 NoteAgent 问答助手。基于森林资料回答，简洁且不要编造。\n森林:\n{context}\n历史:\n{transcript}\n问题: {prompt}")
            citations = self._citations(text, hits)
            db.add(ChatMessage(session_id=session_id, role="user", content=prompt, created_at=now_iso()))
            db.add(ChatMessage(session_id=session_id, role="assistant", content=text, citations=json.dumps(citations, ensure_ascii=False), created_at=now_iso()))
            session.updated_at = now_iso(); db.commit()
            return {"role": "assistant", "content": text, "citations": citations}

    def command(self, session_id, command):
        if command == "/clear":
            with Session(self.engine) as db:
                session = db.get(ChatSession, session_id)
                if not session: raise LookupError(session_id)
                db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete(); session.summary = ""; db.commit()
            return {"command": "clear", "messages": []}
        if command == "/export":
            msgs = self.messages(session_id)
            return {"command": "export", "markdown": "\n\n".join(f"**{m['role']}**\n{m['content']}" for m in msgs)}
        if command == "/regen":
            msgs = self.messages(session_id)
            previous = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), None)
            if not previous: return {"command": "regen", "content": "没有可重新生成的问题"}
            with Session(self.engine) as db:
                last = db.scalars(select(ChatMessage).where(ChatMessage.session_id == session_id, ChatMessage.role == "assistant").order_by(ChatMessage.id.desc())).first()
                if last: db.delete(last); db.commit()
            return self.answer(session_id, previous)
        raise ValueError("不支持的指令")
