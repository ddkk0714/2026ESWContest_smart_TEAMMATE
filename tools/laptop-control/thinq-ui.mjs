// Presentation/authorization state only. Never a Cloud or MQTT client.
export class ThinQUiState {
  constructor() { this.postEnabled=false; this.uncertain=false; }
  receive(reply={}) {
    if(reply.status==='outcome_unconfirmed') this.uncertain=true;
    if(Object.hasOwn(reply,'post_enabled')) this.postEnabled=reply.post_enabled===true;
    if(reply.status==='session_revoked') {this.postEnabled=false;this.uncertain=false;}
    if(reply.status==='reconciled') this.uncertain=false;   // a reviewed fresh GET, never a resend
  }
  transportFailure(duringWrite=false) { if(duringWrite) this.uncertain=true; }
  canPrepare() {return this.postEnabled&&!this.uncertain;}
}
const labels={pm1:'PM1',pm1_level:'PM1 API 등급',pm2:'PM2 (API 필드명)',pm2_level:'PM2 API 등급',
  pm10:'PM10',pm10_level:'PM10 API 등급',odor:'냄새',odor_level:'냄새 API 등급',
  total_pollution:'종합 오염도',total_pollution_level:'종합 API 등급',filter_remain_percent:'필터 잔여율',
  power:'전원',monitoring_mode:'센서 모니터링 모드',job_mode:'운전 모드',wind_strength:'풍량'};
export function snapshotRows(snapshot, elapsedSec=0) {
  const s=snapshot??{}, fields=s.fields??{};
  const ttl=Number(s.expires_at)-Number(s.received_at);
  const stale=s.receipt_stale!==false||s.received_at==null||!Number.isFinite(ttl)||ttl<=0||
    !Number.isFinite(s.api_age_sec)||s.api_age_sec+Math.max(0,elapsedSec)>=ttl;
  const usable=!stale&&s.api_status==='ok';
  const rows=[['출처',s.mode==='fixture'?'Fixture 테스트 데이터 · 실제 가전값 아님':'LG ThinQ Cloud · ESP32와 별도 출처'],
    ['API',s.api_status??'아직 조회하지 않음'],['데이터',stale?'stale / 현재값 표시 안 함':'API 수신 기준 유효'],
    ['기기 연결',s.connection==='offline'?'오프라인 보고됨':'미확인 (unknown)'],
    ['마지막 성공 GET (UTC)',Number.isFinite(s.last_successful_received_at??s.received_at)?new Date((s.last_successful_received_at??s.received_at)*1000).toISOString():'--']];
  for(const [name,label] of Object.entries(labels)) {
    const f=fields[name]; const valid=usable&&f?.valid===true&&f.status==='ok';
    rows.push([label,valid?String(f.value)+(name==='filter_remain_percent'?'%':''):'--']);
  }
  rows.push(['측정 의미','PM 단위·PM2의 정확한 의미·실제 센서 측정 시각 미확인']);
  return rows;
}
export function renderSnapshot(document,id,snapshot,elapsedSec=0) {
  const root=document.getElementById(id);if(!root)return;
  const dl=document.createElement('dl');
  for(const [label,value] of snapshotRows(snapshot,elapsedSec)) {
    const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;dl.append(dt,dd);
  }
  root.replaceChildren(dl);
}
