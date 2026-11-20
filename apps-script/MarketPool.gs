/** All 169 research symbols: Google Sheets acquisition, official OTC fallback.
 * Public endpoint ONLY exports public market prices. No portfolio/book access.
 * Research financials, ranks, signals and macro dates are never rewritten here.
 */
const MP_BOOK='1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0';
const MP_POOL={"US":[{"symbol":"AAOI","exchange":"NASDAQ","inactive":false},{"symbol":"AAPL","exchange":"NASDAQ","inactive":false},{"symbol":"ACLS","exchange":"NASDAQ","inactive":false},{"symbol":"ADBE","exchange":"NASDAQ","inactive":false},{"symbol":"ADI","exchange":"NASDAQ","inactive":false},{"symbol":"ADSK","exchange":"NASDAQ","inactive":false},{"symbol":"AEIS","exchange":"NASDAQ","inactive":false},{"symbol":"AKAM","exchange":"NASDAQ","inactive":false},{"symbol":"ALGM","exchange":"NASDAQ","inactive":false},{"symbol":"AMAT","exchange":"NASDAQ","inactive":false},{"symbol":"AMD","exchange":"NASDAQ","inactive":false},{"symbol":"AMKR","exchange":"NASDAQ","inactive":false},{"symbol":"AMZN","exchange":"NASDAQ","inactive":false},{"symbol":"APP","exchange":"NASDAQ","inactive":false},{"symbol":"AVGO","exchange":"NASDAQ","inactive":false},{"symbol":"BKNG","exchange":"NASDAQ","inactive":false},{"symbol":"CDNS","exchange":"NASDAQ","inactive":false},{"symbol":"COHU","exchange":"NASDAQ","inactive":false},{"symbol":"CRUS","exchange":"NASDAQ","inactive":false},{"symbol":"CRWD","exchange":"NASDAQ","inactive":false},{"symbol":"CSCO","exchange":"NASDAQ","inactive":false},{"symbol":"DBX","exchange":"NASDAQ","inactive":false},{"symbol":"DDOG","exchange":"NASDAQ","inactive":false},{"symbol":"DIOD","exchange":"NASDAQ","inactive":false},{"symbol":"DOCU","exchange":"NASDAQ","inactive":false},{"symbol":"DUOL","exchange":"NASDAQ","inactive":false},{"symbol":"EBAY","exchange":"NASDAQ","inactive":false},{"symbol":"ENPH","exchange":"NASDAQ","inactive":false},{"symbol":"ETSY","exchange":"NYSE","inactive":false},{"symbol":"FIVN","exchange":"NASDAQ","inactive":false},{"symbol":"FORM","exchange":"NASDAQ","inactive":false},{"symbol":"FSLR","exchange":"NASDAQ","inactive":false},{"symbol":"FTNT","exchange":"NASDAQ","inactive":false},{"symbol":"GEN","exchange":"NASDAQ","inactive":false},{"symbol":"GOOGL","exchange":"NASDAQ","inactive":false},{"symbol":"GTM","exchange":"NASDAQ","inactive":false},{"symbol":"ICHR","exchange":"NASDAQ","inactive":false},{"symbol":"INTC","exchange":"NASDAQ","inactive":false},{"symbol":"INTU","exchange":"NASDAQ","inactive":false},{"symbol":"KLAC","exchange":"NASDAQ","inactive":false},{"symbol":"LCID","exchange":"NASDAQ","inactive":false},{"symbol":"LITE","exchange":"NASDAQ","inactive":false},{"symbol":"LRCX","exchange":"NASDAQ","inactive":false},{"symbol":"LSCC","exchange":"NASDAQ","inactive":false},{"symbol":"MCHP","exchange":"NASDAQ","inactive":false},{"symbol":"MDB","exchange":"NASDAQ","inactive":false},{"symbol":"META","exchange":"NASDAQ","inactive":false},{"symbol":"MGNI","exchange":"NASDAQ","inactive":false},{"symbol":"MKSI","exchange":"NASDAQ","inactive":false},{"symbol":"MPWR","exchange":"NASDAQ","inactive":false},{"symbol":"MRVL","exchange":"NASDAQ","inactive":false},{"symbol":"MSFT","exchange":"NASDAQ","inactive":false},{"symbol":"MU","exchange":"NASDAQ","inactive":false},{"symbol":"NTAP","exchange":"NASDAQ","inactive":false},{"symbol":"NTNX","exchange":"NASDAQ","inactive":false},{"symbol":"NVDA","exchange":"NASDAQ","inactive":false},{"symbol":"NXPI","exchange":"NASDAQ","inactive":false},{"symbol":"OKTA","exchange":"NASDAQ","inactive":false},{"symbol":"ON","exchange":"NASDAQ","inactive":false},{"symbol":"PANW","exchange":"NASDAQ","inactive":false},{"symbol":"POWI","exchange":"NASDAQ","inactive":false},{"symbol":"PTON","exchange":"NASDAQ","inactive":false},{"symbol":"PUBM","exchange":"NASDAQ","inactive":false},{"symbol":"QCOM","exchange":"NASDAQ","inactive":false},{"symbol":"RIVN","exchange":"NASDAQ","inactive":false},{"symbol":"RMBS","exchange":"NASDAQ","inactive":false},{"symbol":"ROKU","exchange":"NASDAQ","inactive":true},{"symbol":"RPD","exchange":"NASDAQ","inactive":false},{"symbol":"SEDG","exchange":"NASDAQ","inactive":false},{"symbol":"SITM","exchange":"NASDAQ","inactive":false},{"symbol":"SLAB","exchange":"NASDAQ","inactive":false},{"symbol":"SMCI","exchange":"NASDAQ","inactive":false},{"symbol":"SNPS","exchange":"NASDAQ","inactive":false},{"symbol":"STX","exchange":"NASDAQ","inactive":false},{"symbol":"SWKS","exchange":"NASDAQ","inactive":false},{"symbol":"SYNA","exchange":"NASDAQ","inactive":false},{"symbol":"TEAM","exchange":"NASDAQ","inactive":false},{"symbol":"TENB","exchange":"NASDAQ","inactive":false},{"symbol":"TER","exchange":"NASDAQ","inactive":false},{"symbol":"TSLA","exchange":"NASDAQ","inactive":false},{"symbol":"TTD","exchange":"NASDAQ","inactive":false},{"symbol":"TTWO","exchange":"NASDAQ","inactive":false},{"symbol":"TXN","exchange":"NASDAQ","inactive":false},{"symbol":"VECO","exchange":"NASDAQ","inactive":false},{"symbol":"VIAV","exchange":"NASDAQ","inactive":false},{"symbol":"WDAY","exchange":"NASDAQ","inactive":false},{"symbol":"WDC","exchange":"NASDAQ","inactive":false},{"symbol":"ZM","exchange":"NASDAQ","inactive":false},{"symbol":"ZS","exchange":"NASDAQ","inactive":false}],"TW":[{"symbol":"2330","exchange":"TWSE","inactive":false},{"symbol":"2303","exchange":"TWSE","inactive":false},{"symbol":"2454","exchange":"TWSE","inactive":false},{"symbol":"2379","exchange":"TWSE","inactive":false},{"symbol":"3034","exchange":"TWSE","inactive":false},{"symbol":"2458","exchange":"TWSE","inactive":false},{"symbol":"3035","exchange":"TWSE","inactive":false},{"symbol":"3443","exchange":"TWSE","inactive":false},{"symbol":"5269","exchange":"TWSE","inactive":false},{"symbol":"6415","exchange":"TWSE","inactive":false},{"symbol":"3661","exchange":"TWSE","inactive":false},{"symbol":"3529","exchange":"TPEX","inactive":false},{"symbol":"5274","exchange":"TPEX","inactive":false},{"symbol":"2344","exchange":"TWSE","inactive":false},{"symbol":"2408","exchange":"TWSE","inactive":false},{"symbol":"2337","exchange":"TWSE","inactive":false},{"symbol":"8299","exchange":"TPEX","inactive":false},{"symbol":"3260","exchange":"TPEX","inactive":false},{"symbol":"4967","exchange":"TWSE","inactive":false},{"symbol":"3711","exchange":"TWSE","inactive":false},{"symbol":"2449","exchange":"TWSE","inactive":false},{"symbol":"6239","exchange":"TWSE","inactive":false},{"symbol":"3374","exchange":"TPEX","inactive":false},{"symbol":"6223","exchange":"TPEX","inactive":false},{"symbol":"6510","exchange":"TPEX","inactive":false},{"symbol":"6488","exchange":"TPEX","inactive":false},{"symbol":"3532","exchange":"TWSE","inactive":false},{"symbol":"2317","exchange":"TWSE","inactive":false},{"symbol":"2382","exchange":"TWSE","inactive":false},{"symbol":"2357","exchange":"TWSE","inactive":false},{"symbol":"2376","exchange":"TWSE","inactive":false},{"symbol":"3231","exchange":"TWSE","inactive":false},{"symbol":"2356","exchange":"TWSE","inactive":false},{"symbol":"4938","exchange":"TWSE","inactive":false},{"symbol":"2324","exchange":"TWSE","inactive":false},{"symbol":"2353","exchange":"TWSE","inactive":false},{"symbol":"2377","exchange":"TWSE","inactive":false},{"symbol":"3706","exchange":"TWSE","inactive":false},{"symbol":"6669","exchange":"TWSE","inactive":false},{"symbol":"2308","exchange":"TWSE","inactive":false},{"symbol":"2301","exchange":"TWSE","inactive":false},{"symbol":"3044","exchange":"TWSE","inactive":false},{"symbol":"3037","exchange":"TWSE","inactive":false},{"symbol":"8046","exchange":"TWSE","inactive":false},{"symbol":"2313","exchange":"TWSE","inactive":false},{"symbol":"3189","exchange":"TWSE","inactive":false},{"symbol":"2383","exchange":"TWSE","inactive":false},{"symbol":"4958","exchange":"TWSE","inactive":false},{"symbol":"6274","exchange":"TPEX","inactive":false},{"symbol":"2368","exchange":"TWSE","inactive":false},{"symbol":"2492","exchange":"TWSE","inactive":false},{"symbol":"2327","exchange":"TWSE","inactive":false},{"symbol":"3008","exchange":"TWSE","inactive":false},{"symbol":"3019","exchange":"TWSE","inactive":false},{"symbol":"3406","exchange":"TWSE","inactive":false},{"symbol":"2409","exchange":"TWSE","inactive":false},{"symbol":"3481","exchange":"TWSE","inactive":false},{"symbol":"2474","exchange":"TWSE","inactive":false},{"symbol":"2498","exchange":"TWSE","inactive":false},{"symbol":"2371","exchange":"TWSE","inactive":false},{"symbol":"2347","exchange":"TWSE","inactive":false},{"symbol":"3702","exchange":"TWSE","inactive":false},{"symbol":"5434","exchange":"TWSE","inactive":false},{"symbol":"3017","exchange":"TWSE","inactive":false},{"symbol":"3324","exchange":"TPEX","inactive":false},{"symbol":"2421","exchange":"TWSE","inactive":false},{"symbol":"3013","exchange":"TWSE","inactive":false},{"symbol":"6121","exchange":"TPEX","inactive":false},{"symbol":"3533","exchange":"TWSE","inactive":false},{"symbol":"3105","exchange":"TPEX","inactive":false},{"symbol":"3081","exchange":"TPEX","inactive":false},{"symbol":"3363","exchange":"TPEX","inactive":false},{"symbol":"3023","exchange":"TWSE","inactive":false},{"symbol":"2385","exchange":"TWSE","inactive":false},{"symbol":"6117","exchange":"TWSE","inactive":false},{"symbol":"6116","exchange":"TWSE","inactive":false},{"symbol":"2402","exchange":"TWSE","inactive":false},{"symbol":"2481","exchange":"TWSE","inactive":false},{"symbol":"5425","exchange":"TPEX","inactive":false},{"symbol":"8086","exchange":"TPEX","inactive":false}]};
function mpDate(v){
  const s=String(v).replace(/[\/-]/g,'');
  if(/^\d{7}$/.test(s))return (Number(s.slice(0,3))+1911)+'-'+s.slice(3,5)+'-'+s.slice(5,7);
  if(/^\d{8}$/.test(s))return s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8);
  throw Error('Invalid exchange quote date');
}
function mpNumber(v){const n=Number(String(v).replace(/,/g,''));return Number.isFinite(n)&&n>0?n:null;}
function mpBuild(market,day,live,official,at){
  const quotes={},missing=[],inactive=[],sources={};
  for(const s of MP_POOL[market]){
    if(s.inactive){inactive.push(s.symbol);continue;}
    const matches=live.filter(r=>r.market===market&&String(r.symbol)===s.symbol);
    if(matches.length!==1){missing.push(s.symbol);continue;}
    const q=matches[0],stamp=String(q.quote_time||''),p=mpNumber(q.price),currency=market==='US'?'USD':'TWD';
    const t=stamp.slice(11,19),nearClose=market==='US'?t>='15:59:00'&&t<='16:10:00':t>='13:25:00'&&t<='13:40:00';
    if(q.status==='OK'&&p&&q.currency===currency&&stamp.slice(0,10)===day&&nearClose){
      quotes[s.symbol]={close:p,priceDate:day,currency,source:'GOOGLEFINANCE',observedAt:at};
    }else{
      const rows=official.filter(r=>String(r.SecuritiesCompanyCode||r.Code)===s.symbol&&mpDate(r.Date)===day);
      const price=rows.length===1?mpNumber(rows[0].Close||rows[0].ClosingPrice):null;
      if(market==='TW'&&s.exchange==='TPEX'&&price)quotes[s.symbol]={close:price,priceDate:day,currency,source:'TPEx official daily quotes',observedAt:at};
      else missing.push(s.symbol);
    }
  }
  for(const q of Object.values(quotes))sources[q.source]=(sources[q.source]||0)+1;
  return {schema:1,market,day,status:missing.length?'PARTIAL_REFERENCE_QUOTES':'REFERENCE_QUOTES_COMPLETE',
    expected:MP_POOL[market].length,succeeded:Object.keys(quotes).length,inactive,missing,sources,quotes,capturedAt:at};
}
function mpGate(market,day){
  const p=PropertiesService.getScriptProperties(),k='MP_SESSION_'+market,state=JSON.parse(p.getProperty(k)||'{}'),now=Date.now();
  const stop=gnGateDecision(state,day,now);if(stop)return stop;
  p.setProperty(k,JSON.stringify({day,done:false,attempts:state.day===day?Number(state.attempts||0)+1:1,retryAfter:now+20*60*1000}));return '';
}
function mpDone(market,day){const p=PropertiesService.getScriptProperties(),k='MP_SESSION_'+market,s=JSON.parse(p.getProperty(k)||'{}');p.setProperty(k,JSON.stringify({...s,day,done:true}));}
function mpSession(market,now){
  if(market==='US')return gnExpectedSession(Utilities.formatDate(now,'America/New_York','yyyy-MM-dd HH:mm:ss'));
  const s=Utilities.formatDate(now,'Asia/Taipei','yyyy-MM-dd HH:mm:ss'),day=s.slice(0,10),w=new Date(day+'T12:00:00Z').getUTCDay();
  return [0,6].includes(w)||s.slice(11,19)<'14:00:00'?null:day;
}
function captureMarketPool(){
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return;
  try{
    const now=new Date(),at=now.toISOString(),results=[];let book,live;
    for(const market of ['US','TW']){
      const day=mpSession(market,now);if(!day){results.push({market,status:'WAIT_CLOSE'});continue;}
      const stop=mpGate(market,day);if(stop){results.push({market,status:stop});continue;}
      try{
        book=book||SpreadsheetApp.openById(MP_BOOK);live=live||gnRead(book.getSheetByName('market_quotes'));
        let official=[];
        if(market==='TW'&&live.some(r=>r.market==='TW'&&r.exchange==='TPEX'&&(r.status!=='OK'||String(r.quote_time).slice(0,10)!==day))){
          const response=UrlFetchApp.fetch('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes',{muteHttpExceptions:true});
          if(response.getResponseCode()===200)official=JSON.parse(response.getContentText());
        }
        const batch=mpBuild(market,day,live,official,at),tab=book.getSheetByName('market_quote_batches'),history=gnTab(book,'market_price_history_'+day.slice(0,7),['market','day','symbol','price','currency','source','captured_at'],true);
        const old=gnRead(history),seen=new Set(old.map(r=>r.market+'|'+r.day+'|'+r.symbol));
        const rows=Object.entries(batch.quotes).filter(([s])=>!seen.has(market+'|'+day+'|'+s)).map(([s,q])=>[market,day,s,q.close,q.currency,q.source,at]);
        if(rows.length)history.getRange(history.getLastRow()+1,1,rows.length,7).setValues(rows);
        const record=[market,day,batch.status,batch.expected,batch.succeeded,batch.inactive.length,batch.missing.join(','),at,JSON.stringify(batch)];
        tab.appendRow(record);
        const latest=gnTab(book,'market_latest',['market','day','status','expected','succeeded','inactive','missing','captured_at','payload'],true);
        if(!batch.missing.length)latest.getRange(market==='US'?2:3,1,1,9).setValues([record]);
        SpreadsheetApp.flush();
        if(!batch.missing.length)mpDone(market,day);
        results.push({market,day,status:batch.status,succeeded:batch.succeeded,inactive:batch.inactive,missing:batch.missing,sources:batch.sources});
      }catch(e){results.push({market,day,status:'FAILED',error:String(e.message).slice(0,200)});}
    }
    console.log(JSON.stringify(results));return results;
  }finally{lock.releaseLock();}
}
function installMarketPool(){
  for(const t of ScriptApp.getProjectTriggers())if(['captureTaiwanProbe','captureMarketPool'].includes(t.getHandlerFunction()))ScriptApp.deleteTrigger(t);
  ScriptApp.newTrigger('captureMarketPool').timeBased().everyMinutes(5).create();
  return captureMarketPool();
}
function doGet(){
  // Deliberately fixed, public quote-only book/tab. No parameters can select files.
  const rows=gnRead(SpreadsheetApp.openById(MP_BOOK).getSheetByName('market_quote_batches'));
  const markets={};
  for(const row of rows){
    const b=JSON.parse(row.payload);
    if(!['US','TW'].includes(b.market))continue;
    const old=markets[b.market];
    if(!old||b.day>old.day||(b.day===old.day&&b.succeeded>=old.succeeded))markets[b.market]=b;
  }
  return ContentService.createTextOutput(JSON.stringify({schema:1,markets})).setMimeType(ContentService.MimeType.JSON);
}
if(typeof module!=='undefined')module.exports={mpBuild,mpDate,MP_POOL};