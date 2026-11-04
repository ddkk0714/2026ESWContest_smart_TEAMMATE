import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {ThinQUiState,snapshotRows,renderSnapshot} from './thinq-ui.mjs';
import {ThinQSession,createServer} from './server.mjs';

test('manual HTML refuses Fixture and read-only credentials and caches the verified access mode',async()=>{
  for(const claim of [{mode:'fixture',access:'manual_control'},{mode:'live',access:'read_only'}]){
    const client=new ThinQSession({expectedMode:'live',requiredAccess:'manual_control',fetcher:async()=>new Response(JSON.stringify({key:'e'.repeat(64),expires_in_seconds:1200,...claim}))});
    await assert.rejects(client.connect());assert.equal(client.connected,false);
  }
  let requests=0;
  const client=new ThinQSession({expectedMode:'live',requiredAccess:'manual_control',fetcher:async()=>{requests++;return new Response(JSON.stringify({key:'e'.repeat(64),expires_in_seconds:1200,mode:'live',access:'manual_control'}));}});
  const first=await client.connect(),again=await client.connect();
  assert.equal(first.access,'manual_control');assert.equal(again.mode,'live');assert.equal(again.access,'manual_control');assert.equal(requests,1);
  for(const op of ['observation','policy_fixture','policy_respond'])await assert.rejects(client.call({op}));
  assert.equal(requests,1);
});

async function manualUi({loseResponse=false}={}){
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
  const inline=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^import .*?;$/m,'');
  const elements=new Map(),requests=[];
  class Element {disabled=false;hidden=false;checked=false;textContent='';style={};children=[];classList={toggle(){}};addEventListener(){};append(child){this.children.push(child);}replaceChildren(...children){this.children=children;}showModal(){this.open=true;}close(){this.open=false;}}
  const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const buttons=['thinqConnect','thinqRead','thinqLow','thinqAuto','thinqExecute','thinqPhysical','thinqRevoke','closeInfo'];
  const context=vm.createContext({
    document:{documentElement:{dataset:{runMode:'live-manual'}},getElementById:element,createElement:()=>new Element(),querySelectorAll:()=>buttons.map(element)},
    ThinQUiState,renderSnapshot:()=>{},performance:{now:()=>0},
    fetch:async(route,options)=>{
      const body=options?.body?JSON.parse(options.body):{};requests.push({route,body});
      let reply={pi4:''};
      if(route==='/api/thinq/connect')reply={status:'session_connected',mode:'live',access:'manual_control'};
      if(body.op==='read')reply={status:'read_ok',mode:'live',post_enabled:true};
      if(body.op==='prepare')reply={status:'approval_required',nonce:'synthetic-one-use',value:body.value,post_enabled:true};
      if(body.op==='execute'){
        if(loseResponse)throw Error('safe synthetic timeout');
        reply={status:'state_match',api_accepted:true,state_match:true,physical_confirmed:false,value:'LOW',post_enabled:true};
      }
      if(body.op==='physical_confirmation')reply={status:'user_attestation_recorded',physical_confirmed:true,post_enabled:true};
      return {ok:true,json:async()=>reply,blob:async()=>({})};
    },URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},setInterval(){},setTimeout(){},window:{addEventListener(){}},
  });
  new vm.Script(inline).runInContext(context);
  return {element,requests};
}

test('manual HTML requires explicit two-condition acceptance, sends once, and separates physical evidence',async()=>{
  const {element,requests}=await manualUi();
  assert.equal(element('thinqManualPanel').hidden,false);
  assert.equal(element('purifierFixtureScenarios').hidden,true);
  for(const id of ['openApp','remoteTab','connect'])assert.equal(element(id).disabled,true);
  await element('thinqConnect').onclick();
  assert.equal(element('thinqLow').disabled,true);
  await element('thinqRead').onclick();
  assert.equal(element('thinqLow').disabled,false);
  assert.equal(element('thinqExecute').disabled,true);
  await element('thinqLow').onclick();
  assert.equal(requests.filter(r=>r.body.op==='execute').length,0);
  assert.equal(element('thinqExecute').disabled,true);
  element('thinqOnline').checked=true;element('thinqOnline').onchange();
  assert.equal(element('thinqExecute').disabled,true);
  element('thinqUnchanged').checked=true;element('thinqUnchanged').onchange();
  assert.equal(element('thinqExecute').disabled,false);
  const first=element('thinqExecute').onclick(),duplicate=element('thinqExecute').onclick();
  await Promise.all([first,duplicate]);
  assert.equal(requests.filter(r=>r.body.op==='execute').length,1);
  assert.equal(element('thinqExecute').disabled,true);
  const evidence=element('thinqTrialEvidence').children[0].textContent;
  assert.ok(evidence.includes('API 접수: 확인')&&evidence.includes('GET 상태 일치: 확인')&&evidence.includes('사용자 확인 전'));
  assert.equal(requests.filter(r=>r.body.op==='physical_confirmation').length,0);
  await element('thinqPhysical').onclick();
  assert.ok(element('thinqTrialEvidence').children.at(-1).textContent.includes('사용자 확인 완료'));
});

