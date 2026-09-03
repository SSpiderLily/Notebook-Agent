"""树页与森林总览 Markdown 渲染（DESIGN.md FR-6）。"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any


class BaseArtifactRenderer(ABC):
    """产物渲染抽象基类。"""

    @abstractmethod
    def render_tree(self, tree: Mapping[str, Any], nodes: Sequence[Mapping[str, Any]], events: Mapping[int, Mapping[str, Any]], notes: Mapping[str, Mapping[str, Any]]) -> str:
        raise NotImplementedError

    @abstractmethod
    def render_overview(self, trees: Sequence[Mapping[str, Any]], links: Mapping[str, str], run_id: str) -> str:
        raise NotImplementedError


def safe_filename(value: str, fallback: str = "tree") -> str:
    """把树标识转换为稳定且不越界的 Markdown 文件名。"""
    value = re.sub(r"[\\/:*?\"<>|\[\]#\n\r]+", "-", str(value)).strip(" .-")
    return value[:120] or fallback


def obsidian_link(note: Mapping[str, Any]) -> str:
    """生成可跳转原笔记的 Obsidian URI 与 Wiki 链接。"""
    path = str(note.get("path") or note.get("relative_path") or note.get("filename") or "")
    display = str(note.get("filename") or path.rsplit("/", 1)[-1] or path)
    encoded = path.replace("%", "%25").replace("#", "%23").replace("?", "%3F").replace(" ", "%20")
    wiki = path.replace("[", "\\[").replace("]", "\\]").replace("|", "\\|").replace("#", "\\#")
    return f"[{display}](obsidian://open?vault=&file={encoded}) · [[{wiki}]]"


class ArtifactRenderer(BaseArtifactRenderer):
    """使用确定性模板生成树页和森林总览。"""

    def render_tree(self, tree, nodes, events, notes) -> str:
        title = str(tree.get("title") or tree.get("id") or "未命名树")
        status = str(tree.get("status") or "in_progress")
        confidence = float(tree.get("confidence") or 0.0)
        lines = [f"# {title}", "", f"> 树 ID：`{tree.get('id', '')}`", f"> 状态：`{status}` · 置信度：{confidence:.2f}", ""]
        evidence = tree.get("evidence") or []
        if isinstance(evidence, str): evidence = [evidence]
        if evidence:
            lines += ["## 判定证据", ""] + [f"- {x}" for x in evidence] + [""]
        narrative = tree.get("narrative") or ""
        if narrative:
            lines += ["## 来龙去脉", "", str(narrative), ""]
        lines += ["## 路径", ""]
        ordered = sorted(nodes, key=lambda n: (n.get("order", 0), n.get("id", 0)))
        if not ordered:
            lines += ["_暂无节点。_", ""]
        for index, node in enumerate(ordered, 1):
            event = events.get(node.get("event_id"), {})
            content = str(event.get("content") or "未命名事件")
            lines.append(f"{index}. **{content}**")
            clues = []
            if event.get("time_clue"): clues.append(f"时间：{event['time_clue']}")
            if event.get("status_clue"): clues.append(f"状态线索：{event['status_clue']}")
            clues.append(f"置信度：{float(node.get('confidence') or 0.0):.2f}")
            lines.append(f"   - {' · '.join(clues)}")
            ev = node.get("evidence") or []
            if isinstance(ev, str): ev = [ev]
            for item in ev: lines.append(f"   - 证据：{item}")
            note = notes.get(node.get("note_id"))
            if note: lines.append(f"   - 来源：{obsidian_link(note)}")
        lines += ["", "---", "", "由 NoteAgent 自动生成。", ""]
        return "\n".join(lines)

    def render_overview(self, trees, links, run_id) -> str:
        ordered = sorted(trees, key=lambda t: (not str(t.get("status", "")).startswith("dangling"), float(t.get("confidence") or 0.0), str(t.get("id", ""))))
        lines = ["# NoteAgent 森林总览", "", f"> Run：`{run_id}`", "", "| 树 | 状态 | 置信度 |", "|---|---|---:|"]
        if not ordered: lines.append("| _暂无树_ | - | - |")
        for tree in ordered:
            tid = str(tree.get("id", "")); title = str(tree.get("title") or tid)
            lines.append(f"| [{title}]({links.get(tid, f'trees/{safe_filename(tid)}.md')}) | `{tree.get('status', 'in_progress')}` | {float(tree.get('confidence') or 0.0):.2f} |")
        lines += ["", "断头路径优先展示；详情见各树页。", ""]
        return "\n".join(lines)

    def export(self, data: Any, output_path: str) -> str:
        """兼容 BaseExporter 的最小导出接口。"""
        from pathlib import Path
        path = Path(output_path)
        path.write_text(str(data), encoding="utf-8")
        return str(path)
