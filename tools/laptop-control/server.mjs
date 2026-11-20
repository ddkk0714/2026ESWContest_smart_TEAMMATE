import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { spawn, execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { randomBytes, timingSafeEqual } from 'node:crypto';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const run = promisify(execFile);
const here = path.dirname(fileURLToPath(import.meta.url));
const app = 'com.atlas.app.deskmate_ui_v27';
const remoteInput = '/tmp/deskmate-laptop-input';
const remoteFrame = '/tmp/deskmate-laptop-screen.png';
const PNG = Buffer.from([137,80,78,71,13,10,26,10]);

export function coordinates(body) {
  const keys = ['x1','y1','x2','y2'];
  if (Object.keys(body).some(k => !keys.includes(k))) throw new Error('허용되지 않은 입력');
  const values = keys.map((key, i) => {
    const value = body[key];
    if (!Number.isInteger(value) || value < 0 || value > (i % 2 ? 599 : 1023)) {
      throw new Error('화면 좌표가 범위를 벗어났어요');
    }
    return value;
  });
  return values;
}

export class Pi5Remote {
  constructor({host, knownHosts, input, appId=app, runner = run}) {
    if (!/^[\w:%.\-]+$/.test(host)) throw new Error('Pi5 주소 형식 오류');
    if (!/^com\.atlas\.app\.deskmate_ui_v\d+$/.test(appId)) throw new Error('앱 ID 형식 오류');
    this.appId=appId;
    this.host = host; this.knownHosts = knownHosts; this.input = input; this.runner = runner;
    this.tail = Promise.resolve(); this.cached = null; this.lastFrame = 0;
    this.prepared = false;
  }
  queue(job) {
    const result = this.tail.then(job);
    this.tail = result.catch(() => {});
    return result;
  }
  options() {
    return [...(this.host.includes(':') ? ['-6'] : []), '-o','BatchMode=yes',
      '-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5',
      '-o',`UserKnownHostsFile=${this.knownHosts}`];
  }
  async ssh(command) {
    return this.runner('ssh', [...this.options(), `root@${this.host}`, command],
      {encoding: null, timeout: 12000, maxBuffer: 6*1024*1024, windowsHide: true});
  }
  async prepare() {
    const destination = `root@${this.host.includes(':') ? `[${this.host}]` : this.host}:${remoteInput}`;
    await this.runner('scp', ['-O', ...this.options(), this.input, destination],
      {timeout:15000, windowsHide:true});
    await this.ssh(`chmod 700 ${remoteInput}`);
    this.prepared = true;
  }
  frame() {
    return this.queue(async () => {
      if (this.cached && Date.now()-this.lastFrame < 500) return this.cached;
      const {stdout} = await this.ssh(`abusctl call com.atlas.WindowManager1 CaptureScreen ${remoteFrame} >/dev/null && cat ${remoteFrame}`);
      const image = Buffer.from(stdout);
      if (!image.subarray(0,8).equals(PNG)) throw new Error('Pi5 화면을 받지 못했어요');
      this.cached=image; this.lastFrame=Date.now(); return image;
    });
  }
  touch(body) {
    const values = coordinates(body);
    return this.queue(async () => {
      if (!this.prepared) await this.prepare();
      await this.ssh(`${remoteInput} ${values.join(' ')}`);
      this.cached=null;
    });
  }
  openApp() { return this.queue(() => this.ssh(`abusctl call com.atlas.AppManager1 Start ${this.appId}`)); }
  async close() { await this.tail; await this.ssh(`rm -f ${remoteInput} ${remoteFrame}`); }
}

export class SensorViewer {
  constructor({docker='docker', port=8790, root=path.resolve(here,'../..')}={}) {
    this.docker=docker; this.port=port; this.root=root; this.host=''; this.child=null;
    this.name=`deskmate-laptop-sensors-${process.pid}`;
  }
  async start(host) {
    if (!/^[a-zA-Z0-9][a-zA-Z0-9.\-]{0,252}$/.test(host)) throw new Error('Pi4 주소를 확인해 주세요');
    await this.stop(); this.host=host;
    const args=['run','--rm','--name',this.name,'-p',`127.0.0.1:${this.port}:8790`,
      '-v',`${this.root}:/workspace:ro`,'-w','/workspace/hub/atlas-tof/pc/devview',
      'deskmate-laptop-sensors:local','python','-u','devview.py','--bind','0.0.0.0','--pi4',host];
    this.child=spawn(this.docker,args,{windowsHide:true,stdio:['ignore','ignore','pipe']});
    this.child.stderr.on('data',()=>{});
    this.child.on('error',()=>{ this.host=''; });
    this.child.on('exit',()=>{ this.child=null; });
  }
  async stop() {
    if (!this.child) return;
    await run(this.docker,['stop','--time','2',this.name],{windowsHide:true,timeout:8000}).catch(()=>{});
    this.child=null; this.host='';
  }
}

export class ThinQSession {
  constructor({fetcher=fetch,clock=Date.now,expectedMode=null,baseUrl="http://127.0.0.1:8792",readOnly=false,requiredAccess=null}={}) {this.fetcher=fetcher;this.clock=clock;this.key=null;this.until=0;this.expectedMode=expectedMode;this.readOnly=readOnly;this.requiredAccess=requiredAccess;this.mode=null;this.access=null;if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(baseUrl))throw new Error("인증 프로세스는 localhost만 허용해요.");this.baseUrl=baseUrl;}
  get connected() {return this.key!==null&&this.clock()<this.until;}
  async connect() {
      if(this.connected) return {status:'session_connected',mode:this.mode,access:this.access,expires_in_seconds:Math.floor((this.until-this.clock())/1000)};
    const response=await this.fetcher(this.baseUrl+'/bootstrap',{
      headers:{'X-Deskmate-Bootstrap':'1'},signal:AbortSignal.timeout(5000)});
    if(!response.ok) throw new Error('ThinQ 인증 터미널을 새로 열어 세션을 준비해 주세요.');
    const session=await response.json();
    if(!/^[0-9a-f]{64}$/.test(session.key)||!Number.isInteger(session.expires_in_seconds)||
       session.expires_in_seconds<1||session.expires_in_seconds>1200) throw new Error('ThinQ 세션 응답이 올바르지 않아요.');
    if(this.expectedMode && session.mode!==this.expectedMode) throw new Error('인증 프로세스가 화면의 Fixture/Live 모드와 달라요.');
    if(this.readOnly&&session.access!=='read_only') throw new Error('읽기 전용 인증 프로세스만 연결할 수 있어요.');
    if(this.requiredAccess&&session.access!==this.requiredAccess) throw new Error('별도의 수동 제어 인증 프로세스가 필요해요.');
    this.mode=session.mode??'live';
    this.access=session.access??null;
    this.key=session.key;this.until=this.clock()+session.expires_in_seconds*1000;
    return {status:'session_connected',mode:this.mode,access:this.access,expires_in_seconds:session.expires_in_seconds};
  }
  async call(body) {
    if(!this.connected) throw new Error('ThinQ 인증 세션을 먼저 연결해 주세요.');
    const allowed=['op','value','nonce','approved','physical_online_confirmed','no_external_change'];
    if(body?.op==='policy')allowed.push('phase');
    if(body?.op==='policy_fixture')allowed.push('scenario');
    if(body?.op==='policy_respond')allowed.push('proposal_id','decision');
    if(!body||Array.isArray(body)||Object.keys(body).some(k=>!allowed.includes(k))||
      !['read','snapshot','observation','prepare','execute','physical_confirmation','revoke','policy','policy_fixture','policy_respond'].includes(body.op)) throw new Error('허용되지 않은 ThinQ 요청이에요.');
    if(this.readOnly&&!['read','snapshot','revoke','policy'].includes(body.op)) throw new Error('Live 읽기 전용: 제어·export 요청 금지');
    if(this.requiredAccess==='manual_control'&&!['read','snapshot','prepare','execute','physical_confirmation','revoke','policy'].includes(body.op)) throw new Error('수동 시험에서 허용되지 않은 요청이에요.');
    const response=await this.fetcher(this.baseUrl+'/call',{method:'POST',
      headers:{'Content-Type':'application/json','X-Deskmate-Session':this.key},body:JSON.stringify(body),
      signal:AbortSignal.timeout(90000)});
    if(response.status===401){this.key=null;this.until=0;throw new Error('ThinQ 세션이 만료됐어요. 인증 터미널을 다시 열어 주세요.');}
    if(!response.ok) throw new Error('ThinQ 요청을 처리하지 못했어요. 자동 재전송하지 않아요.');
    const reply=await response.json();
    const replyMode=body.op==='observation'?reply.envelope?.data?.mode:reply.mode;
    if(this.expectedMode&&body.op!=='revoke'&&replyMode!==this.expectedMode) throw new Error('인증 응답 출처가 실행 모드와 달라요.');
    if(this.readOnly)reply.post_enabled=false;
    if(body.op==='revoke'){this.key=null;this.until=0;}
    return reply;
  }
}

