"""Native compatibility MCP names; static discovery, no application autostart."""
import asyncio,json,os
from pathlib import Path
import httpx
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult,TextContent,Tool

ROOT=Path(__file__).resolve().parent.parent
IDENTITY=os.environ['HOARD_COMPAT_ID']
CONFIG=json.loads((ROOT/'compatibility'/IDENTITY/'bridge.json').read_text(encoding='utf-8'))
app=Server(IDENTITY+'-hoard')

@app.list_tools()
async def list_tools():
    return [Tool.model_validate(t['schema']) for t in CONFIG['tools']]

@app.call_tool()
async def call_tool(name,arguments):
    item=next((t for t in CONFIG['tools'] if t['schema']['name']==name),None)
    if item is None:raise ValueError('Unknown native tool: '+name)
    data_dir=Path(os.environ.get(CONFIG['owner'].upper()+'_DATA_DIR') or ROOT/'data')
    settings_file=data_dir/('fusion-modules.json' if CONFIG['owner']=='borges' else 'modules.json')
    settings=json.loads(settings_file.read_text(encoding='utf-8')) if settings_file.exists() else {}
    native_dir=settings.get('data_paths',{}).get(item['module'])
    token_file=Path(native_dir)/'mcp-token' if native_dir else ROOT/item['token_file']
    token=token_file.read_text(encoding='utf-8').strip()
    base=os.environ.get('HOARD_COMPAT_URL') or CONFIG['owner_url']
    timeout=float(item['schema'].get('x-timeout-s') or item['schema'].get('inputSchema',{}).get('x-timeout-s') or 180)
    wait=(arguments or {}).get('wait_s')
    if isinstance(wait,(int,float)) and not isinstance(wait,bool):timeout=max(timeout,wait+90)
    headers={'Authorization':'Bearer '+token}
    # Native SDKs keep their original caller/session context on the private HTTP leg.
    meta=getattr(app.request_context,'meta',None)
    fields=meta.model_dump() if hasattr(meta,'model_dump') else {}
    for key in ('agent_id','session_id','run_id','parent_call_id'):
        if fields.get(key):headers['X-'+key.replace('_','-')]=str(fields[key])
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout,connect=10),trust_env=False) as client:
            response=await client.post(base.rstrip('/')+'/'+item['module']+'/api/agent/call',json={'name':name,'arguments':arguments or {}},headers=headers)
        body=response.json()
        error=response.is_error or (isinstance(body,dict) and bool(body.get('error')))
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(body,ensure_ascii=False))],structuredContent=body if isinstance(body,dict) else None,isError=error)
    except httpx.TimeoutException:
        body={'error':'timeout','tool':name,'outcome_unknown':not item['schema'].get('annotations',{}).get('readOnlyHint',False),'message':'The call was not replayed. Check native status before retrying.'}
        return CallToolResult(content=[TextContent(type='text',text=json.dumps(body))],structuredContent=body,isError=True)
    except (OSError,httpx.HTTPError) as exc:raise RuntimeError('Start the merged app explicitly from Hoard Hub: '+str(exc)) from exc

async def main():
    async with stdio_server() as (reader,writer):await app.run(reader,writer,app.create_initialization_options())
if __name__=='__main__':asyncio.run(main())
