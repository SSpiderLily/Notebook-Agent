#!/usr/bin/env python3
"""Record one atomic project-progress event and append the progress board.

This command deliberately requires explicit event metadata. It never commits,
pushes, or edits files outside the configured dev-log directory.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ALLOWED_STATUS = {"completed", "in_progress", "blocked"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--status", choices=sorted(_ALLOWED_STATUS), required=True)
    parser.add_argument("--tests", default="")
    parser.add_argument("--commit", default="")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--dev-log", type=Path, default=Path("dev-log"))
    return parser


def _validate(args: argparse.Namespace) -> None:
    if not _SAFE_ID.fullmatch(args.event_id):
        raise ValueError("event-id 只能包含字母、数字、点、下划线和连字符")
    if not args.stage.strip() or not args.title.strip() or not args.summary.strip():
        raise ValueError("stage、title、summary 不能为空")
    if args.status == "completed" and not args.tests.strip():
        raise ValueError("completed 事件必须提供 --tests")
    try:
        date.fromisoformat(args.date)
    except ValueError as exc:
        raise ValueError("date 必须是 YYYY-MM-DD") from exc


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


def _event_filename(event_id: str, title: str) -> str:
    slug = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", title, flags=re.UNICODE).strip("-") or "事件"
    return f"{event_id}-{slug}.md"


def _board_entry(args: argparse.Namespace, filename: str) -> str:
    status = {"completed": "完成", "in_progress": "进行中", "blocked": "阻塞"}[args.status]
    return (
        f"- [{args.date}] **{args.stage}：{args.title}**：{args.summary}\n"
        f"  来源：[[{Path(filename).stem}]]\n"
        f"  测试：{args.tests or '未执行'}\n"
        f"  Commit：{args.commit or '未提交'}\n"
        f"  状态：{status}\n"
    )


def record(args: argparse.Namespace) -> tuple[Path, bool]:
    _validate(args)
    root = args.dev_log.resolve()
    if not root.is_dir():
        raise ValueError(f"dev-log 目录不存在: {root}")
    board = (root / "进度看板.md").resolve()
    if not board.is_file():
        raise ValueError(f"进度看板不存在: {board}")
    filename = _event_filename(args.event_id, args.title)
    event_path = (root / filename).resolve()
    if root not in event_path.parents:
        raise ValueError("事件文档路径越界")
    existing = event_path.read_text(encoding="utf-8") if event_path.exists() else None
    marker = f"事件 ID：{args.event_id}"
    if existing is not None:
        if marker not in existing:
            raise ValueError(f"目标事件文档已存在但事件 ID 不匹配: {event_path.name}")
        return event_path, False

    event_content = (
        f"# {args.title}\n\n"
        f"- 事件 ID：{args.event_id}\n"
        f"- 阶段：{args.stage}\n"
        f"- 日期：{args.date}\n"
        f"- 状态：{args.status}\n\n"
        f"## 事件内容\n\n{args.summary}\n\n"
        f"## 验证\n\n{args.tests or '未执行'}\n\n"
        f"## 提交\n\n{args.commit or '未提交'}\n\n"
        "## 归属\n\n"
        "- 进度看板：[[进度看板]]\n"
    )
    board_content = board.read_text(encoding="utf-8")
    if args.event_id in board_content:
        raise ValueError("看板已包含该事件 ID，但事件文档不存在，拒绝产生不一致")
    separator = "" if board_content.endswith("\n") else "\n"
    new_board = board_content + separator + _board_entry(args, filename)
    _atomic_write(event_path, event_content)
    try:
        _atomic_write(board, new_board)
    except Exception:
        event_path.unlink(missing_ok=True)
        raise
    return event_path, True


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        path, created = record(args)
    except (OSError, ValueError) as exc:
        print(f"record-progress: {exc}", file=sys.stderr)
        return 2
    print(f"{'created' if created else 'unchanged'}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
