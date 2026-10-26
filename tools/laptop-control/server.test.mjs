import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer, coordinates, Pi5Remote, ThinQSession} from './server.mjs';

test('ThinQ bridge keeps its host key out of public replies and enforces expiry',async()=>{
  const key='a'.repeat(64),calls=[];let now=100;
  const thinq=new ThinQSession({clock:()=>now,fetcher:async(url,options)=>{
    calls.push({url,options});
    return new Response(JSON.stringify(url.endsWith('/bootstrap')?{key,expires_in_seconds:1200}:{status:'read_ok'}));
  }});
  assert.equal(thinq.connected,false);
  const connected=await thinq.connect();
  assert.equal(JSON.stringify(connected).includes(key),false);
  assert.equal(thinq.connected,true);
  await thinq.call({op:'read'});
  assert.equal(calls.at(-1).options.headers['X-Deskmate-Session'],key);
  await assert.rejects(thinq.call({op:'read',access_token:'secret'}));
  await assert.rejects(thinq.call({op:'shell'}));
  now+=1200000;
  assert.equal(thinq.connected,false);
  await assert.rejects(thinq.call({op:'read'}));
  assert.equal(calls.length,2);
});

test('ThinQ host revocation clears the local session',async()=>{
  let response=200;
  const thinq=new ThinQSession({fetcher:async(url)=>new Response(JSON.stringify(url.endsWith('/bootstrap')?
    {key:'b'.repeat(64),expires_in_seconds:1200}:{status:'session_expired'}),{status:response})});
  await thinq.connect();response=401;
  await assert.rejects(thinq.call({op:'read'}));
  assert.equal(thinq.connected,false);
});

test('coordinates reject malformed and shell-shaped commands',()=>{
  assert.deepEqual(coordinates({x1:0,y1:0,x2:1023,y2:599}),[0,0,1023,599]);
  for(const body of [{x1:'1; reboot',y1:0,x2:0,y2:0},{x1:1024,y1:0,x2:0,y2:0},
    {x1:0,y1:600,x2:0,y2:0},{x1:0.5,y1:0,x2:0,y2:0},
    {x1:0,y1:0,x2:0,y2:0,command:'reboot'}]) assert.throws(()=>coordinates(body));
});

test('remote uses verified SSH, bounded capture caching, serialized input and cleanup',async()=>{
  const calls=[];const png=Buffer.from([137,80,78,71,13,10,26,10,0]);
  const remote=new Pi5Remote({host:'fe80::1234%21',knownHosts:'C:/verified hosts',input:'input',
    runner:async(file,args)=>{calls.push([file,args]);return{stdout:png};}});
  await remote.prepare();
  assert.ok(calls[0][1].includes('StrictHostKeyChecking=yes'));
  assert.ok(calls[0][1].includes('root@[fe80::1234%21]:/tmp/deskmate-laptop-input'));
  const [a,b]=await Promise.all([remote.frame(),remote.frame()]);
  assert.deepEqual(a,b);
  assert.equal(calls.filter(c=>c[1].at(-1).includes('CaptureScreen')).length,1);
  await remote.touch({x1:120,y1:50,x2:120,y2:300});
  assert.equal(calls.at(-1)[1].at(-1),'/tmp/deskmate-laptop-input 120 50 120 300');
  await remote.close();assert.ok(calls.at(-1)[1].at(-1).startsWith('rm -f /tmp/deskmate-laptop-input'));
});

test('web API permits only local authenticated actions; sensor proxy remains separate',async()=>{
  const port=19000+Math.floor(Math.random()*10000),base=`http://127.0.0.1:${port}`;
  const actions=[],remote={host:'pi5',frame:async()=>Buffer.from([137,80,78,71]),
    touch:async b=>{coordinates(b);actions.push(b);},openApp:async()=>actions.push('open')};
  const sensors={host:'',port:8790,start:async host=>{sensors.host=host;}};
  const token='test-session',server=createServer({remote,sensors,port,token});
  await new Promise(resolve=>server.listen(port,'127.0.0.1',resolve));
  try {
    const status=await(await fetch(base+'/api/status')).json();assert.equal(status.pi5,'pi5');
    const page=await(await fetch(base)).text();assert.ok(page.includes(token));
    assert.equal((await fetch(base+'/api/touch',{method:'POST',body:'{}'})).status,403);
    const headers={'Content-Type':'application/json','X-Deskmate-Key':token,'Origin':base};
    assert.equal((await fetch(base+'/api/open',{method:'POST',headers:{...headers,Origin:'https://attacker.invalid'},body:'{}'})).status,403);
    assert.equal((await fetch(base+'/api/touch',{method:'POST',headers,body:JSON.stringify({x1:10,y1:20,x2:10,y2:20})})).status,200);
    assert.equal(actions.length,1);
    assert.equal((await fetch(base+'/api/touch',{method:'POST',headers,body:JSON.stringify({x1:-1,y1:0,x2:0,y2:0})})).status,400);
    assert.equal(actions.length,1);
    assert.equal((await fetch(base+'/api/shell',{method:'POST',headers,body:'{}'})).status,404);
    assert.equal((await fetch(base+'/sensors')).status,503);
    assert.equal((await fetch(base+'/api/open',{method:'POST',headers,body:'{}'})).status,200);
    assert.equal(actions.at(-1),'open');
  } finally {server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