test('manual HTML transport loss locks controls across GET and never automatically restores',async()=>{
  const {element,requests}=await manualUi({loseResponse:true});
  await element('thinqConnect').onclick();await element('thinqRead').onclick();await element('thinqLow').onclick();
  element('thinqOnline').checked=true;element('thinqUnchanged').checked=true;
  await element('thinqExecute').onclick();
  assert.equal(element('thinqLow').disabled,true);assert.equal(element('thinqAuto').disabled,true);
  await element('thinqRead').onclick();
  assert.equal(element('thinqAuto').disabled,true);
  assert.equal(requests.filter(r=>r.body.op==='execute').length,1);
  assert.equal(requests.filter(r=>r.body.op==='prepare'&&r.body.value==='AUTO').length,0);
  assert.ok(element('thinqTrialEvidence').children[0].textContent.includes('재전송/자동 복원 금지'));
});

const normal={mode:'fixture',api_status:'ok',connection:'unknown',receipt_stale:false,received_at:1000,
  expires_at:1120,api_age_sec:0,fields:{pm2:{value:8,valid:true,status:'ok'},filter_remain_percent:{value:96,valid:true,status:'ok'}}};
test('uncertain write stays locked across successful GET and finally-like updates',()=>{
  const s=new ThinQUiState();s.receive({post_enabled:true});assert.equal(s.canPrepare(),true);
  s.receive({status:'outcome_unconfirmed',post_enabled:false});s.receive({status:'read_ok',post_enabled:true});
  assert.equal(s.canPrepare(),false);
});
test('transport loss during POST locks while an ordinary read failure does not authorize',()=>{
  const s=new ThinQUiState();s.transportFailure(false);assert.equal(s.canPrepare(),false);
  s.receive({post_enabled:true});s.transportFailure(true);assert.equal(s.canPrepare(),false);
});
test('unknown is not offline, PM2 meaning is not invented, expiry hides current values',()=>{
  const rows=snapshotRows(normal);
  assert.equal(rows.find(r=>r[0]==='PM2 (API 필드명)')[1],'8');
  assert.ok(rows.some(r=>r[1].includes('Fixture 테스트')));
  assert.ok(rows.some(r=>r[1].includes('unknown')));
  assert.ok(rows.some(r=>r[1].includes('PM2의 정확한 의미')));
  assert.equal(snapshotRows(normal,121).find(r=>r[0]==='PM2 (API 필드명)')[1],'--');
  assert.equal(snapshotRows({...normal,api_status:'api_error'}).find(r=>r[0]==='PM2 (API 필드명)')[1],'--');
});
test('snapshot renderer writes text nodes, never HTML from API fields',()=>{
  class Element {children=[];textContent='';append(...a){this.children.push(...a);}replaceChildren(...a){this.children=a;}}
  const root=new Element();const doc={getElementById:()=>root,createElement:()=>new Element()};
  renderSnapshot(doc,'target',normal);assert.equal(root.children.length,1);
  assert.ok(root.children[0].children.some(x=>x.textContent==='8'));
});
test('HTML retains current sensor iframe and separates control approval from snapshot polling',async()=>{
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
  assert.ok(html.includes('id="sensorFrame"'));assert.ok(html.includes('id="thinqSummary"'));
  assert.ok(html.includes('op:\'snapshot\''));assert.ok(!html.includes("thinqShow(await post('/api/thinq/connect'));thinqShow(await post('/api/thinq/call',{op:'read'}))"));
  const inline=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^import .*?;$/m,'');
  new vm.Script(inline); // Parse the actual browser handlers; no network or device action.
});
test('Fixture frontend refuses a live auth backend before any read or write',async()=>{
  let count=0;const client=new ThinQSession({expectedMode:'fixture',fetcher:async()=>{count++;return new Response(JSON.stringify({key:'c'.repeat(64),mode:'live',expires_in_seconds:20}));}});
  await assert.rejects(client.connect());assert.equal(client.connected,false);assert.equal(count,1);
});
test('cache-only snapshot and observation calls remain authenticated and use no browser PAT',async()=>{
  const calls=[];const client=new ThinQSession({expectedMode:'fixture',fetcher:async(url,options)=>{calls.push([url,options]);return new Response(JSON.stringify(url.endsWith('bootstrap')?{key:'d'.repeat(64),mode:'fixture',expires_in_seconds:20}:JSON.parse(options.body).op==='observation'?{published:false,envelope:{data:{mode:'fixture'}}}:normal));}});
  await client.connect();await client.call({op:'snapshot'});await client.call({op:'observation'});
  assert.equal(calls.length,3);assert.equal(JSON.parse(calls[1][1].body).op,'snapshot');
  await assert.rejects(client.call({op:'snapshot',access_token:'FAKE-NOT-REAL'}));
});


