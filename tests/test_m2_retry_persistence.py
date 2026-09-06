import json
from src.services.pipeline import Pipeline
from src.infra.llm_gateway import LLMGateway
from src.core.extraction import build_extraction_prompt

def test_retry_failed_updates_artifact(tmp_path):
    vault=tmp_path/'v'; vault.mkdir(); (vault/'a.md').write_text('# A\ntext')
    rec=tmp_path/'r'; rec.mkdir()
    note_id='placeholder'
    # Build a pipeline and seed the failure artifact using the collected stable id.
    p=Pipeline(vault,tmp_path/'db.sqlite',tmp_path/'runs',rec)
    note=p.collector.collect()[0]
    prompt=build_extraction_prompt(note['relative_path'],note['content'])
    LLMGateway(rec,mode='record',transport=lambda _: json.dumps({'title':'A','summary':'text','keywords':[],'candidate_tags':[],'events':[]})).chat(prompt)
    p.io.write('run-1','extract',{'results':[],'failures':[{'note_id':note['note_id'],'error':'old'}]})
    result=p.retry_failed('run-1')
    assert len(result['retried'])==1 and result['failures']==[]
    assert p.io.read('run-1','extract')['failures']==[]
