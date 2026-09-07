"""M3 关联调用量优化：候选门槛、语义阈值与跨 Run 缓存。"""
import json

from src.core.association import generate_candidates
from src.models.orm import Association, Base
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from src.services.pipeline import Pipeline


class _VectorStore:
    def __init__(self, hits):
        self.hits = hits

    def search(self, _query, k=5):
        return self.hits[:k]


def test_same_folder_alone_is_not_candidate():
    notes = [
        {"id": "a", "folder": "日报", "filename": "a.md", "keywords": ["alpha"]},
        {"id": "b", "folder": "日报", "filename": "b.md", "keywords": ["beta"]},
    ]
    assert generate_candidates(notes) == []


def test_same_folder_and_common_keyword_is_candidate():
    notes = [
        {"id": "a", "folder": "项目", "filename": "a.md", "keywords": ["noteagent", "alpha"]},
        {"id": "b", "folder": "项目", "filename": "b.md", "keywords": ["NoteAgent", "beta"]},
    ]
    candidates = generate_candidates(notes)
    assert len(candidates) == 1
    assert "folder" in candidates[0].basis
    assert "keyword" in candidates[0].basis


def test_semantic_distance_threshold_filters_weak_hit():
    notes = [
        {"id": "a", "folder": "x", "filename": "a.md", "keywords": []},
        {"id": "b", "folder": "y", "filename": "b.md", "keywords": []},
    ]
    store = _VectorStore([{"id": "b", "distance": 0.8}])
    assert generate_candidates(notes, store, min_similarity=0.5) == []
    assert len(generate_candidates(notes, store, min_similarity=0.9)) == 1


class _Transport:
    def __init__(self):
        self.association_calls = 0

    def __call__(self, prompt: str) -> str:
        if "是否存在实质关联" in prompt:
            self.association_calls += 1
            candidate = json.loads(prompt.split("\n")[-1])
            return json.dumps({
                "source_id": candidate["source_id"],
                "target_id": candidate["target_id"],
                "related": False,
                "confidence": 0.2,
                "evidence": candidate["evidence"],
                "rationale": "测试负判定",
            })
        return json.dumps({
            "title": "项目",
            "summary": "推进项目",
            "keywords": ["项目"],
            "candidate_tags": [],
            "events": [{"content": "推进项目", "order_in_note": 0}],
        })


def test_pipeline_reuses_association_judgement_across_runs(tmp_path):
    vault = tmp_path / "vault"
    (vault / "work").mkdir(parents=True)
    (vault / "work" / "alpha.md").write_text("# A\n推进项目", encoding="utf-8")
    (vault / "work" / "beta.md").write_text("# B\n推进项目", encoding="utf-8")
    transport = _Transport()
    pipeline = Pipeline(vault, tmp_path / "db.sqlite", tmp_path / "runs", tmp_path / "recordings", mode="record", transport=transport)

    pipeline.run()
    first_calls = transport.association_calls
    assert first_calls == 1

    pipeline.run()
    assert transport.association_calls == first_calls

    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with Session(engine) as session:
        association = session.scalars(select(Association)).one()
        assert association.related is False
        assert association.input_digest
        assert association.model == "test"