test('actual HTML buttons open, close and reopen the ThinQ dialog without network actions',async()=>{
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
  const inline=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^import .*?;$/m,'');
  for(const runMode of ['fixture','live-read-only']) {
  const elements=new Map();
  class Element {
    open=false;disabled=false;style={};classList={toggle(){}};
    onclick=null;oncancel=null;onkeydown=null;opens=0;closes=0;
    addEventListener(){} setPointerCapture(){}
    showModal(){assert.equal(this.open,false);this.open=true;this.opens++;}
    close(){assert.equal(this.open,true);this.open=false;this.closes++;}
    click(){assert.equal(typeof this.onclick,'function');this.onclick();}
  }
  const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const requests=[];
  const context=vm.createContext({
    document:{documentElement:{dataset:{runMode}},getElementById:element,querySelectorAll:()=>[]},
    ThinQUiState,renderSnapshot:()=>{},performance:{now:()=>0},
    fetch:async(route,options)=>{requests.push([route,options]);return {ok:true,json:async()=>({pi4:''}),blob:async()=>({})};},
    URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},
    setInterval(){},setTimeout(){},window:{addEventListener(){}},
  });
  new vm.Script(inline).runInContext(context);
  const before=requests.length;
  const dialog=element('thinqDialog');
  assert.equal(dialog.open,false);
  element('thinq').click();assert.equal(dialog.open,true);
  element('closeInfo').click();assert.equal(dialog.open,false);
  element('thinq').click();assert.equal(dialog.open,true);
  element('closeInfo').click();assert.equal(dialog.open,false);
  assert.equal(dialog.opens,2);assert.equal(dialog.closes,2);
  assert.equal(requests.length,before); // Buttons do not authenticate, GET or POST.
  assert.equal(dialog.oncancel,null);assert.equal(dialog.onkeydown,null);
  assert.equal((inline.match(/\$\('thinq'\)\.onclick\s*=/g)||[]).length,1);
  assert.equal((inline.match(/\$\('closeInfo'\)\.onclick\s*=/g)||[]).length,1);
  if(runMode==='live-read-only'){
    for(const id of ['thinqLow','thinqAuto','thinqExecute','openApp','remoteTab'])assert.equal(element(id).disabled,true);
    for(const id of ['purifierAccept','purifierReject'])assert.equal(element(id).disabled,true);
    assert.equal(element('purifierFixtureScenarios').hidden,true);
    assert.equal(element('thinqRead').disabled,false);
    assert.equal(element('sensors').style.display,'block');
  }
  }
});

