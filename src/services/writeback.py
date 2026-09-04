"""M7 双写回服务：标签回写 + 双链写回（REQUIREMENTS.md FR-10，DESIGN.md M7）。

遵循项目最高安全边界（AGENTS.md）：修改原笔记仅限本通道，流程为
Web 逐篇 diff 预览 → 确认 → 只增不删 → 时间戳备份 → 幂等原子写。

- 写回内容全部由 DB 中的确定性数据生成（Extraction.candidate_tags / 已验证树结构），
  不新增 LLM 调用（写回安全机制不交给 LLM）。
- 复用 M0 基础设施 SafeWriter（diff/原子写/备份/超 vault 拦截）与 BackupManager（保留 N 次 + 恢复）。
- 只增不删：tags 只并入新增标签，links 只追加关联小节，绝不删改原笔记已有内容。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.data.parser import NoteParser
from src.infra.backup import BackupManager
from src.infra.safe_writer import SafeWriter
from src.models.orm import Extraction, Note, Tree, TreeNode, WritebackItem, WritebackJob

# 写回备份与产物共用同一目录（DESIGN.md 4.3 data/backups/<时间戳>/）。
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


def _now_store() -> str:
    from src.models.orm import now_iso
    return now_iso()


def _safe_wiki(display: str) -> str:
    """把笔记标题/文件名转义为安全的 Obsidian Wiki 链接显示文本。"""
    return display.replace("[[", "").replace("]]", "").replace("\n", " ").strip()


def _link_for(path: str, display: str) -> str:
    """生成 `[[路径|别名]]` 双链；保留完整相对路径便于 Obsidian 解析，特殊字符用别名规避。"""
    path = path.replace("[[", "").replace("]]", "").strip()
    alias = _safe_wiki(display) or path.split("/")[-1] or path
    return f"[[{path}|{alias}]]" if alias != path and alias else f"[[{path}]]"


class WritebackError(ValueError):
    pass


def writer_for(vault_dir: Path | str, backup_dir: Path | str, keep: int = 5) -> SafeWriter:
    return SafeWriter(BackupManager(backup_dir, keep=keep), Path(vault_dir))


def _with_frontmatter_tags(content: str, tags: list[str]) -> str:
    """只增不删地并入 tags 到 frontmatter，返回新内容。无 frontmatter 则新建一个。"""
    parser = NoteParser()
    fm = parser.parse_metadata(content)
    existing = fm.get("tags") or []
    if isinstance(existing, str):
        existing = [t.strip() for t in re.split(r"[,\s]+", existing) if t.strip()]
    merged: list[str] = []
    for tag in list(existing) + list(tags):
        if tag not in merged and tag:
            merged.append(tag)
    fm["tags"] = merged
    fm_text = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
    match = re.match(r"\A---\s*\n", content)
    if match:
        rest = re.sub(r"\A---\s*\n.*?(?:\n---\s*(?:\n|\Z))", "", content, count=1, flags=re.DOTALL).lstrip("\n")
        return f"---\n{fm_text}\n---\n\n{rest}"
    return f"---\n{fm_text}\n---\n\n{content.strip()}\n"


def _with_links_section(content: str, kind: str, links: list[str]) -> str:
    """追加双链小节（只增不删），返回新内容。已存在同名小节则并入，避免重复。"""
    heading = "## NoteAgent 关联笔记"
    anchor = "<!-- NoteAgent:links -->"
    remain_index = content.find(anchor)
    if remain_index != -1:
        remain = content[remain_index:]
    else:
        remain = ""
    base = content if not remain_index else content[:remain_index].rstrip("\n") + "\n\n"

    section_marker = f'{heading}\n\n{anchor}'
    marker_pos = base.find(section_marker)
    existing_links: list[str] = []
    if marker_pos != -1:
        block = base[marker_pos:]
        for m in _WIKILINK.finditer(block[: block.find("\n## ") if block.find("\n## ") != -1 else len(block)]):
            link = m.group(0)
            if link not in existing_links:
                existing_links.append(link)
        base = base.replace(block, "").rstrip("\n") + "\n\n"
    inline_links = re.findall(r"\[\[[^\]]+\]\]", base)
    merged = list(existing_links) + [l for l in links if l not in inline_links and l not in existing_links]
    if not merged:
        return content  # 无新增双链，保持原样（幂等）
    section = f"{heading}\n\n{anchor}\n" + "\n".join(f"- {l}" for l in merged) + "\n"
    return base.rstrip("\n") + "\n\n" + section + remain.rstrip("\n") + ("\n" if remain else "")


def plan_tags(
    session: Session,
    vault_dir: Path | str,
    backup_dir: Path | str,
    *,
    note_ids: list[str] | None = None,
    keep: int = 5,
) -> WritebackJob:
    """标签写回预览：为有候选标签的笔记生成并入 tags 的 diff 计划。"""
    writer = writer_for(vault_dir, backup_dir, keep)
    notes = {n.id: n for n in session.scalars(select(Note))}
    if note_ids:
        notes = {k: v for k, v in notes.items() if k in set(note_ids)}
    extract_map: dict[str, Extraction] = {}
    for ex in session.scalars(select(Extraction).order_by(Extraction.id.desc())):
        extract_map.setdefault(ex.note_id, ex)
    job = WritebackJob(kind="tags", status="previewed")
    session.add(job); session.flush()
    items: list[WritebackItem] = []
    for note_id, note in notes.items():
        ex = extract_map.get(note_id)
        candidate_tags = list(json.loads(ex.candidate_tags)) if ex and ex.candidate_tags else []
        candidate_tags = [t for t in candidate_tags if t and not str(t).startswith("#")]
        if not candidate_tags:
            continue
        vault_path = Path(vault_dir) / note.path
        if not vault_path.is_file():
            continue
        old = vault_path.read_text(encoding="utf-8")
        new = _with_frontmatter_tags(old, candidate_tags)
        if new == old:
            continue
        items.append(WritebackItem(
            job_id=job.id, kind="tags", note_id=note_id, path=note.path,
            content=new, diff_text=writer.preview(note.path, new), preview_hash=writer.preview_hash(note.path, new),
        ))
    session.add_all(items)
    session.commit()
    return job


def plan_links(
    session: Session,
    vault_dir: Path | str,
    backup_dir: Path | str,
    *,
    note_ids: list[str] | None = None,
    keep: int = 5,
) -> WritebackJob:
    """双链写回预览：为已验证树的成员笔记生成"关联笔记"双链计划。

    仅处理 verified 树（追加原则：已验证结构作为后续运行的骨干）；未验证/草稿树不写回。
    """
    writer = writer_for(vault_dir, backup_dir, keep)
    notes = {n.id: n for n in session.scalars(select(Note))}
    if note_ids:
        notes = {k: v for k, v in notes.items() if k in set(note_ids)}
    # 汇总已验证树中每个笔记应链接的同树其他成员笔记
    links_by_note: dict[str, list[str]] = {}
    tree_nodes: dict[str, list[tuple[int, str]]] = {}
    for n in session.scalars(select(TreeNode)):
        tree_nodes.setdefault(n.tree_id, []).append((n.order, n.note_id))
    for tree in session.scalars(select(Tree).where(Tree.verified.is_(True))):
        members: dict[str, int] = {}
        for order, note_id in tree_nodes.get(tree.id, []):
            if note_id in notes:
                members.setdefault(note_id, order)
        ids = list(members)
        for note_id in ids:
            others = [n for n in ids if n != note_id]
            if not others:
                continue
            if note_id not in links_by_note:
                links_by_note[note_id] = []
            for other_id in others:
                other = notes.get(other_id)
                if other is None:
                    continue
                link = _link_for(other.path, other.filename or other.path)
                if link not in links_by_note[note_id]:
                    links_by_note[note_id].append(link)
    job = WritebackJob(kind="links", status="previewed")
    session.add(job); session.flush()
    items: list[WritebackItem] = []
    for note_id, links in links_by_note.items():
        note = notes.get(note_id)
        if note is None:
            continue
        vault_path = Path(vault_dir) / note.path
        if not vault_path.is_file():
            continue
        old = vault_path.read_text(encoding="utf-8")
        new = _with_links_section(old, "links", links)
        if new == old:
            continue
        items.append(WritebackItem(
            job_id=job.id, kind="links", note_id=note_id, path=note.path,
            content=new, diff_text=writer.preview(note.path, new), preview_hash=writer.preview_hash(note.path, new),
        ))
    session.add_all(items)
    session.commit()
    return job


def plan(
    session: Session,
    vault_dir: Path | str,
    backup_dir: Path | str,
    kind: str,
    *,
    note_ids: list[str] | None = None,
    keep: int = 5,
) -> WritebackJob:
    if kind == "tags":
        return plan_tags(session, vault_dir, backup_dir, note_ids=note_ids, keep=keep)
    if kind == "links":
        return plan_links(session, vault_dir, backup_dir, note_ids=note_ids, keep=keep)
    raise WritebackError(f"不支持的写回类型: {kind}")


def confirm(
    session: Session,
    job_id: int,
    vault_dir: Path | str,
    backup_dir: Path | str,
    *,
    keep: int = 5,
) -> WritebackJob:
    """确认并执行写回：逐 item 用 SafeWriter.apply 原子写（写前备份、令牌校验、幂等）。"""
    job = session.get(WritebackJob, job_id)
    if job is None:
        raise LookupError(f"写回任务不存在: {job_id}")
    if job.status == "applied":
        return job  # 幂等：已全部应用，直接返回
    writer = writer_for(vault_dir, backup_dir, keep)
    items = list(session.scalars(select(WritebackItem).where(WritebackItem.job_id == job_id)))
    ok, failed = [], []
    for item in items:
        if item.applied:
            ok.append(item)
            continue
        try:
            # SafeWriter.apply 内部执行：preview_hash 令牌校验（检测外部变更）→ 备份 → 原子替换。
            applied = writer.apply(item.path, item.content, confirm=True, preview_hash=item.preview_hash)
            item.applied = True
            ok.append(item)
        except Exception as exc:
            item.error = str(exc)
            failed.append(item)
    if ok and failed:
        job.status = "partially_applied"
    elif ok:
        job.status = "applied"
    else:
        job.status = "failed"
    job.applied_at = _now_store()
    session.commit()
    return job


def item_out(item: WritebackItem) -> dict[str, Any]:
    return {"id": item.id, "kind": item.kind, "note_id": item.note_id, "path": item.path,
            "diff": item.diff_text, "applied": item.applied, "error": item.error}


def job_out(job: WritebackJob, items: list[WritebackItem]) -> dict[str, Any]:
    return {"id": job.id, "kind": job.kind, "status": job.status, "created_at": job.created_at,
            "applied_at": job.applied_at, "error": job.error,
            "count": len(items), "items": [item_out(i) for i in items]}