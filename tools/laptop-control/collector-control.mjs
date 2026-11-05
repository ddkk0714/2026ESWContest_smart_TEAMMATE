import {execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {fileURLToPath} from 'node:url';

const execute=promisify(execFile);
export class CollectorControl {
  constructor({run=execute,platform=process.platform,distro=process.env.WSL_DISTRO_NAME??'Ubuntu-22.04'}={}) {
    this.run=run;this.platform=platform;this.distro=distro;this.pending=false;this.cached=null;this.cachedAt=0;this.reading=null;
  }
  async request(action,enabled) {
    const local=fileURLToPath(new URL('../../collector/control.ps1',import.meta.url));
    const script=this.platform==='win32'?local:`\\\\wsl.localhost\\${this.distro}${local.replaceAll('/','\\')}`;
    const exe=this.platform==='win32'?'powershell.exe':'/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe';
    try {
      const args=['-NoProfile','-NonInteractive','-File',script,'-Action',action];
      if(action==='Set')args.push('-Enabled',String(enabled));
      const {stdout}=await this.run(exe,args,{timeout:12000,maxBuffer:8192});
      const s=JSON.parse(stdout.replace(/^\uFEFF/,''));
      return {connected:s.connected===true,keyboard_enabled:s.connected===true&&typeof s.keyboard_enabled==='boolean'?s.keyboard_enabled:null,
        mouse_enabled:s.connected===true&&s.mouse_enabled===true,mqtt_publish_enabled:s.mqtt_publish_enabled===true};
    }catch {
      if(action==='Set')throw Error('수집기 변경을 확인하지 못했습니다. 상태를 다시 확인하세요. 자동 재시도하지 않습니다.');
      return {connected:false,keyboard_enabled:null,mouse_enabled:null,mqtt_publish_enabled:null};
    }
  }
  async status() {
    if(this.cached&&Date.now()-this.cachedAt<1500)return this.cached;
    if(!this.reading)this.reading=this.request('Status').then(s=>{this.cached=s;this.cachedAt=Date.now();return s;}).finally(()=>{this.reading=null;});
    return this.reading;
  }
  async set(enabled) {
    if(typeof enabled!=='boolean')throw Error('수집 여부는 boolean이어야 합니다.');
    if(this.pending)throw Error('수집기 변경 중입니다.');
    this.pending=true;
    try {const s=await this.request('Set',enabled);if(!s.connected||s.keyboard_enabled!==enabled)throw Error('수집기 응답 불일치');this.cached=s;this.cachedAt=Date.now();return s;}
    finally {this.pending=false;}
  }
}
