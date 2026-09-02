"""M3 修正验收：关联幂等、事件向量入库、判定失败隔离、Chroma 跨进程重开兼容。"""
import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.data.vector_store import ChromaVectorStore, local_hash_embedding
from src.models.orm import Association, LLMCall
from src.services.pipeline import Pipeline


class _Transport:
    """可复用 record 传输：抽取 prompt 与关联判定 prompt 区分，判定一律 related。"""

    def __call__(self, prompt: str) -> str:
        if "请判断以下候选" in prompt:
            cand = json.loads(prompt.split("\n")[-1])
            return json.dumps({"source_id": cand["source_id"], "target_id": cand["target_id"], "related": True, "confidence": 0.9, "evidence": cand["evidence"], "rationale": "同文件夹"})
        return json.dumps({"title": "项目", "summary": "推进项目", "keywords": ["项目"], "candidate_tags": [], "events": [{"content": "推进项目", "order_in_note": 0}]})


class _FailFirstCandidate:
    """让第一个候选的判定持续失败（重试后仍失败），用于验证失败隔离。"""

    def __init__(self, inner):
        self.seen = None
        self.inner = inner

    def __call__(self, prompt: str) -> str:
        if "请判断以下候选" in prompt:
            if self.seen is None:
                self.seen = prompt
            if prompt == self.seen:
                raise RuntimeError("first candidate persistently fails")
        return self.inner(prompt)


def _make_vault(tmp_path, names=("a", "b")):
    vault = tmp_path / "vault"
    (vault / "work").mkdir(parents=True)
    for name in names:
        (vault / "work" / f"{name}.md").write_text(f"# {name}\n{name}", encoding="utf-8")
    return vault


# ── Chroma 跨进程重开兼容 ──

def test_vector_store_reopen_uses_injected_embedding(tmp_path):
    """重开既有 collection（新 client）仍使用注入的嵌入函数，不退化到默认 MiniLM（维度不匹配）。"""
    store = ChromaVectorStore(tmp_path / "chroma", model_name="det-v1", embedding_function=local_hash_embedding)
    store.add_notes([{"id": "n1", "summary": "alpha", "folder": "work"}, {"id": "n2", "summary": "beta", "folder": "work"}])
    reopened = ChromaVectorStore(tmp_path / "chroma", model_name="det-v1", embedding_function=local_hash_embedding)
    assert reopened.search("alpha", 1)[0]["id"] == "n1"
    reopened.add_notes([{"id": "n3", "summary": "gamma", "folder": "work"}])
    assert reopened.search("gamma", 1)[0]["id"] == "n3"


# ── 关联幂等：重跑不产生重复关联 ──

def test_association_idempotent_across_runs(tmp_path):
    vault = _make_vault(tmp_path)
    pipeline = Pipeline(vault, tmp_path / "db.sqlite", tmp_path / "runs", tmp_path / "rec", mode="record", transport=_Transport())
    r1 = pipeline.run()
    # 变更 b 触发其重提炼，note_id 稳定不变
    (vault / "work" / "b.md").write_text("# B\nB B changed", encoding="utf-8")
    r2 = pipeline.run()

    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with Session(engine) as session:
        assoc = session.scalars(select(Association)).all()
        assert len(assoc) == 1  # 两次 run 均产出同一对关联，只保留最新一行
        assert assoc[0].run_id == r2
        assert assoc[0].confidence == 0.9


# ── 事件向量入库 ──

def test_pipeline_populates_events_vectors(tmp_path):
    vault = _make_vault(tmp_path)
    pipeline = Pipeline(vault, tmp_path / "db.sqlite", tmp_path / "runs", tmp_path / "rec", mode="record", transport=_Transport())
    pipeline.run()

    store = ChromaVectorStore(tmp_path / "chroma", model_name="local-hash-v1", embedding_function=local_hash_embedding)
    events = store.get_all("events")
    ids = {e["id"] for e in events}
    assert len(events) >= 1
    assert all("推进项目" in e["document"] for e in events)
    assert store.search_events("推进项目", 1)[0]["id"] in ids


# ── 判定失败隔离：单个候选失败不中断阶段 ──

def test_associate_failure_isolation(tmp_path):
    vault = _make_vault(tmp_path, names=("a", "b", "c"))
    pipeline = Pipeline(vault, tmp_path / "db.sqlite", tmp_path / "runs", tmp_path / "rec", mode="record", transport=_FailFirstCandidate(_Transport()))
    run_id = pipeline.run()

    run = pipeline.rm.get_run(run_id)
    assert run.status == "done"  # 部分失败不影响 Run 收尾
    artifact = pipeline.io.read(run_id, "associate")
    assert len(artifact["candidates"]) == 3
    assert len(artifact["judgements"]) == 2
    assert len(artifact["failures"]) == 1

    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with Session(engine) as session:
        assert len(session.scalars(select(Association)).all()) == 2
        calls = session.scalars(select(LLMCall).where(LLMCall.stage == "associate")).all()
        assert {c.status for c in calls} == {"ok", "failed"}  # 失败判定同样入台账
