
pytest_plugins = ['tests.test_api']


def test_m9_observe_endpoints(env, client):
    assert client.get('/api/observe/runs').status_code == 200
    assert client.get('/api/observe/llm-calls').status_code == 200
    status = client.get('/api/observe/vault-status').json()
    assert status['total'] >= 0 and set(status['counts']) == {'active','missing','ignored'}
    assert client.get('/api/observe/failures').status_code == 200


def test_m9_reset_requires_confirmation(env, client):
    assert client.post('/api/reset', json={}).status_code == 400
    assert client.post('/api/reset', json={'confirm': True, 'scope': 'artifacts'}).status_code == 200
    assert (env['tm'].vault_dir / 'a.md').exists()
