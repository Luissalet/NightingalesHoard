"""Synthetic integration of native workbench and calculation lifetimes."""
import json
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from nightingale.config import Config
from fusion.app import create_app


def test_catalog_is_static_and_every_engine_starts_off(tmp_path):
    script = """
import sys, tempfile
from pathlib import Path
from fastapi.testclient import TestClient
from nightingale.config import Config
from fusion.app import create_app
with TestClient(create_app(Config(data_dir=Path(tempfile.mkdtemp()))),base_url='http://127.0.0.1:5189') as client:
    assert all(not m['enabled'] for m in client.get('/api/modules').json()['modules'])
    assert len(client.get('/api/agent/tools').json()['tools']) >= 33
    assert 'nightingale.services' not in sys.modules
    assert 'laplaces_hoard.api' not in sys.modules
    assert 'duckdb' not in sys.modules
    assert client.put('/api/modules/calculation',json={'enabled':True}).status_code == 200
    assert 'duckdb' not in sys.modules
    result = client.post('/calculation/api/ui/calc',json={'expression':'2+2'})
    assert result.status_code == 200, result.text
    assert result.json()['exact'] == '4'
    assert 'duckdb' not in sys.modules
    assert 'nightingale.services' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", script], check=True, cwd=Path(__file__).parents[1])


def test_independent_calculation_data_and_collisions(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:5189") as client:
        headers = {"Authorization": "Bearer " + app.state.token}
        def call(name, arguments={}):
            return client.post('/api/agent/call', headers=headers, json={"name": name, "arguments": arguments})
        assert call('calc', {"expression": "0.1+0.2"}).status_code == 409
        assert client.put('/api/modules/calculation', json={"enabled": True}).status_code == 200
        answer = call('calc', {"expression": "0.1+0.2"})
        assert answer.status_code == 200, answer.text
        assert answer.json()['exact'] == '3/10'
        assert app.state.modules['calculation'].app.state.lh._catalog is None
        assert not app.state.modules['data'].status()['enabled']
        assert client.get('/calculation/').status_code == 200
        assert client.put('/api/modules/data', json={"enabled": True}).status_code == 200
        result = call('data_ingest', {"kind": "paste", "text": "x,y\n1,2\n3,4\n", "name": "synthetic"})
        assert result.status_code == 200, result.text
        listing = call('data_list').json()
        assert 'synthetic' in str(listing)
        assert call('laplace_data_list').status_code == 200
        forwarded = call('laplace_data_list', {"target": "nightingale"})
        assert forwarded.status_code == 200, forwarded.text
        assert 'synthetic' in str(forwarded.json())
        assert client.post('/api/modules/stop-all').status_code == 200
        assert all(not m.app for m in app.state.modules.values())
        assert json.loads((tmp_path/'modules.json').read_text())['enabled'] == []


def test_ui_assets_and_paths_keep_module_prefix(tmp_path):
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:5189") as client:
        assert client.get('/').status_code == 200
        assert client.put('/api/modules/calculation', json={"enabled": True}).status_code == 200
        html = client.get('/calculation/').text
        assert '/calculation/assets/' in html
        assert '/calculation/calculation/' not in html
        import re
        script = re.search(r'src="([^"]+\.js)"', html).group(1)
        asset = client.get(script)
        assert asset.status_code == 200
        assert asset.content == (Path(__file__).parents[1] / 'fusion' / 'calculation-static' / 'assets' / script.rsplit('/',1)[1]).read_bytes()


def test_data_shutdown_drains_workers_without_blocking_health(tmp_path):
    import concurrent.futures
    import threading
    import time
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app, base_url="http://127.0.0.1:5189") as client:
        assert client.put('/api/modules/data', json={'enabled': True}).status_code == 200
        release = threading.Event()
        started = threading.Event()
        def work():
            started.set()
            release.wait(10)
        service = app.state.modules['data'].app.state.services
        job = service.executor.submit(work)
        assert started.wait(2)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            stopping = pool.submit(client.put, '/api/modules/data', json={'enabled': False})
            try:
                deadline = time.monotonic() + 2
                while not app.state.modules['data'].stopping and time.monotonic() < deadline:
                    time.sleep(.01)
                assert not stopping.done()
                assert json.loads((tmp_path/'modules.json').read_text())['enabled'] == []
                before = time.monotonic()
                assert client.get('/api/health').status_code == 200
                assert time.monotonic() - before < 1
            finally:
                release.set()
            assert stopping.result(timeout=3).status_code == 200
        assert job.done()
        assert app.state.modules['data'].app is None


def test_stdio_discovery_works_with_no_server_or_data(tmp_path):
    import asyncio
    import os
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    root = Path(__file__).parents[1]
    async def discover():
        params = StdioServerParameters(command=sys.executable, args=[str(root/'mcp_server.py')],
            env={**os.environ, 'NIGHTINGALE_URL': 'http://127.0.0.1:1', 'NIGHTINGALE_DATA_DIR': str(tmp_path/'no-data')})
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert len(tools.tools) == 51
                assert {'calc', 'data_ingest', 'laplace_data_query', 'data_join_preview'} <= {tool.name for tool in tools.tools}
    asyncio.run(discover())
    assert not (tmp_path/'no-data').exists()


def mock_copilot_transport(monkeypatch):
    """Exercise the native relay and origin rewriting without any model/network."""
    import httpx
    original = httpx.AsyncClient
    calls = []
    async def upstream(request):
        calls.append(request)
        if request.method == 'GET' and request.url.path == '/api/hoard-copilot/capabilities':
            return httpx.Response(200, json={'contract_version': '1.0'})
        assert request.method == 'POST' and request.url.path == '/api/session'
        return httpx.Response(200, json=json.loads(request.content))
    def local_client(*args, **kwargs):
        kwargs.setdefault('transport', httpx.MockTransport(upstream))
        return original(*args, **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', local_client)
    return calls


def test_copilot_preserves_each_native_identity_and_history_space(tmp_path, monkeypatch):
    mock_copilot_transport(monkeypatch)
    (tmp_path/'copilot-space-id').write_text('shell-space', encoding='utf-8')
    paths = {key: tmp_path/('native-' + key) for key in ('data', 'calculation')}
    for key, path in paths.items():
        path.mkdir()
        (path/'copilot-space-id').write_text('history-' + key, encoding='utf-8')
    (tmp_path/'modules.json').write_text(json.dumps({'enabled': [], 'data_paths': {key: str(path) for key,path in paths.items()}}), encoding='utf-8')
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app, base_url='http://127.0.0.1:5189') as client:
        for key, native_id in (('data', 'nightingale'), ('calculation', 'laplace')):
            assert client.put('/api/modules/' + key, json={'enabled': True}).status_code == 200
            page = 'http://127.0.0.1:5189/' + key + '/'
            scoped = client.get('/api/copilot/info', headers={'Referer': page})
            direct = client.get('/' + key + '/api/copilot/info', follow_redirects=False)
            for response in (scoped, direct):
                assert response.status_code == 200, response.text
                info = response.json()
                assert info['app_id'] == info['tool_server_id'] == native_id
                assert info['data_space_id'] == 'history-' + key
            for route, headers in (('/api/copilot/proxy', {'Referer': page}), ('/' + key + '/api/copilot/proxy', {})):
                response = client.post(route, headers=headers, json={'path': '/api/session', 'method': 'POST',
                    'body': {'context': {'origin': {'app_id': 'stale', 'extra': 'keep'}}, 'hoard_context': {'origin': {'data_space_id': 'stale'}}}}, follow_redirects=False)
                assert response.status_code == 200, response.text
                body = response.json()
                assert body['context']['origin'] == {'app_id': native_id, 'data_space_id': 'history-' + key, 'extra': 'keep'}
                assert body['hoard_context']['origin'] == {'app_id': native_id, 'data_space_id': 'history-' + key}
        for referer in (None, 'http://example.org/calculation/', 'http://127.0.0.1:5190/calculation/', 'https://127.0.0.1:5189/calculation/', 'http://127.0.0.1:5189/calculation-other/', 'http://[invalid'):
            info = client.get('/api/copilot/info', headers={'Referer': referer} if referer else {}).json()
            assert info['app_id'] == info['tool_server_id'] == 'nightingale'
            assert info['data_space_id'] == 'shell-space'
        central = client.post('/api/copilot/proxy', json={'path': '/api/session', 'method': 'POST', 'body': {'context': {}}})
        assert central.status_code == 200
        assert central.json()['context']['origin'] == {'app_id': 'nightingale', 'data_space_id': 'shell-space'}


def test_copilot_off_is_inert_for_canonical_and_prefixed_routes(tmp_path, monkeypatch):
    calls = mock_copilot_transport(monkeypatch)
    app = create_app(Config(data_dir=tmp_path))
    with TestClient(app, base_url='http://127.0.0.1:5189') as client:
        for key in ('data', 'calculation'):
            before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*'))
            page = 'http://127.0.0.1:5189/' + key + '/'
            for route, headers in (('/api/copilot/', {'Referer': page}), ('/' + key + '/api/copilot/', {})):
                assert client.get(route + 'info', headers=headers, follow_redirects=False).status_code == 409
                assert client.post(route + 'proxy', headers=headers, json={'path': '/api/session'}, follow_redirects=False).status_code == 409
            assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')) == before
        assert not calls
        assert all(module.app is None for module in app.state.modules.values())
