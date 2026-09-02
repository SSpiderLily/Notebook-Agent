from src.core.association import AssociationCandidate, AssociationJudgement, generate_candidates
from src.data.vector_store import ChromaVectorStore


class DeterministicEmbedding:
    def __call__(self, input):
        return [[float(len(text)), 1.0] for text in input]

    def name(self):
        return "deterministic"


def test_vector_store_collections_and_roundtrip(tmp_path):
    store = ChromaVectorStore(tmp_path / "chroma", model_name="det-v1", embedding_function=DeterministicEmbedding())
    store.add_notes([{"id": "n1", "title": "Task", "summary": "alpha", "folder": "work"}])
    store.add_events([{"id": "e1", "content": "did alpha", "note_id": "n1"}])
    assert store.get("n1")["metadata"]["folder"] == "work"
    assert store.search("alpha", 1)[0]["id"] == "n1"
    assert store.search_events("alpha", 1)[0]["id"] == "e1"
    assert store.rebuild_estimate()["items"] == 1


def test_candidates_use_folder_naming_and_time():
    notes = [
        {"id": "a", "filename": "project-plan.md", "folder": "work", "updated_at": "2026-01-01T10:00:00"},
        {"id": "b", "filename": "project-plan-next.md", "folder": "work", "updated_at": "2026-01-01T11:00:00"},
    ]
    candidates = generate_candidates(notes)
    assert len(candidates) == 1
    assert {"folder", "naming", "temporal"} <= set(candidates[0].basis)


def test_judgement_schema_bounds():
    value = AssociationJudgement(source_id="a", target_id="b", related=True, confidence=.8)
    assert value.confidence == .8
    assert AssociationCandidate(source_id="a", target_id="b").basis == []