test('V1.1 HTML Fixture scenarios bind accept/reject to the displayed proposal and never execute Cloud control',async()=>{
  const html=await readFile(new URL('./index.html',import.meta.url),'utf8');
  const inline=html.match(/<script type="module">([\s\S]*?)<\/script>/)[1].replace(/^import .*?;$/m,'');
  const elements=new Map(),requests=[];
  const element=id=>{if(!elements.has(id))elements.set(id,{disabled:false,hidden:false,style:{},classList:{toggle(){}},addEventListener(){}});return elements.get(id);};
  const context=vm.createContext({
    document:{documentElement:{dataset:{runMode:'fixture'}},getElementById:element,querySelectorAll:()=>[]},
    ThinQUiState,renderSnapshot:()=>{},performance:{now:()=>0},
    fetch:async(route,options)=>{
      requests.push([route,options]);
      const body=options?.body?JSON.parse(options.body):{};
      let reply={pi4:''};
      if(route==='/api/thinq/policy-fixture')reply={fixture_timeline:true,present:true,fsm_state:'START',proposal:{proposal_id:body.scenario,operation:body.scenario==='pollution_auto'?'set_mode':'set_power',value:body.scenario==='presence_on'?'on':body.scenario==='absence_off'?'off':'auto'}};
      if(route==='/api/thinq/policy-response')reply={proposal:null,result:{status:body.decision==='accept'?'mock_succeeded':'rejected'}};
      return {ok:true,json:async()=>reply,blob:async()=>({})};
    },
    URL:{createObjectURL:()=> 'blob:fixture',revokeObjectURL(){}},setInterval(){},setTimeout(){},window:{addEventListener(){}},
  });
  new vm.Script(inline).runInContext(context);
  for(const [id,scenario] of [['purifierOnScenario','presence_on'],['purifierOffScenario','absence_off'],['purifierAirScenario','pollution_auto']]){
    await element(id).onclick();
    assert.equal(element('purifierAccept').disabled,false);
    assert.ok(element('purifierPolicyState').textContent.includes('Fixture'));
    await element('purifierAccept').onclick();
    const request=requests.findLast(([r])=>r==='/api/thinq/policy-response');
    assert.deepEqual(JSON.parse(request[1].body),{proposal_id:scenario,decision:'accept'});
    assert.equal(element('purifierAccept').disabled,true);
    assert.ok(element('purifierPolicyResult').textContent.includes('실제 전송 없음'));
  }
  await element('purifierOnScenario').onclick();await element('purifierReject').onclick();
  assert.ok(element('purifierPolicyResult').textContent.includes('거절'));
  assert.equal(requests.filter(([r])=>r==='/api/thinq/call').length,0);
});

test('Live read-only rejects Fixture/manual backends and blocks writes before HTTP',async()=>{
  for(const session of [{mode:'fixture',access:'read_only'},{mode:'live',access:'manual_control'}]){
    const client=new ThinQSession({expectedMode:'live',readOnly:true,fetcher:async()=>new Response(JSON.stringify({key:'a'.repeat(64),expires_in_seconds:30,...session}))});
    await assert.rejects(client.connect());assert.equal(client.connected,false);
  }
  let calls=0;
  const client=new ThinQSession({expectedMode:'live',readOnly:true,fetcher:async url=>{calls++;return new Response(JSON.stringify(url.endsWith('bootstrap')?{key:'a'.repeat(64),expires_in_seconds:30,mode:'live',access:'read_only'}:{...normal,mode:'live',post_enabled:true}));}});
  await client.connect();
  for(const op of ['prepare','execute','observation','physical_confirmation'])await assert.rejects(client.call({op}));
  assert.equal(calls,1);
  const read=await client.call({op:'read'});assert.equal(read.post_enabled,false);
  client.fetcher=async()=>new Response(JSON.stringify(normal));
  await assert.rejects(client.call({op:'read'}));
});

test('Live HTML remains usable without Pi4 and never constructs a Pi5 remote',async()=>{
  const port=19000+Math.floor(Math.random()*10000),base=`http://127.0.0.1:${port}`;
  const thinq={connected:false,connect:async()=>({mode:'live',access:'read_only'})};
  const server=createServer({port,executionMode:'live-read-only',token:'test-only',thinq,remote:{host:'laptop-read-only'},sensors:{host:'',port:18890}});
  await new Promise(resolve=>server.listen(port,'127.0.0.1',resolve));
  try{
    const page=await(await fetch(base)).text();assert.ok(page.includes('data-run-mode="live-read-only"'));assert.ok(page.includes('POST 금지'));
    assert.equal((await fetch(base+'/sensors')).status,503);
    const result=await fetch(base+'/api/thinq/connect',{method:'POST',headers:{Origin:base,'X-Deskmate-Key':'test-only','Content-Type':'application/json'},body:'{}'});
    assert.equal(result.status,200);assert.equal((await result.json()).mode,'live');
  }finally{server.closeAllConnections();await new Promise(resolve=>server.close(resolve));}
});
