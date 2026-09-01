import json
from src.infra.llm_gateway import LLMGateway

def test_record_transport_retries_and_records_metadata(tmp_path):
    attempts = []
    def transport(prompt):
        attempts.append(prompt)
        if len(attempts) < 3:
            raise RuntimeError('temporary')
        return json.dumps({'ok': True})
    gw = LLMGateway(tmp_path, mode='record', transport=transport)
    assert gw.chat('p') == '{"ok": true}'
    assert len(attempts) == 3
    assert gw.calls[0]['retries'] == 2
    assert 'latency_ms' in gw.calls[0]
