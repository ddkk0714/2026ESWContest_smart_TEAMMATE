import http from 'node:http';
import {pathToFileURL} from 'node:url';

export function redirectServer() {
  return http.createServer((req,res)=>{
    if(req.method==='GET'&&(req.url==='/'||req.url?.startsWith('/?'))){
      res.writeHead(302,{Location:'http://127.0.0.1:19891/','Cache-Control':'no-store'});
      return res.end();
    }
    res.writeHead(410,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});
    res.end(JSON.stringify({error:'이전 시험 주소입니다. http://127.0.0.1:19891/ 를 사용하세요.'}));
  });
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  const port=Number(process.argv[2]);
  if(![19591,19691].includes(port))throw Error('이 작업의 이전 주소만 정리할 수 있습니다.');
  redirectServer().listen(port,'127.0.0.1');
}
