import pytest
from src.infra.llm_gateway import LLMGateway, LLMCostCapExceeded

def test_cost_cap_blocks_call(tmp_path):
    gw = LLMGateway(tmp_path, mode='record', cost_cap=0.0, transport=lambda _: 'x')
    with pytest.raises(LLMCostCapExceeded):
        gw.chat('prompt')

def test_replay_call_has_usage_metadata(tmp_path):
    record = LLMGateway(tmp_path, mode='record', transport=lambda _: 'response')
    record.chat('prompt')
    replay = LLMGateway(tmp_path, mode='replay')
    assert replay.chat('prompt') == 'response'
    assert replay.calls[0]['prompt_tokens'] > 0
    assert replay.calls[0]['cost_est'] > 0
