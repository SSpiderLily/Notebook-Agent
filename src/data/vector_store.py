from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Iterable

import chromadb
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from chromadb.utils.embedding_functions import register_embedding_function


def local_hash_embedding(texts: Iterable[str], dim: int = 64) -> list[list[float]]:
    """无网络的确定性本地嵌入：按字符序哈希词袋，用于测试或无需真实语义的场合。

    dim 固定，保证同一次进程内向量可比。生产环境可替换为真实 embedding 模型。
    """
    out: list[list[float]] = []
    for text in texts:
        vector = [0.0] * dim
        for ch in str(text):
            vector[ord(ch) % dim] += 1.0
        out.append(vector)
    return out


class BaseVectorStore(ABC):
    @abstractmethod
    def add_notes(self, notes: list): ...

    @abstractmethod
    def search(self, query: str, k: int = 5) -> list: ...

    @abstractmethod
    def get_all(self) -> list: ...

    def add_events(self, events: list):
        raise NotImplementedError

    def search_events(self, query: str, k: int = 5) -> list:
        raise NotImplementedError

    def get(self, item_id: str, collection: str = "notes") -> dict | None:
        raise NotImplementedError

    def rebuild_estimate(self, collection: str = "notes") -> dict:
        raise NotImplementedError


_EF_NAME = "noteagent_injected"


def _resolve_importable(fn: Any) -> str:
    """尝试把可导入的模块级函数解析为 'module:qualname'，否则返回空串。"""
    mod = getattr(fn, "__module__", None)
    qual = getattr(fn, "__qualname__", None)
    if mod and qual:
        try:
            importlib.import_module(mod)
            return f"{mod}:{qual}"
        except Exception:
            return ""
    return ""


class InjectedEmbeddingFunction(EmbeddingFunction[Documents]):
    """把注入的确定性/自定义嵌入函数包装为 Chroma 可识别、可序列化重建的 EmbeddingFunction。

    背景：Chroma 1.x 下，注入一个普通 callable 作为 embedding_function 会被当作 legacy 配置，
    跨进程重开集合时退化为默认 MiniLM（维度不匹配，报 dimension 错误）。这里子类化官方
    EmbeddingFunction 并注册，使创建/查询都真正使用注入函数，重新打开集合时也能通过
    build_from_config 重建（注入函数需为可 import 的模块级函数，如 local_hash_embedding）。
    """

    def __init__(self, function: Callable[[Iterable[str]], list[list[float]]], model: str = "injected"):
        self._function = function
        self._model = model

    def __call__(self, input: Documents) -> Embeddings:
        return self._function(list(input))

    def embed_query(self, input) -> Embeddings:
        # Chroma 以 embed_query(input=[query_text]) 调用，期望返回一个向量列表
        return self._function(list(input))

    @staticmethod
    def name() -> str:
        return _EF_NAME

    def get_config(self) -> dict:
        return {"function": _resolve_importable(self._function), "model": self._model}

    @staticmethod
    def build_from_config(config: dict) -> "InjectedEmbeddingFunction":
        fnref = config.get("function", "")
        if not fnref:
            raise ValueError(f"注入嵌入函数无法从配置重建: {config}")
        mod, qual = fnref.split(":", 1)
        obj = importlib.import_module(mod)
        for part in qual.split("."):
            obj = getattr(obj, part)
        return InjectedEmbeddingFunction(obj, config.get("model", "injected"))

    def is_legacy(self) -> bool:
        # 完整实现 name/get_config/build_from_config，避免 Chroma 走 legacy 序列化路径
        return False


register_embedding_function(InjectedEmbeddingFunction)


