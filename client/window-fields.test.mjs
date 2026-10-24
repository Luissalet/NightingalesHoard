import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import {buildSync} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const compiled=buildSync({entryPoints:[new URL('./src/components/StepToolbar.jsx',import.meta.url).pathname.replace(/^\/([A-Z]:)/,'$1')],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],define:{'import.meta.env.BASE_URL':'"/data/"'}}).outputFiles[0].text;
const module={exports:{}};
vm.runInNewContext(compiled,{module,exports:module.exports,require:createRequire(import.meta.url)});
const {STEP_FIELDS,buildParams,paramsToValues,StepFieldsGrid}=module.exports;
const plain=value=>JSON.parse(JSON.stringify(value));

test('editing a saved window preserves explicit sizes, zero and hidden parameters while blank values keep native defaults',()=>{
  for(const params of [
    {fn:'lag',column:'amount',order_by:['seq'],offset:2,new_column:'old'},
    {fn:'rolling_sum',column:'amount',order_by:['seq'],partition_by:['group'],window_size:5,offset:0,new_column:'old'},
  ]){
    const values=paramsToValues('window',params);
    values.new_column='Edited alias ñ';
    assert.deepEqual(plain(buildParams('window',values)),{...params,new_column:'Edited alias ñ'});
  }
  assert.deepEqual(plain(buildParams('window',{fn:'lag',offset:0,window_size:''})),{fn:'lag',offset:0});
  assert.deepEqual(plain(buildParams('window',{fn:'rolling_mean',offset:'',window_size:''})),{fn:'rolling_mean'});
  const html=fn=>renderToStaticMarkup(React.createElement(StepFieldsGrid,{fields:STEP_FIELDS.window,values:{fn,offset:0,window_size:5},setField(){},columnNames:['amount','seq','group'],t:key=>key}));
  assert.match(html('lag'),/window_offset/);assert.doesNotMatch(html('lag'),/window_size_label/);
  assert.match(html('rolling_sum'),/window_size_label/);assert.doesNotMatch(html('rolling_sum'),/window_offset/);
  assert.doesNotMatch(html('rank'),/window_offset|window_size_label/);
});
