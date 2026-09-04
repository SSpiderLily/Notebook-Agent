from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

import yaml

from .models import Note


def _json_safe(value: Any) -> Any:
    """把 YAML 解析出的非 JSON 安全标量归一化，保证采集快照可序列化。

    yaml.safe_load 会把 `created: 2026-03-02` 解析成 date 对象，直接写入快照会
    导致 json.dumps 失败。这里递归转换：date/datetime → ISO 字符串，其余不可
    序列化对象 → 字符串。
    """
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_json_safe(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class NoteParser:
    """解析 Vault Markdown 的 frontmatter、标题、标签和双链。"""
    _fm = re.compile(r"\A---\s*\n(.*?)(?:\n---\s*(?:\n|\Z))", re.DOTALL)
    _heading = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
    _tag = re.compile(r"(?<![\w/])#([^\s#`]+)")
    _wikilink = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")

    def _without_code(self, content: str) -> str:
        return re.sub(r"```[\s\S]*?```|~~~[\s\S]*?~~~", "", content)

    def parse_metadata(self, content: str) -> dict[str, Any]:
        match = self._fm.match(content)
        if not match:
            return {}
        try:
            value = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"frontmatter YAML 无效: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError("frontmatter 必须是 YAML 对象")
        return _json_safe(value)

    def parse_title(self, content: str) -> str:
        match = self._heading.search(self._without_code(self._fm.sub("", content, count=1)))
        return match.group(1).strip() if match else "无标题"

    def extract_keywords(self, content: str) -> list[str]:
        clean = self._without_code(self._fm.sub("", content, count=1))
        clean = re.sub(r"!?(?:\[[^\]]*\]\([^)]*\)|\[[^\]]*\]\([^)]*\))", "", clean)
        result = []
        for tag in self._tag.findall(clean):
            if tag not in result:
                result.append(tag)
        return result

    def extract_links(self, content: str) -> list[dict[str, str | None]]:
        clean = self._without_code(content)
        return [{"target": target.strip(), "alias": alias.strip() if alias else None} for target, alias in self._wikilink.findall(clean)]

    def extract_content_without_metadata(self, content: str) -> str:
        return self._fm.sub("", content, count=1).strip()

    def parse(self, content: str, filepath: str | None = None) -> dict[str, Any]:
        metadata = self.parse_metadata(content)
        return {"title": self.parse_title(content), "content": self.extract_content_without_metadata(content),
                "keywords": self.extract_keywords(content), "links": self.extract_links(content), "metadata": metadata}

    def parse_to_note(self, content: str, note_id: int | str = None, filepath: str | None = None) -> Note:
        parsed = self.parse(content, filepath)
        path = __import__("pathlib").Path(filepath) if filepath else None
        return Note(id=note_id or __import__("hashlib").sha256(content.encode()).hexdigest(), title=parsed["title"], content=parsed["content"], keywords=parsed["keywords"], metadata={**parsed["metadata"], "links": parsed["links"]}, filepath=filepath, filename=path.name if path else None, created_at=datetime.fromtimestamp(path.stat().st_ctime) if path else None, updated_at=datetime.fromtimestamp(path.stat().st_mtime) if path else None)


class MarkdownParser(NoteParser):
    def extract_code_blocks(self, content: str) -> list[dict[str, str]]:
        return [{"language": m.group(1), "code": m.group(2)} for m in re.finditer(r"```(\w*)\n([\s\S]*?)```", content)]

    def extract_images(self, content: str) -> list[dict[str, str]]:
        return [{"alt": m.group(1), "url": m.group(2)} for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", content)]

    def clean_content(self, content: str) -> str:
        return re.sub(r"```[\s\S]*?```", "", content)
