import test from 'node:test';
import assert from 'node:assert/strict';
import {redirectServer} from './legacy-redirect.mjs';
test('legacy consoles only redirect navigation and never relay commands or credentials',async()=>{
  const s=redirectServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));
  const url=`http://127.0.0.1:${s.address().port}`;
  try {
    const page=await fetch(url+'/',{redirect:'manual'});
    assert.equal(page.status,302);assert.equal(page.headers.get('location'),'http://127.0.0.1:19891/');
    assert.equal((await fetch(url+'/api/thinq/call',{method:'POST',body:'{}'})).status,410);
    assert.equal((await fetch(url+'/api/collector/keyboard',{method:'POST',body:'{}'})).status,410);
    assert.equal((await fetch(url+'/api/status')).status,410);
  }finally{await new Promise(r=>s.close(r));}
});
