import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import vm from 'node:vm';
import test from 'node:test';
import {buildSync} from 'esbuild';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {makeT} from './src/i18n.js';

const compiled=buildSync({entryPoints:[new URL('./src/components/StepToolbar.jsx',import.meta.url).pathname.replace(/^\/([A-Z]:)/,'$1')],bundle:true,write:false,platform:'node',format:'cjs',external:['react'],define:{'import.meta.env.BASE_URL':'"/data/"'}}).outputFiles[0].text;
const module={exports:{}};
vm.runInNewContext(compiled,{module,exports:module.exports,require:createRequire(import.meta.url)});
const {STEP_FIELDS,buildParams,paramsToValues,StepFieldsGrid}=module.exports;
const plain=value=>JSON.parse(JSON.stringify(value));

test('editing a saved bin alias retains native edges/labels exactly and blank or empty edges keep bins',()=>{
  const saved={column:'importe',bins:5,edges:[0,10,25],labels:['Bajo, ñ','Medio "especial"',''],new_column:'antes'};
  const values=paramsToValues('bin',saved);
  values.new_column='Tramo editado ñ';
  assert.deepEqual(plain(buildParams('bin',values)),{...saved,new_column:'Tramo editado ñ'});
  for(const edges of [undefined,[]]){
    const legacy={column:'importe',bins:3,...(edges?{edges}:{}),new_column:'legacy'};
    assert.deepEqual(plain(buildParams('bin',paramsToValues('bin',legacy))),legacy);
  }
  values.edges='[0,';
  assert.throws(()=>buildParams('bin',values),/valid JSON/);
  assert.equal(values.edges,'[0,');
  assert.equal(values.labels,JSON.stringify(saved.labels));
  for(const lang of ['es','en']){
    const t=makeT(lang);
    const html=renderToStaticMarkup(React.createElement(StepFieldsGrid,{fields:STEP_FIELDS.bin,values,setField(){},columnNames:['importe'],t}));
    assert.ok(html.includes(t('bin_edges')));
    assert.ok(html.includes(t('bin_labels')));
    assert.match(html,/textarea[^>]+aria-describedby/);
    assert.match(html,/NULL/);
    assert.doesNotMatch(html,/bin_edges_help|bin_labels_help/);
  }
});
