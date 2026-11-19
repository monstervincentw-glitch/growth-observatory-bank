import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {setTimeout} from 'node:timers/promises';
const [market,prior,output]=process.argv.slice(2);
const history=[];
for(let i=0;i<2;i++){
  const code=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,['engine/update.mjs',market,prior,output],{stdio:'inherit',env:process.env});child.on('error',reject);child.on('exit',resolve);});
  if(code!==0)throw Error('更新程式異常結束，未發布未驗證資料');
  const release=JSON.parse(await readFile(output,'utf8'));
  history.push(structuredClone(release.attempt));
  release.attempt.retryHistory=history.map(({retryHistory,...entry})=>entry);
  release.attempts[market]=release.attempt;
  await writeFile(output,JSON.stringify(release)+'\n');
  if(!['failed','quotes_only'].includes(release.attempt.status))break;
  // Only one retry for transient transport/5xx errors. Validation and throttling
  // wait for the next scheduled slot rather than refetching the full universe.
  if(release.attempt.rateLimitedUntil || release.attempt.errors?.some(e=>[429,403].includes(e.code)))break;
  if(!release.attempt.errors?.some(e=>e.code==='TIMEOUT'||e.code==='FETCH_FAILED'||(Number(e.code)>=500&&Number(e.code)<=599)))break;
  if(i<1)await setTimeout(120000);
}