async function jsonBody(req) {
  if (!req.headers['content-type']?.startsWith('application/json')) throw new Error('JSON 요청만 허용해요');
  let text=''; for await(const chunk of req) { text+=chunk; if(text.length>2048) throw new Error('요청이 너무 커요'); }
  return JSON.parse(text || '{}');
}

export function createServer({remote, sensors, thinq=new ThinQSession(), port=8791, executionMode='legacy', token=randomBytes(24).toString('hex')}) {
  const origin=`http://127.0.0.1:${port}`;
  let busy=false;
  return http.createServer(async(req,res)=>{
    const send=(code,body,type='application/json; charset=utf-8')=>{
      res.writeHead(code, {'Content-Type':type,'Cache-Control':'no-store',
        'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
        'Cross-Origin-Resource-Policy':'same-origin',
        'Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; frame-src 'self'; frame-ancestors 'self'"});
      res.end(Buffer.isBuffer(body)||typeof body==='string'?body:JSON.stringify(body));
    };
    try {
      if(req.headers.host!==`127.0.0.1:${port}`) return send(403,{error:'로컬 주소만 허용해요'});
      const url=new URL(req.url,origin);
      if(req.method==='GET' && url.pathname==='/') {
        const html=(await readFile(path.join(here,'index.html'),'utf8')).replace('__SESSION_TOKEN__',token).replace('__RUN_MODE__',executionMode).replace('__EXECUTION_MODE__',executionMode==='live-manual'?'Live Manual · 실제 공기청정기 LOW/AUTO · 각 동작마다 별도 승인 필요 · 자동 제어 OFF':executionMode==='live-read-only'?'Live Read-only · Pi4 센서는 연결 상태 별도 확인 · ThinQ Cloud 수동 GET 전용 · POST 금지':remote.host==='fixture'?(sensors.sourceMode==='live'?'실제 Pi4 센서 · 읽기 전용 / ThinQ Fixture 테스트 · Cloud 연결 없음':'Fixture 테스트 · 실제 보드·Cloud 연결 없음'):'실기 경로 · GET/POST는 별도 승인 필요');
        return send(200,html,'text/html; charset=utf-8');
      }
      if(req.method==='GET' && url.pathname==='/api/status') {
          return send(200,{pi5:remote.host,pi4:sensors.host,app:remote.appId??app,thinq:thinq.connected?'session_connected':'credential_session_required'});
      }
      if(req.method==='GET' && url.pathname==='/thinq-ui.mjs') return send(200,await readFile(path.join(here,'thinq-ui.mjs')),'text/javascript; charset=utf-8');
      if(req.method==='GET' && url.pathname==='/api/thinq/policy') {
        if(!thinq.connected)return send(200,{execution:'mock_only',reason:'인증 세션 연결 전',proposal:null,post_enabled:false});
        let phase=null;
        if(sensors.host){try{
          const response=await fetch(`http://127.0.0.1:${sensors.port}/data`,{signal:AbortSignal.timeout(4000)});
          if(response.ok)phase=(await response.json()).thinq_phase??null;
        }catch{ /* Missing live data fails closed in the policy. */ }}
        return send(200,await thinq.call({op:'policy',phase}));
      }
      if(req.method==='GET' && url.pathname==='/api/thinq/commands') {
        if(!sensors.host) return send(200,{mode:'observe_only',post_enabled:false,commands:[]});
        const response=await fetch(`http://127.0.0.1:${sensors.port}/data`,{signal:AbortSignal.timeout(4000)});
        if(!response.ok) return send(503,{error:'센서 모니터 연결을 확인해 주세요.'});
        const data=await response.json();
        return send(200,data.thinq_commands??{mode:'unavailable',post_enabled:false,commands:[]});
      }
      if(req.method==='GET' && url.pathname==='/api/frame') return send(200,await remote.frame(),'image/png');
      if(req.method==='GET' && ['/sensors','/data'].includes(url.pathname)) {
        if(!sensors.host) return send(503,'먼저 Pi4 주소를 입력해 주세요.','text/plain; charset=utf-8');
        const response=await fetch(`http://127.0.0.1:${sensors.port}${url.pathname==='/sensors'?'/'+url.search:'/data'}`,{signal:AbortSignal.timeout(4000)});
        let content=Buffer.from(await response.arrayBuffer());
        if(url.pathname==='/sensors') content=Buffer.from(content.toString('utf8').replaceAll('"X-Devview": "1"', '"X-Devview": "1", "X-Deskmate-Key": "'+token+'"'));
        return send(response.status,content,response.headers.get('content-type'));
      }
      if(req.method!=='POST') return send(404,{error:'없는 경로예요'});
      const key=Buffer.from(req.headers['x-deskmate-key']??'');
      const expected=Buffer.from(token);
      if(req.headers.origin!==origin || key.length!==expected.length || !timingSafeEqual(key,expected)) {
        return send(403,{error:'이 노트북 화면에서만 조작할 수 있어요'});
      }
      if(busy) return send(409,{error:'이전 조작이 끝날 때까지 기다려 주세요'});
      busy=true;
      try {
        const body=await jsonBody(req);
        if(['/api/profile','/api/feedback'].includes(url.pathname)) {
          if(!sensors.host) return send(503,{error:'Pi4를 먼저 연결해 주세요.'});
          const response=await fetch(`http://127.0.0.1:${sensors.port}${url.pathname}`,{method:'POST',headers:{'Content-Type':'application/json','X-Devview':'1'},body:JSON.stringify(body),signal:AbortSignal.timeout(5000)});
          return send(response.status,Buffer.from(await response.arrayBuffer()),'application/json; charset=utf-8');
        }
        else if(url.pathname==='/api/touch') await remote.touch(body);
        else if(url.pathname==='/api/open') await remote.openApp();
        else if(url.pathname==='/api/pi4') await sensors.start(body.host);
        else if(url.pathname==='/api/thinq/connect') return send(200,await thinq.connect());
        else if(url.pathname==='/api/thinq/policy-fixture') {
          if(executionMode!=='fixture')return send(403,{error:'Fixture에서만 Mock 시나리오를 선택할 수 있어요.'});
          if(Object.keys(body).some(k=>k!=='scenario'))return send(400,{error:'잘못된 시나리오 요청'});
          return send(200,await thinq.call({op:'policy_fixture',scenario:body.scenario}));
        }
        else if(url.pathname==='/api/thinq/policy-response') {
          if(executionMode!=='fixture')return send(403,{error:'현재 정책 수락·거절은 Fixture Mock 전용이에요.'});
          if(Object.keys(body).some(k=>!['proposal_id','decision'].includes(k)))return send(400,{error:'잘못된 응답'});
          return send(200,await thinq.call({op:'policy_respond',...body}));
        }
        else if(url.pathname==='/api/thinq/call') {
          if(String(body.op).startsWith('policy'))return send(403,{error:'전용 정책 경로를 사용해 주세요.'});
          return send(200,await thinq.call(body));
        }
        else return send(404,{error:'없는 경로예요'});
        return send(200,{ok:true});
      } finally { busy=false; }
    } catch(error) {
      // Never send arbitrary SSH stderr or process arguments back to the browser.
      const message=error.message?.startsWith('Command failed') ? 'Pi5 연결을 확인해 주세요.' : error.message;
      send(400,{error:message ?? '요청을 처리하지 못했어요'});
    }
  });
}

export async function main(args=process.argv.slice(2)) {
  const options={};
  for(let i=0;i<args.length;i+=2) options[args[i].replace(/^--/,'')]=args[i+1];
  const port=Number(options.port??8791);
  if(!Number.isInteger(port)||port<1024||port>65535) throw new Error('포트 범위 오류');
  const fixture=options.fixture==='true'||options.mode==='fixture';
  const liveReadOnly=options.mode==='live-read-only';
  const liveManual=options.mode==='live-manual';
  if(options.mode&&!['fixture','live-read-only','live-manual'].includes(options.mode))throw new Error('실행 모드 오류');
  if(fixture&&(liveReadOnly||liveManual))throw new Error('Fixture와 Live 모드는 동시에 사용할 수 없어요');
  const localOnly=fixture||liveReadOnly||liveManual;
  const sensorSource=liveReadOnly||liveManual?'live':(options['sensor-source']??'fixture');
  if(!['fixture','live'].includes(sensorSource)) throw new Error('--sensor-source는 fixture 또는 live만 허용해요');
  if(options.pi4&&!/^[a-zA-Z0-9][a-zA-Z0-9.\-]{0,252}$/.test(options.pi4))throw new Error('Pi4 주소 형식 오류');
  if(fixture&&sensorSource==='live'&&!options.pi4)throw new Error('실제 센서 표시에는 --pi4 주소가 필요해요');
  if(!localOnly&&(!options.pi5||!options['known-hosts'])) throw new Error('--pi5, --known-hosts가 필요해요');
  const sensorPort=Number(options['sensor-port']??options['sensor-fixture-port']??(liveReadOnly||liveManual?18890:8790));
  const thinqPort=Number(options['thinq-port']??(liveManual?19092:liveReadOnly?18892:8792));
  if(![sensorPort,thinqPort].every(p=>Number.isInteger(p)&&p>=1024&&p<=65535))throw new Error('백엔드 포트 범위 오류');
  const remote=localOnly ? {host:fixture?'fixture':'laptop-read-only', appId:app, prepare:async()=>{}, frame:async()=>Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aH1cAAAAASUVORK5CYII=","base64"), touch:async()=>{throw new Error('이 모드에서는 Pi5 조작 없음');},openApp:async()=>{throw new Error('이 모드에서는 Pi5 앱 실행 없음');},close:async()=>{}} : new Pi5Remote({host:options.pi5,knownHosts:options['known-hosts'],appId:options.app??app,input:options.input??path.join(here,'build','ui-input')});
  await remote.prepare().catch(()=>console.log('Pi5 연결 대기 중. 유선랜을 연결하면 화면 수신과 조작을 다시 시도합니다.'));
  const sensors=localOnly ? {host:liveReadOnly||liveManual?(options.pi4??''):(sensorSource==='live'?options.pi4:'fixture'),sourceMode:sensorSource,port:sensorPort,start:async()=>{throw new Error('읽기 전용 센서 주소는 실행 터미널에서 변경해 주세요');},stop:async()=>{}} : new SensorViewer({docker:options.docker??'docker'});
  if(options.pi4&&!localOnly) await sensors.start(options.pi4);
  const server=createServer({remote,sensors,port,executionMode:liveManual?'live-manual':liveReadOnly?'live-read-only':fixture?'fixture':'legacy',thinq:new ThinQSession({expectedMode:fixture?'fixture':liveReadOnly||liveManual?'live':null,readOnly:liveReadOnly,requiredAccess:liveManual?'manual_control':null,baseUrl:localOnly?`http://127.0.0.1:${thinqPort}`:'http://127.0.0.1:8792'})});
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
  console.log(`DESKMATE 노트북 제어: http://127.0.0.1:${port}/`);
  let closing=false;
  const close=async()=>{if(closing)return;closing=true;server.close();await Promise.allSettled([remote.close(),sensors.stop()]);process.exit(0);};
  process.on('SIGINT',close); process.on('SIGTERM',close);
  return server;
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href) {
  main().catch(error=>{console.error(error.message);process.exitCode=1;});
}
