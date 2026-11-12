const BOOK = '1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0';
const SOURCE = 'https://raw.githubusercontent.com/monstervincentw-glitch/growth-observatory-bank/research-data/release.json';
const SNAPSHOT_ID = 729112601;
const RUNS_ID = 729112602;
const HEADER = ['batch_id','part','parts','sha256','payload_json'];

function installBankSync() {
  if (!ScriptApp.getProjectTriggers().some(trigger => trigger.getHandlerFunction() === 'syncBankResearch')) {
    ScriptApp.newTrigger('syncBankResearch').timeBased().everyHours(1).create();
  }
  syncBankResearch();
}

function syncBankResearch() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(20000)) return;
  try {
    const response = UrlFetchApp.fetch(SOURCE, {muteHttpExceptions:true});
    if (response.getResponseCode() !== 200) throw new Error('研究排程發布來源 HTTP '+response.getResponseCode());
    const release = JSON.parse(response.getContentText());
    const attempt = release.attempt;
    if (!attempt) return;
    const rows = release.rows;
    const verified = verifyEnvelope_(rows);
    const ranges = Sheets.Spreadsheets.Values.batchGet(BOOK,{ranges:['bank_snapshot!A2:E300','bank_runs!A2:A1000'],valueRenderOption:'UNFORMATTED_VALUE'}).valueRanges;
    const existing = ranges[0].values || [];
    const recorded = ranges[1].values || [];
    if (recorded.some(row => row[0] === attempt.at)) return;
    const requests = [];
    if (attempt.status === 'published' && existing[0]?.[0] !== rows[1][0]) {
      const previous = verifyEnvelope_([HEADER,...existing]);
      for (const market of ['US','TW']) {
        if (verified.markets[market].macro.marketAsOf < previous.markets[market].macro.marketAsOf)
          throw new Error('收盤日期倒退，保留上一次成功快照');
      }
      requests.push({updateCells:{range:{sheetId:SNAPSHOT_ID,startRowIndex:0,endRowIndex:300,startColumnIndex:0,endColumnIndex:5},
        rows:rows.map(cells_),fields:'userEnteredValue'}});
    }
    requests.push({appendCells:{sheetId:RUNS_ID,rows:[cells_([attempt.at,attempt.market,attempt.status,attempt.batch,
      attempt.us_close,attempt.tw_close,attempt.updated,attempt.failed,attempt.detail])],fields:'userEnteredValue'}});
    Sheets.Spreadsheets.batchUpdate({requests},BOOK);
    console.log(JSON.stringify({at:attempt.at,market:attempt.market,status:attempt.status,batch:attempt.batch}));
  } catch (error) {
    console.error(String(error.message||error));
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function cells_(row) {
  return {values:row.map(value=>({userEnteredValue:typeof value==='number'?{numberValue:value}:{stringValue:String(value??'')}}))};
}

function verifyEnvelope_(rows) {
  if (JSON.stringify(rows[0])!==JSON.stringify(HEADER)||rows.length<2||rows.length>300) throw new Error('資料封套錯誤');
  const [batch,,count,hash] = rows[1];
  if (!/^v29-[a-f0-9]{16}$/.test(batch)||count!==rows.length-1||!/^[a-f0-9]{64}$/.test(hash))
    throw new Error('批次識別或資料筆數錯誤');
  const fragments=rows.slice(1).map(([id,index,parts,sha,data],offset)=>{
    if(id!==batch||index!==offset+1||parts!==count||sha!==hash||typeof data!=='string'||data.length>18000)
      throw new Error('混合批次或缺漏分段');
    return data;
  });
  const raw=Utilities.ungzip(Utilities.newBlob(Utilities.base64Decode(fragments.join('')),'application/x-gzip')).getDataAsString('UTF-8');
  const actual=Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256,raw,Utilities.Charset.UTF_8)
    .map(byte=>(byte&255).toString(16).padStart(2,'0')).join('');
  if(actual!==hash)throw new Error('SHA-256 驗證失敗');
  const value=JSON.parse(raw);
  const keys=['generatedAt','historyThrough','jobs','markets','qa','schema','sourceVersion'];
  if(JSON.stringify(Object.keys(value).sort())!==JSON.stringify(keys)||value.schema!==1||
     value.markets.US.stocks.length!==89||value.markets.TW.stocks.length!==80)
    throw new Error('公開研究欄位或研究池不符');
  return value;
}