class ChromaVectorStore(BaseVectorStore):
    """本地 Chroma 存储；embedding_function 可注入以保证测试确定性。"""
    def __init__(self, path: str | Path, model_name: str = "default", embedding_function: Any = None):
        self.path = str(path)
        self.model_name = model_name
        self.embedding_function = InjectedEmbeddingFunction(embedding_function, model_name) if embedding_function is not None else None
        self.client = chromadb.PersistentClient(path=self.path)
        self.notes = self._collection("notes")
        self.events = self._collection("events")

    def _collection(self, name: str):
        existing = self.client.get_collection(name) if name in [c.name for c in self.client.list_collections()] else None
        if existing is not None:
            stored = (existing.metadata or {}).get("embedding_model")
            if stored and stored != self.model_name:
                raise ValueError(f"向量模型不匹配: collection={stored}, current={self.model_name}；请重建向量库")
            return existing
        return self.client.create_collection(name, metadata={"embedding_model": self.model_name}, embedding_function=self.embedding_function)

    @staticmethod
    def _text(item: Any, event: bool = False) -> str:
        if isinstance(item, dict):
            keys = ("content", "summary", "title", "keywords", "folder") if event else ("summary", "content", "title", "keywords", "folder")
            return " ".join(str(item.get(k, "")) for k in keys)
        return str(getattr(item, "content", item))

    @staticmethod
    def _id(item: Any) -> str:
        value = item.get("id") if isinstance(item, dict) else getattr(item, "id", None)
        return str(value)

    @staticmethod
    def _metadata(item: Any) -> dict:
        value = item if isinstance(item, dict) else getattr(item, "metadata", {})
        meta = dict(value.get("metadata", {}) if isinstance(value, dict) else {})
        if isinstance(item, dict):
            for key in ("note_id", "folder", "filepath", "filename", "time_clue", "note_id"):
                if key in item: meta[key] = item[key]
        return {k: (v if isinstance(v, (str, int, float, bool)) else str(v)) for k, v in meta.items()}

    def _add(self, collection, items: Iterable):
        items = list(items)
        if not items: return
        collection.upsert(ids=[self._id(x) for x in items], documents=[self._text(x, collection is self.events) for x in items], metadatas=[self._metadata(x) for x in items])

    def add_notes(self, notes: list): self._add(self.notes, notes)
    def add_events(self, events: list): self._add(self.events, events)

    def delete_events_by_note(self, note_ids: Iterable[str]) -> None:
        """删除指定笔记（note_id 元数据）下的全部事件向量。

        用于事件向量旧数据清理：笔记重提炼会生成新的 extraction_id，按稳定
        note_id 维度整批删除可避免旧向量残留。Chroma 的 where 删除对不存在的
        note_id 幂等（无匹配即无操作）。
        """
        for nid in note_ids:
            try:
                self.events.delete(where={"note_id": str(nid)})
            except Exception:
                # 无匹配或集合级异常均视为幂等 no-op，不阻断后续清理
                continue

    def _search(self, collection, query, k):
        result = collection.query(query_texts=[query], n_results=k)
        ids = result.get("ids", [[]])[0]
        docs = result.get("documents", [[]])[0]
        metas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [{"id": i, "document": d, "metadata": m or {}, "distance": distances[n] if n < len(distances) else None} for n, (i, d, m) in enumerate(zip(ids, docs, metas))]

    def search(self, query: str, k: int = 5) -> list: return self._search(self.notes, query, k)
    def search_events(self, query: str, k: int = 5) -> list: return self._search(self.events, query, k)
    def get_all(self, collection: str = "notes") -> list:
        c = self.notes if collection == "notes" else self.events
        r = c.get(include=["documents", "metadatas"])
        return [{"id": i, "document": d, "metadata": m or {}} for i, d, m in zip(r["ids"], r.get("documents", []), r.get("metadatas", []))]
    def get(self, item_id: str, collection: str = "notes") -> dict | None:
        c = self.notes if collection == "notes" else self.events
        r = c.get(ids=[str(item_id)], include=["documents", "metadatas"])
        return None if not r["ids"] else {"id": r["ids"][0], "document": r["documents"][0], "metadata": r["metadatas"][0] or {}}
    def rebuild_estimate(self, collection: str = "notes") -> dict:
        items = self.get_all(collection)
        chars = sum(len(x.get("document", "")) for x in items)
        return {"collection": collection, "items": len(items), "characters": chars, "estimated_tokens": max(0, chars // 4)}
