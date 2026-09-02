"""M3 关联：ORM 持久化（Note/Association/Extraction 新字段）与 Pipeline associate 阶段集成。"""
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.data.vector_store import ChromaVectorStore, local_hash_embedding
from src.models.orm import Base, Association, Extraction, LLMCall, Note
from src.services.pipeline import Pipeline


# ── ORM 持久化 ──

def test_note_association_extraction_orm_roundtrip(tmp_path):
    db = create_engine(f"sqlite:///{tmp_path / 'orm.sqlite'}")
    Base.metadata.create_all(db)
    with Session(db) as session:
        session.add(Note(id="n1", path="work/a.md", folder="work", filename="a.md", content_hash="abc", vault_status="active", last_run_id="r1"))
        session.add(Note(id="n2", path="work/b.md", folder="work", filename="b.md", content_hash="def", vault_status="active", last_run_id="r1"))
        session.add(Extraction(note_id="n1", run_id="r1", title="A", summary="s", keywords=json.dumps(["k1", "k2"]), candidate_tags=json.dumps(["tag"]), model="m1", raw_json="{}"))
        session.add(Association(src_type="note", src_id="n1", dst_id="n2", basis=json.dumps(["folder", "semantic"]), confidence=0.9, evidence=json.dumps(["同文件夹: work"]), run_id="r1"))
        session.commit()

    with Session(db) as session:
        notes = {n.id: n for n in session.scalars(select(Note))}
        assert notes["n1"].folder == "work" and notes["n1"].vault_status == "active"
        ex = session.scalars(select(Extraction)).one()
        assert json.loads(ex.keywords) == ["k1", "k2"]
        assert json.loads(ex.candidate_tags) == ["tag"]
        assert ex.model == "m1"
        assoc = session.scalars(select(Association)).one()
        assert json.loads(assoc.basis) == ["folder", "semantic"]
        assert json.loads(assoc.evidence) == ["同文件夹: work"]
        assert assoc.confidence == 0.9


def test_vector_model_change_detection(tmp_path):
    """M3 验收：切换 embedding 模型后应被 Chroma 检测拦截。"""
    path = tmp_path / "chroma"
    ChromaVectorStore(path, model_name="model-v1", embedding_function=local_hash_embedding)
    with pytest.raises(ValueError, match="向量模型不匹配"):
        ChromaVectorStore(path, model_name="model-v2", embedding_function=local_hash_embedding)


# ── Pipeline associate 集成 ──

class _Transport:
    """可复用的 record 传输：区分抽取 prompt 与关联判定 prompt。"""

    def __call__(self, prompt: str) -> str:
        if "请判断以下候选" in prompt:
            cand = json.loads(prompt.split("\n")[-1])
            return json.dumps({"source_id": cand["source_id"], "target_id": cand["target_id"], "related": True, "confidence": 0.9, "evidence": cand["evidence"], "rationale": "同文件夹"})
        return json.dumps({"title": "项目", "summary": "推进项目", "keywords": ["项目"], "candidate_tags": [], "events": [{"content": "推进项目", "order_in_note": 0}]})


def test_pipeline_associate_persists_associations(tmp_path):
    vault = tmp_path / "vault"
    (vault / "work").mkdir(parents=True)
    (vault / "work" / "alpha.md").write_text("# A\n推进项目A", encoding="utf-8")
    (vault / "work" / "beta.md").write_text("# B\n推进项目B", encoding="utf-8")
    recordings = tmp_path / "recordings"
    pipeline = Pipeline(vault, tmp_path / "db.sqlite", tmp_path / "runs", recordings, mode="record", transport=_Transport())
    run_id = pipeline.run()

    run = pipeline.rm.get_run(run_id)
    assert run.status == "done"
    stages = {s.stage: s for s in pipeline.rm.get_stages(run_id)}
    assert stages["associate"].status == "done"

    artifact = pipeline.io.read(run_id, "associate")
    assert len(artifact["candidates"]) == 1
    assert len(artifact["judgements"]) == 1
    assert artifact["judgements"][0]["related"] is True

    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with Session(engine) as session:
        assoc = session.scalars(select(Association)).all()
        assert len(assoc) == 1
        a = assoc[0]
        assert a.src_type == "note"
        assert {a.src_id, a.dst_id} == {pipeline.collector.collect()[0]["note_id"], pipeline.collector.collect()[1]["note_id"]}
        assert "folder" in json.loads(a.basis)
        assert a.confidence == 0.9
        assert json.loads(a.evidence)
        # 关联判定调用入台账
        assert len(session.scalars(select(LLMCall).where(LLMCall.stage == "associate")).all()) == 1
        # 抽取关键词/候选标签已持久化
        ex = session.scalars(select(Extraction)).first()
        assert json.loads(ex.keywords) == ["项目"]
        # 采集登记入 notes 表
        assert len(session.scalars(select(Note)).all()) == 2
