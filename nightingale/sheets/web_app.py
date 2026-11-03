"""Export a compiled workbook as one offline, standard-library web application."""
from __future__ import annotations

import base64
import html
import json
import re
from importlib import metadata, resources
from pathlib import Path

from .compiler import Model, is_date_format
from .engine import Compiled, export_python


def _date_kind(fmt):
    if not is_date_format(fmt):
        return None
    fmt = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', '', fmt).lower()
    has_date, has_time = bool(re.search('[dy]', fmt)), bool(re.search('[hs]', fmt))
    return 'datetime-local' if has_date and has_time else 'time' if has_time else 'date'


def export_web_app(model: Model) -> str:
    """The exported file contains its calculation, interface, fonts and licences."""
    frozen_upstream = Compiled(model).frozen_upstream()
    parameters = []
    for key in model.parameters:
        item = model.describe(key)
        value = item['value']
        if item.get('options'):
            kind = 'select'
        elif isinstance(value, bool):
            kind = 'checkbox'
        elif isinstance(value, (int, float)):
            kind = _date_kind(model.cells[key].number_format) or 'number'
        else:
            kind = 'error' if isinstance(value, dict) and 'error' in value else 'text'
        parameters.append({**item, 'kind': kind})
    result_fields = [{**model.describe(k), 'date_kind': _date_kind(model.cells[k].number_format),
                      'uses_frozen': bool(model.cells[k].frozen or k in frozen_upstream)} for k in model.results]
    info = {'name': model.name, 'parameters': parameters, 'results': result_fields,
            'date_system': model.date_system, 'as_of': any(c.volatile for c in model.cells.values()),
            'frozen': sum(bool(c.frozen) for c in model.cells.values())}
    fonts = resources.files('fusion').joinpath('web/fonts')
    font_css = []
    licences = []
    for family, filename, mime, fmt in [('Cinzel', 'Cinzel.ttf', 'font/ttf', 'truetype'),
                                      ('Source Sans 3', 'SourceSans3.woff2', 'font/woff2', 'woff2')]:
        data = base64.b64encode(fonts.joinpath(filename).read_bytes()).decode('ascii')
        font_css.append(f"@font-face{{font-family:'{family}';src:url(data:{mime};base64,{data}) format('{fmt}');font-weight:100 900;font-display:swap}}")
        licences.append(fonts.joinpath('OFL-' + family.replace(' ', '') + '.txt').read_text(encoding='utf-8'))
    source_licence = Path(__file__).resolve().parents[2] / 'LICENSE'
    licence = source_licence.read_text(encoding='utf-8') if source_licence.is_file() else (
        metadata.distribution('nightingale-hoard').read_text('licenses/LICENSE') or
        metadata.distribution('nightingale-hoard').metadata.get('License', ''))
    licences.insert(0, licence)
    encoded = json.dumps(info, ensure_ascii=False).replace('<', '\\u003c')
    replacements = {'__TITLE__': html.escape(model.name), '__FONTS__': '\n'.join(font_css), '__MODEL__': encoded}
    page = re.sub('__TITLE__|__FONTS__|__MODEL__', lambda match: replacements[match.group()], _HTML)
    return (export_python(model, cli=False) + '\n_APP_MODEL = ' + repr(info) +
            '\n_APP_HTML = ' + repr(page) + '\n_APP_LICENCES = ' + repr('\n\n'.join(licences)) + '\n' + _SERVER)


