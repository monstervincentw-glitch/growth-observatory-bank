import {spawn} from 'node:child_process';
import {readFile,writeFile} from 'node:fs/promises';
import {setTimeout} from 'node:timers/promises';
const [market,prior,output]=process.argv.slice(2);
const history=[];
for(let i=0;i<3;i++){
  const code=await new Promise((resolve,reject)=>{const child=spawn(process.execPath,['engine/update.mjs',market,prior,output],{stdio:'inherit',env:process.env});child.on('error',reject);child.on('exit',resolve);});
  if(code!==0)throw Error('更新程式異常結束，未發布未驗證資料');
  const release=JSON.parse(await readFile(output,'utf8'));
  history.push(structuredClone(release.attempt));
  release.attempt.retryHistory=history.map(({retryHistory,...entry})=>entry);
  release.attempts[market]=release.attempt;
  await writeFile(output,JSON.stringify(release)+'\n');
  if(!['failed','quotes_only'].includes(release.attempt.status))break;
  if(i<2)await setTimeout(45000*(i+1));
}
