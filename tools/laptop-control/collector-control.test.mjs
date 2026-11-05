import test from 'node:test';
import assert from 'node:assert/strict';
import {CollectorControl} from './collector-control.mjs';
import {createServer} from './server.mjs';

test('missing collector is unknown, never ON, and failed set is not acknowledged',async()=>{
  const c=new CollectorControl({run:async()=>{throw Error('unavailable');}});
  assert.equal((await c.status()).keyboard_enabled,null);
  await assert.rejects(c.set(true));
});
test('only actual acknowledged keyboard state is returned and no process is spawned',async()=>{
  const calls=[];
  const c=new CollectorControl({run:async(exe,args)=>{calls.push(args);return {stdout:JSON.stringify({connected:true,keyboard_enabled:args.at(-1)==='true',mouse_enabled:true,mqtt_publish_enabled:false})};}});
  assert.equal((await c.set(false)).keyboard_enabled,false);
  assert.equal((await c.set(true)).keyboard_enabled,true);
  assert.equal(calls.length,2);
  assert.equal((await c.status()).mouse_enabled,true);
  await assert.rejects(c.set('true'));
});
test('collector toggle requires same-origin key and strict boolean independently of ThinQ',async()=>{
  let enabled=true;
  const http=await import('node:http');
  const probe=http.createServer();await new Promise(r=>probe.listen(0,'127.0.0.1',r));const port=probe.address().port;await new Promise(r=>probe.close(r));
  const s=createServer({port,token:'test-only',remote:{host:'test'},sensors:{host:''},collector:{status:async()=>({connected:true,keyboard_enabled:enabled}),set:async(v)=>{enabled=v;return {connected:true,keyboard_enabled:v};}}});
  await new Promise(r=>s.listen(port,'127.0.0.1',r));
  try {
    const url=`http://127.0.0.1:${port}`;
    const call=(body,key='test-only')=>fetch(url+'/api/collector/keyboard',{method:'POST',headers:{Origin:url,'Content-Type':'application/json','X-Deskmate-Key':key},body:JSON.stringify(body)});
    assert.equal((await call({enabled:false},'wrong')).status,403);assert.equal(enabled,true);
    assert.equal((await call({enabled:'false'})).status,400);
    assert.equal((await call({enabled:false})).status,200);assert.equal(enabled,false);
    const status=await (await fetch(url+'/api/collector/status')).json();assert.equal(status.keyboard_enabled,false);
  } finally {await new Promise(r=>s.close(r));}
});
