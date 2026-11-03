"""A client calculator must work without Nightingale, Excel or site packages."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import openpyxl
from openpyxl.utils.datetime import MAC_EPOCH, WINDOWS_EPOCH, to_excel
from openpyxl.worksheet.datavalidation import DataValidation
import pytest

from nightingale.sheets.compiler import load
from nightingale.sheets.engine import Compiled, export_python
from nightingale.sheets.web_app import export_web_app


def workbook(path, mac=True):
    wb = openpyxl.Workbook()
    wb.epoch = MAC_EPOCH if mac else WINDOWS_EPOCH
    ws = wb.active
    ws.title = 'Encargo'
    for row in [('Unidades', 12), ('Precio unitario (€)', 18.5), ('Material', 'Aluminio'),
                ('Fecha de pedido', dt.datetime(2026, 11, 3)), ('Activo', True), ('Referencia', '=1+2')]:
        ws.append(row)
    ws['B6'].data_type = 's'
    ws['B4'].number_format = 'yyyy-mm-dd'
    ws['C1'] = '=B1*B2'
    ws['C2'] = '=IF(B5,C1,0)'
    ws['C3'] = '=IF(B3="Acero",C2*1.1,C2)'
    ws['C4'] = '=B4+1'
    ws['C4'].number_format = 'yyyy-mm-dd'
    ws['C5'] = '=B6&"!"'
    ws['C6'] = '=TODAY()'
    ws['C7'] = '=B1/0'
    ws['C8'] = '=UNSUPPORTED(B1)'
    validation = DataValidation(type='list', formula1='"Aluminio,Acero"')
    validation.add('B3')
    ws.add_data_validation(validation)
    wb.save(path)
    return path


@pytest.mark.parametrize('mac', [False, True])
def test_exported_calculator_preserves_types_results_dates_errors_and_frozen_values(tmp_path, mac):
    model = load(workbook(tmp_path / 'quote.xlsx', mac), name='Presupuesto """ cliente </script>')
    namespace = {'__name__': 'client_calculator'}
    code = export_web_app(model)
    exec(code, namespace)
    assert '</script>' not in namespace['_APP_HTML'].split('<h1>')[1].split('</h1>')[0]
    assert r'\u003c/script>' in namespace['_APP_HTML']
    kinds = {p['cell']: p['kind'] for p in namespace['_APP_MODEL']['parameters']}
    assert kinds['Encargo!B3'] == 'select' and kinds['Encargo!B5'] == 'checkbox'
    assert kinds['Encargo!B4'] == 'date'
    assert 'SIL OPEN FONT LICENSE' in namespace['_APP_LICENCES']
    assert 'MIT' in namespace['_APP_LICENCES']
    moment = dt.datetime(2026, 11, 3, 12)
    epoch = MAC_EPOCH if mac else WINDOWS_EPOCH
    for inputs in [{}, {'Encargo!B1': 5, 'Encargo!B3': 'Acero', 'Encargo!B5': False,
                        'Encargo!B4': to_excel(dt.datetime(2026, 11, 10), epoch), 'Encargo!B6': '=9+1'}]:
        got = namespace['calculate_app']({'inputs': inputs, 'as_of': moment.isoformat()})['results']
        expected = Compiled(model).evaluate(inputs, as_of=moment)['results']
        assert {r['cell']: (r['value'], r.get('display')) for r in got} == {
            r['cell']: (r['value'], r.get('display')) for r in expected}
        assert next(r for r in got if r['cell'] == 'Encargo!C8')['uses_frozen']
        assert next(r for r in got if r['cell'] == 'Encargo!C7')['value'] == {'error': '#DIV/0!'}