_HTML = r'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
__FONTS__
:root{color-scheme:dark;--bg:#1c1416;--surface:#241b1d;--line:#433538;--control-line:#8b7379;--text:#ece1e4;--muted:#beb4b6;--accent:#de8396;--danger:#ee8b80;font:16px/1.55 'Source Sans 3',sans-serif;background:var(--bg);color:var(--text)}
*{box-sizing:border-box}body{margin:0}::selection{background:#de83964d}main{max-width:1140px;margin:auto;padding:48px 32px 32px}h1{font:600 30px/1.2 Cinzel,serif;max-width:32ch;overflow-wrap:anywhere;text-wrap:balance;margin:0 0 12px}h2{font-size:20px;margin:0 0 22px}p{max-width:72ch}.help{color:var(--muted);font-size:15px}header{margin-bottom:38px}header p{margin:0}.layout{display:grid;grid-template-columns:minmax(260px,380px) minmax(0,1fr);gap:48px}.results{min-width:0;border-left:1px solid var(--line);padding-left:40px}.field{margin-bottom:22px}label{display:block;margin:0 0 6px;font-weight:600}input,select,button{font:inherit}input:not([type=checkbox]),select{width:100%;min-height:44px;background:var(--surface);color:var(--text);border:1px solid var(--control-line);border-radius:6px;padding:8px 12px;caret-color:var(--accent)}input::placeholder{color:var(--muted)}input[type=checkbox]{width:20px;height:20px;accent-color:var(--accent);margin:0 12px 0 0}.check{display:flex;align-items:center;min-height:44px}.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:28px}button{min-height:44px;padding:9px 18px;border:1px solid var(--line);border-radius:6px;background:transparent;color:var(--text);cursor:pointer;transition:background .15s ease-out}button.primary{background:var(--accent);border-color:var(--accent);color:#1b1315;font-weight:600}button:hover{background:#2f2527}button.primary:hover{background:#e7a3b1}button:disabled{opacity:.6;cursor:wait}:focus-visible{outline:3px solid var(--accent);outline-offset:3px}table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:14px 0;border-bottom:1px solid var(--line);text-align:left;overflow-wrap:anywhere}th{font-size:14px;color:var(--muted);font-weight:400}th:last-child,td:last-child{text-align:right;padding-left:20px}td:last-child{font-size:19px;white-space:normal}td:first-child{width:60%}.error{color:var(--danger)}#status{min-height:24px;margin:20px 0 0}#error:empty,#frozen:empty{display:none}.saved{display:block;font-size:13px;color:var(--muted)}footer{border-top:1px solid var(--line);margin-top:44px;padding-top:18px;font-size:13px}a{color:var(--muted);text-underline-offset:3px}a:hover{color:var(--text)}@media(max-width:720px){main{padding:28px 22px}.layout{grid-template-columns:minmax(0,1fr);gap:36px}.results{border-left:0;border-top:1px solid var(--line);padding:28px 0 0}header{margin-bottom:30px}h1{font-size:26px}}@media(prefers-reduced-motion:reduce){button{transition:none}}
</style></head><body><main>
<header><h1>__TITLE__</h1><p class="help">Modifica los parámetros y calcula los resultados.</p></header>
<div class="layout"><section aria-labelledby="parameters-title"><h2 id="parameters-title">Parámetros</h2>
<form id="form"><div id="fields"></div><div id="moment" class="field" hidden><label for="as-of">Fecha del cálculo (opcional)</label><input id="as-of" type="datetime-local"></div>
<div class="actions"><button class="primary" id="calculate">Calcular</button><button type="reset">Restablecer</button></div>
<p id="error" class="error" role="alert"></p></form></section>
<section class="results" aria-labelledby="results-title"><h2 id="results-title">Resultados</h2><p id="frozen" class="help"></p>
<table><thead><tr><th scope="col">Resultado</th><th scope="col">Valor</th></tr></thead><tbody id="results"></tbody></table><p id="status" class="help" role="status" aria-live="polite"></p></section></div>
<footer><a href="/licenses" target="_blank" rel="noopener">Licencias del cálculo y las fuentes</a></footer>
<script type="application/json" id="model">__MODEL__</script><script>
const model=JSON.parse(document.getElementById('model').textContent),form=document.getElementById('form'),controls=new Map();let calculation=0;
const epoch=model.date_system==='1904'?Date.UTC(1904,0,1):Date.UTC(1899,11,30);
const temporal=kind=>['date','datetime-local','time'].includes(kind);
const toISO=(v,kind)=>{const iso=new Date(epoch+Math.round(v*86400000)).toISOString();return kind==='time'?iso.slice(11,23):iso.slice(0,kind==='date'?10:23)};
const text=v=>v&&typeof v==='object'&&v.error?v.error:typeof v==='boolean'?(v?'Sí':'No'):typeof v==='number'?new Intl.NumberFormat('es-ES',{maximumSignificantDigits:12}).format(v):v??'';
for(const [i,p]of model.parameters.entries()){
 const row=document.createElement('div');row.className='field';const label=document.createElement('label');label.htmlFor='param-'+i;label.textContent=p.label||p.cell;
 const input=document.createElement(p.kind==='select'?'select':'input');input.id=label.htmlFor;input.name=p.cell;
 if(p.kind==='select'){for(const [n,v]of p.options.entries()){const o=document.createElement('option');o.value=String(n);o.textContent=text(v);input.append(o)}if(!p.options.some(v=>JSON.stringify(v)===JSON.stringify(p.value))){const o=document.createElement('option');o.value='-1';o.textContent=text(p.value);input.prepend(o)}}
 else{input.type=p.kind==='error'?'text':p.kind;if(['number','datetime-local','time'].includes(p.kind))input.step='any'}
 if(p.kind==='checkbox'){label.className='check';label.prepend(input);row.append(label)}else row.append(label,input);
 document.getElementById('fields').append(row);controls.set(p.cell,{p,input});
}
if(!model.parameters.length){const p=document.createElement('p');p.className='help';p.textContent='Este cálculo no tiene parámetros editables.';document.getElementById('fields').append(p)}
document.getElementById('moment').hidden=!model.as_of;
if(model.frozen)document.getElementById('frozen').textContent=model.frozen+' celdas conservan valores guardados del libro. Los resultados que dependen de ellas están marcados.';
function reset(){
 for(const control of controls.values()){
  const {p,input}=control;
  if(p.kind==='checkbox')input.checked=p.value;
  else if(p.kind==='select')input.value=String(p.options.findIndex(v=>JSON.stringify(v)===JSON.stringify(p.value)));
  else input.value=p.value==null?'':temporal(p.kind)?toISO(p.value,p.kind):p.kind==='error'?p.value.error:String(p.value);
  control.initial=input.value;
 }
 document.getElementById('as-of').value='';
}
function readInput({p,input,initial}){
 if(p.kind==='checkbox')return input.checked;
 if(p.kind==='select')return input.value==='-1'?p.value:p.options[Number(input.value)];
 if(p.kind==='number')return input.value===''?null:Number(input.value);
 if(temporal(p.kind)){
  if(input.value===initial)return p.value;
  if(!input.value)return null;
  if(p.kind==='time')return Math.floor(p.value)+Date.parse('1970-01-01T'+input.value+'Z')/86400000;
  const serial=(Date.parse(input.value+(p.kind==='date'?'T00:00:00Z':'Z'))-epoch)/86400000;
  return serial+(p.kind==='date'?p.value-Math.floor(p.value):0);
 }
 if(p.kind==='error')return {error:input.value};
 return input.value===''&&p.value==null?null:input.value;
}
async function calculate(){
 const request=++calculation,button=document.getElementById('calculate'),resetButton=form.querySelector('[type=reset]'),error=document.getElementById('error'),status=document.getElementById('status'),body=document.getElementById('results');button.disabled=true;resetButton.disabled=true;button.textContent='Calculando…';error.textContent='';status.textContent='Calculando…';body.replaceChildren();
 try{const inputs=Object.fromEntries(Array.from(controls,([cell,control])=>[cell,readInput(control)]));
 const response=await fetch('/api/evaluate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({inputs,as_of:document.getElementById('as-of').value||null})});const data=await response.json();if(request!==calculation)return;if(!response.ok)throw new Error(data.error||'No se pudo calcular.');
 for(const r of data.results){const tr=document.createElement('tr'),name=document.createElement('td'),value=document.createElement('td');name.textContent=r.label||r.cell;value.textContent=r.display||text(r.value);if(r.value&&typeof r.value==='object'&&r.value.error)value.className='error';if(r.uses_frozen){const note=document.createElement('span');note.className='saved';note.textContent='Usa valores guardados';name.append(note)}tr.append(name,value);body.append(tr)}status.textContent=data.results.length?'Resultados actualizados.':'El libro no contiene resultados de cálculo.';
 }catch(e){if(request===calculation){error.textContent=e.message+' Revisa los valores e inténtalo de nuevo.';status.textContent='No hay resultados actualizados.'}}finally{if(request===calculation){button.disabled=false;resetButton.disabled=false;button.textContent='Calcular'}}
}
form.addEventListener('submit',e=>{e.preventDefault();calculate()});form.addEventListener('reset',e=>{e.preventDefault();reset();calculate()});reset();calculate();
</script></main></body></html>'''


_SERVER = r'''
# The server uses only Python's standard library and never writes calculation data.
import argparse as _argparse
import datetime as _datetime
import json as _json
from http.server import BaseHTTPRequestHandler as _BaseHandler, ThreadingHTTPServer as _HTTPServer


def calculate_app(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('inputs', {}), dict):
        raise ValueError('Se esperaba un objeto con los parámetros en inputs.')
    moment = _datetime.datetime.fromisoformat(payload['as_of']) if payload.get('as_of') else None
    values = compute(payload.get('inputs', {}), as_of=moment)
    results = []
    for field in _APP_MODEL['results']:
        value = values[field['cell']]
        row = {key: field[key] for key in ('cell', 'label', 'uses_frozen') if key in field}
        row['value'] = value
        if field['date_kind'] and isinstance(value, (int, float)) and not isinstance(value, bool):
            moment = from_serial(value, DATE_SYSTEM)
            row['display'] = (moment.time().isoformat(timespec='seconds') if field['date_kind'] == 'time' else
                              moment.isoformat(sep=' ', timespec='seconds') if field['date_kind'] == 'datetime-local' else
                              moment.date().isoformat())
        results.append(row)
    return {'results': results}


class CalculatorHandler(_BaseHandler):
    def _reply(self, body, content_type, status=200):
        data = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == '/':
            return self._reply(_APP_HTML, 'text/html')
        if self.path == '/licenses':
            return self._reply(_APP_LICENCES, 'text/plain')
        if self.path == '/favicon.ico':
            return self._reply('', 'image/x-icon', 204)
        self._reply('No encontrado.', 'text/plain', 404)

    def do_POST(self):
        if self.path != '/api/evaluate':
            return self._reply('No encontrado.', 'text/plain', 404)
        try:
            payload = _json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            body = _json.dumps(calculate_app(payload), ensure_ascii=False, allow_nan=False)
        except (ValueError, KeyError, TypeError) as error:
            return self._reply(_json.dumps({'error': str(error)}, ensure_ascii=False), 'application/json', 422)
        self._reply(body, 'application/json')

    def log_message(self, *args):
        pass


if __name__ == '__main__':
    parser = _argparse.ArgumentParser(description='Calculadora del libro ' + _APP_MODEL['name'])
    parser.add_argument('--port', type=int, default=8080)
    args = parser.parse_args()
    server = _HTTPServer(('127.0.0.1', args.port), CalculatorHandler)
    print('Abre http://127.0.0.1:' + str(server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
'''
