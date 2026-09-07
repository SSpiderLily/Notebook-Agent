#!/usr/bin/env python3
"""Record/update one error entry in the project error log (dev-log/错误记录.md).

Behavior (idempotent, atomic write, mirrors scripts/record-progress.py style):
- If the error ID does not exist and --status=open: prepend a new 「待解决」 entry
  to the 记录 section and refresh the 状态速览 table.
- If the error ID exists and --status=fixed: flip its 状态 to 「已解决 ✅」 and fill
  in 根因/解决/验证/提交 fields when provided.
- Existing resolved entries can have their fields updated by re-passing them.

It never commits or pushes, and only writes inside the configured dev-log directory.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

_SAFE_ID = re.compile(r"^E-\d{3,}$")
_ALLOWED_STATUS = {"open", "fixed"}
_FIELD_KEYS = ("错误 ID", "日期", "模块", "现象", "根因", "解决", "验证", "提交", "状态", "看板")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--error-id", required=True)
    parser.add_argument("--status", choices=sorted(_ALLOWED_STATUS), required=True)
    parser.add_argument("--module", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--symptom", default="")
    parser.add_argument("--cause", default="")
    parser.add_argument("--fix", default="")
    parser.add_argument("--verification", default="")
    parser.add_argument("--commit", default="")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--dev-log", type=Path, default=Path("dev-log"))
    parser.add_argument("--log", default="错误记录.md")
    return parser


def _validate(args: argparse.Namespace) -> None:
    if not _SAFE_ID.fullmatch(args.error_id):
        raise ValueError("error-id 必须形如 E-001")
    try:
        date.fromisoformat(args.date)
    except ValueError as exc:
        raise ValueError("date 必须是 YYYY-MM-DD") from exc
    if args.status == "open" and not args.symptom.strip():
        raise ValueError("新增 open 条目必须提供 --symptom")


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


def _entry_text(args: argparse.Namespace) -> list[str]:
    st = "已解决 ✅" if args.status == "fixed" else "待解决"
    title = args.title or args.error_id
    lines = [
        f"### {args.error_id} — {title}",
        f"- **错误 ID**：{args.error_id}",
        f"- **日期**：{args.date}",
        f"- **模块**：{args.module or '未填'}",
        f"- **现象**：{args.symptom or '未填'}",
        f"- **根因**：{args.cause or '待排查'}",
        f"- **解决**：{args.fix or '未解决'}",
        f"- **验证**：{args.verification or '未验证'}",
        f"- **提交**：{args.commit or '未提交'}",
        f"- **状态**：{st}",
        f"- **看板**：[[进度看板]]",
        "",
    ]
    return lines


def _parse_entries(lines: list[str]) -> list[dict]:
    """Collect each `### E-XXX` block into a dict of fields (raw line strings)."""
    entries, current, cur_lines = [], None, []
    for line in lines:
        if line.startswith("### E-"):
            if current is not None:
                current["_lines"] = cur_lines
                entries.append(current)
            current = {"_id": line.split()[1]}
            cur_lines = []
        elif current is not None:
            cur_lines.append(line)
            m = re.match(r"^- \*\*状态\*\*：(.*)$", line.strip())
            if m:
                current["_status"] = m.group(1).strip()
    if current is not None:
        current["_lines"] = cur_lines
        entries.append(current)
    return entries


def _find_block(lines: list[str], eid: str) -> tuple[int, int] | None:
    start = None
    for i, line in enumerate(lines):
        if line.startswith(f"### {eid} "):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("### ") or lines[j].startswith("## "):
            end = j
            break
    return start, end


def _set_field(block: list[str], key: str, value: str, default: str) -> None:
    marker = f"- **{key}**："
    for i, line in enumerate(block):
        if line.startswith(marker):
            block[i] = f"{marker}{value}"
            return
    block.insert(-1 if block and block[-1].strip() == "" else len(block), f"{marker}{value}")


def _get_field(block: list[str], key: str) -> str:
    marker = f"- **{key}**："
    for line in block:
        if line.startswith(marker):
            return line[len(marker):]
    return ""


def _render_summary(lines: list[str]) -> list[str]:
    entries = _parse_entries(lines)
    table = ["## 状态速览", "", "| ID | 日期 | 模块 | 现象 | 状态 |",
             "|----|------|------|------|------|"]
    for e in entries:
        block = e["_lines"]
        status = _get_field(block, "状态") or "待解决"
        table.append(
            f"| {e['_id']} | {_get_field(block, '日期')} | {_get_field(block, '模块')} "
            f"| {_get_field(block, '现象')} | {status} |"
        )
    table += ["", ""]
    return table


def _replace_section(lines: list[str], heading: str, replacement: list[str]) -> list[str]:
    out, i, started = [], 0, False
    while i < len(lines):
        line = lines[i]
        if line.startswith(heading) and not started:
            started = True
            out.extend(replacement)
            i += 1
            while i < len(lines) and not lines[i].startswith("## "):
                i += 1
            continue
        out.append(line)
        i += 1
    if not started:
        out.extend([heading, ""] + replacement)
    return out


def record(args: argparse.Namespace) -> tuple[str, str]:
    _validate(args)
    root = args.dev_log.resolve()
    if not root.is_dir():
        raise ValueError(f"dev-log 目录不存在: {root}")
    path = (root / args.log).resolve()
    if root not in path.parents:
        raise ValueError("错误日志路径越界")

    if path.exists():
        lines = path.read_text(encoding="utf-8").split("\n")
    else:
        lines = ["# NoteAgent 错误记录", "", "## 状态速览", "", "## 记录（最近在前）", ""]

    span = _find_block(lines, args.error_id)
    if args.status == "open" and span is None:
        block = _entry_text(args)
        rec_idx = next(i for i, l in enumerate(lines) if l.startswith("## 记录"))
        ins = rec_idx + 1
        while ins < len(lines) and lines[ins].strip() == "":
            ins += 1
        lines = lines[:ins] + block + lines[ins:]
        lines = _replace_section(lines, "## 状态速览", _render_summary(lines))
        _atomic_write(path, "\n".join(lines).rstrip("\n") + "\n")
        return "created", path.name

    if span is None:
        raise ValueError(f"未找到错误条目 {args.error_id}，且 status 非 open")

    start, end = span
    block = lines[start:end]
    was_fixed = _get_field(block, "状态").startswith("已解决")

    if args.status == "fixed" and not was_fixed:
        _set_field(block, "状态", "已解决 ✅", "")
        for key, val in (("根因", args.cause), ("解决", args.fix),
                         ("验证", args.verification), ("提交", args.commit)):
            if val:
                _set_field(block, key, val, "")
        lines[start:end] = block
        lines = _replace_section(lines, "## 状态速览", _render_summary(lines))
        _atomic_write(path, "\n".join(lines).rstrip("\n") + "\n")
        return "resolved", path.name

    if args.status == "fixed" and was_fixed:
        changed = False
        for key, val in (("根因", args.cause), ("解决", args.fix),
                         ("验证", args.verification), ("提交", args.commit)):
            if val and val != _get_field(block, key):
                _set_field(block, key, val, "")
                changed = True
        if changed:
            lines[start:end] = block
            _atomic_write(path, "\n".join(lines).rstrip("\n") + "\n")
            return "updated", path.name
        return "unchanged", path.name

    # status == open on existing block: refuse to downgrade
    raise ValueError(f"条目 {args.error_id} 已存在，不允许重复新增；用 --status fixed 记录解决")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        action, name = record(args)
    except (OSError, ValueError) as exc:
        print(f"record-error: {exc}", file=sys.stderr)
        return 2
    print(f"{action}: {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