def test_exported_app_runs_in_isolated_standard_library_python(tmp_path):
    model = load(workbook(tmp_path / 'quote.xlsx'))
    script = tmp_path / 'calculator.py'
    script.write_text(export_web_app(model), encoding='utf-8')
    before = {p.name for p in tmp_path.iterdir()}
    process = subprocess.Popen([sys.executable, '-I', '-S', str(script), '--port', '0'],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
        line = ready.get(timeout=15)
        assert line.startswith('Abre http://127.0.0.1:'), line
        url = line.strip().split()[-1]
        with urllib.request.urlopen(url, timeout=5) as response:
            page = response.read().decode('utf-8')
        assert '<form id="form">' in page and 'data:font/' in page
        request = urllib.request.Request(url + '/api/evaluate',
            data=json.dumps({'inputs': {'Encargo!B1': 5}, 'as_of': '2026-11-03T12:00'}).encode(),
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=5) as response:
            results = json.load(response)['results']
        assert next(r for r in results if r['cell'] == 'Encargo!C3')['value'] == 92.5
        with urllib.request.urlopen(url + '/licenses', timeout=5) as response:
            assert 'SIL OPEN FONT LICENSE' in response.read().decode('utf-8')
        request = urllib.request.Request(url + '/api/evaluate', data=b'[]',
                                         headers={'Content-Type': 'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        assert error.value.code == 422 and 'inputs' in json.load(error.value)['error']
        assert {p.name for p in tmp_path.iterdir()} == before
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_generated_javascript_parses(tmp_path):
    if not shutil.which('node'):
        pytest.skip('Node is needed only for source verification, not the exported calculator')
    namespace = {'__name__': 'client_calculator'}
    exec(export_web_app(load(workbook(tmp_path / 'quote.xlsx'))), namespace)
    script = re.search(r'<script>(.*?)</script>', namespace['_APP_HTML'], re.S).group(1)
    path = tmp_path / 'calculator.js'
    path.write_text(script, encoding='utf-8')
    subprocess.run(['node', '--check', str(path)], check=True, capture_output=True)
    # Execute the shipped input conversion, using Excel serials as an independent oracle.
    reader = script[script.index('function readInput('):script.index('async function calculate(')]
    checks = r'''
const vm=require('node:vm');
const context={};
vm.runInNewContext("const epoch=Date.UTC(1904,0,1);const temporal=k=>['date','time','datetime-local'].includes(k);"+READER,context);
const run=(kind,value,initial,current)=>context.readInput({p:{kind,value},input:{value:current},initial});
console.log(JSON.stringify([
run('date',44867.5,'2026-11-03','2026-11-10'),
run('time',44867.5,'12:00','13:30'),
run('datetime-local',44867.5,'2026-11-03T12:00','2026-11-03T13:30'),
run('date',44867.50000001,'2026-11-03','2026-11-03'),
run('date',44867,'2026-11-03','')
]));
'''.replace('READER', json.dumps(reader))
    result = json.loads(subprocess.check_output(['node', '-e', checks], text=True))
    assert result == [44874.5, 44867.5625, 44867.5625, 44867.50000001, None]


def test_export_handles_quoted_names_and_newlines_in_formula_comments(tmp_path):
    wb = openpyxl.Workbook()
    wb.active.title = 'Datos\nreales'
    wb.active['A1'] = 2
    wb.active['B1'] = '=A1+\n1'
    path = tmp_path / 'newlines.xlsx'
    wb.save(path)
    model = load(path, name='Cliente """\n cálculo')
    for code in [export_python(model), export_web_app(model)]:
        namespace = {'__name__': 'client_calculator'}
        exec(code, namespace)
        assert namespace['compute']() == {"'Datos\nreales'!B1": 3}


def test_calculator_discards_late_responses_and_clears_results_on_error(tmp_path):
    namespace = {'__name__': 'client_calculator'}
    exec(export_web_app(load(workbook(tmp_path / 'quote.xlsx'))), namespace)
    script = re.search(r'<script>(.*?)</script>', namespace['_APP_HTML'], re.S).group(1)
    calculate = script[script.index('async function calculate('):script.index("form.addEventListener('submit'")]
    checks = r'''
const vm=require('node:vm'),assert=require('node:assert/strict');
function element(){return {textContent:'',disabled:false,children:[],append(...nodes){this.children.push(...nodes)},replaceChildren(){this.children=[]}}}
const nodes=Object.fromEntries(['calculate','error','status','results','as-of','reset'].map(id=>[id,element()]));
const pending=[];
const context={document:{getElementById:id=>nodes[id],createElement:element},form:{querySelector:()=>nodes.reset},controls:new Map(),text:String,readInput:()=>0,fetch:()=>new Promise(resolve=>pending.push(resolve))};
vm.runInNewContext('let calculation=0;'+CALCULATE,context);
const response=value=>({ok:true,json:async()=>({results:[{cell:'Total',value}]})});
(async()=>{
 const old=context.calculate();assert.equal(nodes.reset.disabled,true);
 const current=context.calculate();pending[1](response(200));await current;
 pending[0](response(100));await old;
 assert.equal(nodes.results.children.length,1);assert.equal(nodes.results.children[0].children[1].textContent,'200');
 const failed=context.calculate();assert.equal(nodes.results.children.length,0);
 pending[2]({ok:false,json:async()=>({error:'Fallo de cálculo'})});await failed;
 assert.equal(nodes.results.children.length,0);assert.match(nodes.status.textContent,/No hay resultados actualizados/);
 assert.match(nodes.error.textContent,/Fallo de cálculo/);assert.equal(nodes.calculate.disabled,false);assert.equal(nodes.reset.disabled,false);
})().catch(error=>{console.error(error);process.exit(1)});
'''.replace('CALCULATE', json.dumps(calculate))
    subprocess.run(['node', '-e', checks], check=True, capture_output=True)


@pytest.mark.parametrize('fmt', ['hh', 'ss', 'hh:mm:ss'])
def test_hour_only_and_second_only_formats_remain_time_controls_and_results(tmp_path, fmt):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws['A1'] = dt.time(13, 30, 15)
    ws['B1'] = '=A1'
    for cell in ['A1', 'B1']:
        ws[cell].number_format = fmt
    path = tmp_path / 'time.xlsx'
    wb.save(path)
    namespace = {'__name__': 'client_calculator'}
    exec(export_web_app(load(path)), namespace)
    assert namespace['_APP_MODEL']['parameters'][0]['kind'] == 'time'
    result = namespace['calculate_app']({})['results'][0]
    assert result['display'] == '13:30:15'
    assert result['value'] == pytest.approx((13 * 3600 + 30 * 60 + 15) / 86400)


def test_app_is_exported_through_native_http_and_agent_tool(client):
    from nightingale.agent_tools import call_tool_sync, tool_catalog
    folder = client.services.config.data_dir / 'files'
    folder.mkdir(exist_ok=True)
    path = workbook(folder / 'quote.xlsx')
    compiled = client.post('/api/sheets/compile', json={'path': str(path)}).json()
    wid = compiled['id']
    response = client.get(f'/api/sheets/{wid}/export.app.py')
    assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
    compile(response.text, 'calculator.py', 'exec')
    result = call_tool_sync(client.services, 'data_workbook', {'action': 'export', 'workbook_id': wid, 'format': 'app'})
    assert result['format'] == 'app' and '--port' in result['usage']
    assert Path(result['path']).read_bytes() == response.content
    assert result['bytes'] == len(response.content)
    assert 'CalculatorHandler' not in client.get(f'/api/sheets/{wid}/export.py').text
    assert next(t for t in tool_catalog() if t['name'] == 'data_workbook')['inputSchema']['properties']['format']['enum'] == ['python', 'app']
