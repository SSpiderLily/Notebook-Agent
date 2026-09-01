import json
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from src.services.pipeline import Pipeline
from src.data.collection import Collector
from src.infra.llm_gateway import LLMGateway
from src.models.orm import Extraction, Event
from sqlalchemy import select


def test_minimal_pipeline_is_observable_and_executable(tmp_path):
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "a.md").write_text("# A\n推进项目", encoding="utf-8")
    note = Collector(vault).collect()[0]
    prompt = f"请提炼以下笔记为 JSON（title, summary, keywords, candidate_tags, events）；笔记路径：{note['relative_path']}\n{note['content']}"
    recordings = tmp_path / "recordings"
    LLMGateway(recordings, mode="record", transport=lambda _: json.dumps({"title":"A","summary":"推进项目","keywords":[],"candidate_tags":[],"events":[{"content":"推进项目","order_in_note":0}]})).chat(prompt)
    pipeline = Pipeline(vault, tmp_path / "db.sqlite", tmp_path / "runs", recordings)
    run_id = pipeline.run()
    run = pipeline.rm.get_run(run_id)
    assert run.status == "done"
    stages = {s.stage:s for s in pipeline.rm.get_stages(run_id)}
    assert stages['collect'].status == 'done'
    assert stages['extract'].status == 'done'
    assert pipeline.io.read(run_id, 'extract')['results'][0]['draft']['events'][0]['content'] == '推进项目'
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    from src.models.orm import Extraction, Event
    with Session(engine) as session:
        assert len(session.scalars(select(Extraction)).all()) == 1
        assert len(session.scalars(select(Event)).all()) == 1
    assert len(pipeline.gateway.calls) == 1
    second = Pipeline(vault, tmp_path / 'db2.sqlite', tmp_path / 'runs2', recordings)
    second.run()
    assert len(second.gateway.calls) == 0
