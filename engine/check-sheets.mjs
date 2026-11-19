import {readFile} from 'node:fs/promises';
import {setTimeout} from 'node:timers/promises';
const release=JSON.parse(await readFile(process.argv[2],'utf8'));
const url=(await readFile(new URL('../data/sheets-url.txt',import.meta.url),'utf8')).trim();
const [batch,,,checksum]=release.rows[1];
let issue='Sheets 尚未同步';
for(let i=0;i<8;i++){
 try{
  const response=await fetch(url+'?v='+Date.now(),{signal:AbortSignal.timeout(25000)});
  if(!response.ok)throw Error('Sheets reader HTTP '+response.status);
  const csv=await response.text();
  if(csv.includes(batch)&&csv.includes(checksum)){
   console.log(JSON.stringify({status:'SHEETS_VERIFIED',batch,at:new Date().toISOString()}));process.exit(0);
  }
  issue='GitHub 已發布，但 Sheets 批次或 SHA-256 尚未吻合：'+batch;
 }catch(error){issue=String(error.message||error);}
 if(i<7)await setTimeout(60000);
}
console.error(issue);process.exitCode=1;
