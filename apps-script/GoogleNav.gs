/** Private Google-only NAV pilot. No GitHub, external HTTP, or research dependency.
 * Bound to the PRIVATE spreadsheet. Run previewGoogleNav() first.
 * installGoogleNav() installs this script's trigger only. Never alter bank sheet.
 * Source is a regular-close-time Google observation, NOT certified closing data.
 */
const GN = {
  book: '10Xct7p8UF8rnwXzBPSd8v_a-stn7DjAWwOFeKTQJhNc',
  quoteHeaders: ['symbol','google_ticker','price','quote_time','currency','status'],
  navHeaders: ['portfolio_id','date','nav','cash','equity_value','liabilities','daily_pnl','daily_return','cumulative_pnl','twr','source','note'],
  priceHeaders: ['market','date','symbol','price','currency','quote_time','captured_at','source'],
  runHeaders: ['at','status','session_date','quotes_saved','nav_saved','detail'],
  source: 'Google Finance 收盤後參考估值'
};
function gnRecords(rows) {
  if (!rows.length) throw Error('Missing headers');
  const h=rows[0];
  return rows.slice(1).filter(r=>String(r[0]||'').trim()).map(r=>Object.fromEntries(h.map((k,i)=>[k,r[i]===undefined?'':r[i]])));
}
function gnNum(v,label,positive) {
  if(v===''||v===null||v===undefined||typeof v==='boolean')throw Error('Missing '+label);
  const n=Number(v); if(!Number.isFinite(n)||(positive?n<=0:n<0))throw Error('Invalid '+label); return n;
}
function gnRound(v) {return Math.round((v+Number.EPSILON)*100)/100;}
function gnAccount(txs,pid,day) {
  let cash=0; const positions={},ids=new Set();
  const rows=txs.filter(t=>String(t.portfolio_id)===pid);
  for(const t of rows) {
    if(!t.id||ids.has(String(t.id)))throw Error('Duplicate/missing ledger ID'); ids.add(String(t.id));
    if(!/^\d{4}-\d{2}-\d{2}$/.test(String(t.trade_date)))throw Error('Missing trade date');
  }
  rows.sort((a,b)=>[a.trade_date,a.at,a.id].join('|').localeCompare([b.trade_date,b.at,b.id].join('|')));
  for(const t of rows) {
    if(t.trade_date>day)continue;
    const fee=t.fee===''?0:gnNum(t.fee,'fee',false),action=String(t.action).toUpperCase(),s=String(t.symbol).trim();
    if(action==='DEPOSIT'||action==='WITHDRAW')cash+=(action==='DEPOSIT'?1:-1)*gnNum(t.cash_amount,'cash flow',true);
    else if(action==='BUY'||action==='SELL') {
      if(!/^[A-Z0-9][A-Z0-9.-]{0,19}$/.test(s))throw Error('Invalid symbol');
      const q=gnNum(t.quantity,'quantity',true),p=gnNum(t.price,'trade price',true);
      positions[s]=(positions[s]||0)+(action==='BUY'?q:-q);
      if(positions[s]<-1e-8)throw Error('Oversell');
      cash+=(action==='BUY'?-q*p:q*p)-fee;
    } else throw Error('Unknown action');
    if(cash < -1e-6)throw Error('Negative cash');
  }
  for(const s of Object.keys(positions))if(Math.abs(positions[s])<1e-8)delete positions[s];
  return {cash:gnRound(cash),positions};
}
// NYSE published 2026-2028 calendar. An unknown holiday still fails the
// quote-date completeness check; no synthetic closing price is ever created.
const GN_US_CLOSED=('2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19 2026-07-03 2026-09-07 2026-11-26 2026-12-25 '+
 '2027-01-01 2027-01-18 2027-02-15 2027-03-26 2027-05-31 2027-06-18 2027-07-05 2027-09-06 2027-11-25 2027-12-24 '+
 '2028-01-17 2028-02-21 2028-04-14 2028-05-29 2028-06-19 2028-07-04 2028-09-04 2028-11-23 2028-12-25').split(' ');
const GN_US_EARLY=['2026-11-27','2026-12-24','2027-11-26','2028-07-03','2028-11-24'];
function gnNearUsClose(stamp,day){
 const t=String(stamp).slice(11,19),early=GN_US_EARLY.includes(day);
 return t>=(early?'12:59:00':'15:59:00')&&t<=(early?'13:10:00':'16:10:00');
}
function gnExpectedSession(local) {
  // local must be New York wall clock supplied by the adapter; DST handled there.
  const [date,time]=local.split(' '),hour=Number(time.slice(0,2));
  const d=new Date(date+'T12:00:00Z'),weekday=d.getUTCDay();
  if(weekday>=1&&weekday<=5&&!GN_US_CLOSED.includes(date)&&hour>=9&&hour<17)return null;
  if(hour<9||weekday===0||weekday===6)d.setUTCDate(d.getUTCDate()-1);
  while([0,6].includes(d.getUTCDay())||GN_US_CLOSED.includes(d.toISOString().slice(0,10)))d.setUTCDate(d.getUTCDate()-1);
  return d.toISOString().slice(0,10);
}
function gnPlan(portfolios,txs,quotes,navs,prices,day,capturedAt) {
  const bySymbol={},duplicates=new Set();
  for(const q of quotes){if(bySymbol[q.symbol])duplicates.add(q.symbol);bySymbol[q.symbol]=q;}
  const priceRows=[],navRows=[],saved=new Set(prices.map(p=>p.market+'|'+p.date+'|'+p.symbol));
  for(const p of portfolios) {
    if(String(p.archived).toLowerCase()==='true'||p.market!=='US')continue;
    if(p.currency!=='USD')throw Error('Portfolio currency mismatch');
    const a=gnAccount(txs,String(p.id),day); let equity=0;
    const pending=[];
    for(const [s,quantity] of Object.entries(a.positions)) {
      const q=bySymbol[s];
      if(!q||duplicates.has(s)||q.status!=='OK'||q.currency!=='USD')throw Error('Incomplete/duplicate quotes: '+s);
      const stamp=String(q.quote_time);
      if(!/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(stamp)||stamp.slice(0,10)!==day)throw Error('Quote date mismatch: '+s);
      // Explicitly a near-close reference quote; reject intraday/after-hours marks.
      if(!gnNearUsClose(stamp,day))throw Error('Not near regular close: '+s);
      const price=gnNum(q.price,'quote price',true);equity+=quantity*price;
      const key='US|'+day+'|'+s,old=prices.find(r=>r.market==='US'&&r.date===day&&r.symbol===s);
      if(old&&Math.abs(Number(old.price)-price)>1e-8)throw Error('Saved price conflict: '+s);
      if(!saved.has(key)){pending.push(['US',day,s,price,'USD',stamp,capturedAt,GN.source]);saved.add(key);}
    }
    const previous=navs.filter(r=>String(r.portfolio_id)===String(p.id)).sort((a,b)=>String(a.date).localeCompare(String(b.date)));
    const existing=previous.filter(r=>r.date===day);
    if(existing.length>1)throw Error('Duplicate NAV dates');
    if(previous.some(r=>r.date>day))throw Error('Do not backfill old sessions');
    priceRows.push(...pending);
    if(existing.length)continue; // Preserve all existing history, including manual rows.
    if(!Object.keys(a.positions).length)throw Error('Cash-only portfolio requires separate session calendar');
    const nav=gnRound(a.cash+equity),prev=previous.filter(r=>r.date<day).pop();
    let flow=0,net=0;
    for(const t of txs.filter(t=>String(t.portfolio_id)===String(p.id)&&t.trade_date<=day)) {
      if(!['DEPOSIT','WITHDRAW'].includes(t.action))continue;
      const f=(t.action==='DEPOSIT'?1:-1)*Number(t.cash_amount);net+=f;
      if(prev&&t.trade_date>prev.date)flow+=f;
    }
    const pnl=prev?gnRound(nav-Number(prev.nav)-flow):'';
    const ret=prev&&!flow&&Number(prev.nav)>0?pnl/Number(prev.nav):'';
    navRows.push([String(p.id),day,nav,a.cash,gnRound(equity),0,pnl,ret,gnRound(nav-net),'',GN.source,
      '近收盤Google報價觀測；非官方收盤驗證。無缺日補值；有存提時區間報酬留白；TWR不推估。']);
  }
  return {priceRows,navRows};
}
function gnTab(book,name,headers,create) {
  let s=book.getSheetByName(name);
  if(!s&&create){s=book.insertSheet(name);s.getRange(1,1,1,headers.length).setValues([headers]);s.setFrozenRows(1);}
  if(!s)return null;
  if(headers&&JSON.stringify(s.getRange(1,1,1,headers.length).getValues()[0])!==JSON.stringify(headers))throw Error('Header mismatch: '+name);
  return s;
}
function gnRead(s) {
  if(!s)return [];
  if(s.getLastRow()>10000)throw Error('Pilot row limit; checkpoint needed');
  const zone=String(s.getParent().getSpreadsheetTimeZone()||'Etc/UTC');
  const timezone=zone==='Etc/GMT'?'Etc/UTC':zone;
  return gnRecords(s.getDataRange().getValues().map(row=>row.map(v=>v instanceof Date?Utilities.formatDate(v,timezone,'yyyy-MM-dd'):v)));  
}
function gnRun(write) {
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'BUSY'};
  let book,runTab,day;
  try {
    const now=new Date(),at=now.toISOString();
    day=gnExpectedSession(Utilities.formatDate(now,'America/New_York','yyyy-MM-dd HH:mm:ss'));
    if(!day)return {status:'WAIT_REGULAR_SESSION_END'};
    if(write){const gate=gnCaptureGate('US',day,Date.now());if(gate)return {status:gate,day};}
    book=SpreadsheetApp.openById(GN.book);
    const nav=gnTab(book,'nav_history',GN.navHeaders,false);
    if(!nav)throw Error('Missing nav_history');
    const q=gnTab(book,'live_quotes',GN.quoteHeaders,false);if(!q)throw Error('Missing live_quotes');
    const priceTab=gnTab(book,'google_price_history',GN.priceHeaders,write);
    runTab=gnTab(book,'google_capture_runs',GN.runHeaders,write);
    const shared=SpreadsheetApp.openById('1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0');
    const quoteRows=gnPoolQuotes(gnRead(q),gnRead(shared.getSheetByName('market_quotes')));
    const result=gnPlan(gnRead(book.getSheetByName('portfolios')),gnRead(book.getSheetByName('ledger')),
      quoteRows,gnRead(nav),gnRead(priceTab),day,at);
    if(write) {
      if(result.priceRows.length)priceTab.getRange(priceTab.getLastRow()+1,1,result.priceRows.length,GN.priceHeaders.length).setValues(result.priceRows);
      SpreadsheetApp.flush();
      // Recheck history before append. Existing Python writer still needs migration
      // before enabling this in production: a script lock doesn't cover that writer.
      const current=gnRead(nav);
      const missing=result.navRows.filter(r=>!current.some(x=>x.portfolio_id===r[0]&&x.date===r[1]));
      if(missing.length)nav.getRange(nav.getLastRow()+1,1,missing.length,GN.navHeaders.length).setValues(missing);
      if(result.priceRows.length||missing.length)runTab.appendRow([at,'COMPLETE',day,result.priceRows.length,missing.length,'Google-only; no research calls']);
      PropertiesService.getScriptProperties().setProperty('GN_LAST_CHECK',JSON.stringify({at,day,quotes:result.priceRows.length,nav:missing.length}));
      SpreadsheetApp.flush();
      const savedNav=gnRead(nav);
      for(const r of missing)if(savedNav.filter(x=>x.portfolio_id===r[0]&&x.date===r[1]).length!==1)throw Error('NAV verification failed');
      gnCaptureDone('US',day);
    }
    return {status:write?'COMPLETE':'DRY_RUN',day,quotes:result.priceRows.length,nav:result.navRows.length};
  } catch(e) {
    if(write&&runTab)runTab.appendRow([new Date().toISOString(),'FAILED',day||'',0,0,String(e.message).slice(0,150)]);
    throw e;
  } finally {lock.releaseLock();}
}
function previewGoogleNav(){const r=gnRun(false);console.log(JSON.stringify(r));return r;}
function captureGoogleNav(){const r=gnRun(true);console.log(JSON.stringify(r));return r;}
function installGoogleNav(){
  // Must be explicitly enabled after coordinating the existing Python NAV writer.
  if(PropertiesService.getScriptProperties().getProperty('GOOGLE_NAV_WRITER_READY')!=='true')throw Error('Set GOOGLE_NAV_WRITER_READY=true only after disabling other automatic NAV writers');
  previewGoogleNav();
  for(const t of ScriptApp.getProjectTriggers())if(t.getHandlerFunction()==='captureGoogleNav')ScriptApp.deleteTrigger(t);
  ScriptApp.newTrigger('captureGoogleNav').timeBased().everyMinutes(5).create();
  return captureGoogleNav();
}
function uninstallGoogleNav(){for(const t of ScriptApp.getProjectTriggers())if(t.getHandlerFunction()==='captureGoogleNav')ScriptApp.deleteTrigger(t);}
if(typeof module!=='undefined')module.exports={gnAccount,gnExpectedSession,gnPlan,gnNearUsClose,GN};

// Taiwan phase-one probe: archive only the explicitly configured sample rows.
// It does not publish the 80-stock research pool or calculate Taiwan NAV.
function gnTaiwanPlan(quotes,prices,day,capturedAt) {
  const rows=[],seen=new Set();
  for(const q of quotes) {
    if(q.market!=='TW')continue;
    if(seen.has(q.symbol))throw Error('Duplicate Taiwan probe');seen.add(q.symbol);
    if(q.status!=='OK'||q.currency!=='TWD'||String(q.quote_time).slice(0,10)!==day)throw Error('Taiwan quote pending');
    const t=String(q.quote_time).slice(11,19);
    if(t<'13:25:00'||t>'13:40:00')throw Error('Taiwan quote not near close');
    const price=gnNum(q.price,'Taiwan price',true);
    const old=prices.filter(r=>r.market==='TW'&&r.date===day&&r.symbol===q.symbol);
    if(old.length>1||old.some(r=>Math.abs(Number(r.price)-price)>1e-8))throw Error('Taiwan saved quote conflict');
    if(!old.length)rows.push(['TW',day,q.symbol,price,'TWD',q.quote_time,capturedAt,GN.source]);
  }
  return rows;
}
function captureTaiwanProbe() {
  const lock=LockService.getScriptLock();if(!lock.tryLock(1000))return {status:'BUSY'};
  try {
    const now=new Date(),at=now.toISOString(),local=Utilities.formatDate(now,'Asia/Taipei','yyyy-MM-dd HH:mm:ss');
    const day=local.slice(0,10),weekday=new Date(day+'T12:00:00Z').getUTCDay();
    if([0,6].includes(weekday)||local.slice(11,19)<'14:00:00')return {status:'WAIT_TAIWAN_CLOSE'};
    const gate=gnCaptureGate('TW',day,Date.now());if(gate)return {status:gate,day};
    const book=SpreadsheetApp.openById(GN.book),probe=book.getSheetByName('google_quote_probe');
    if(!probe)return {status:'TAIWAN_PROBE_NOT_CONFIGURED'};
    const priceTab=gnTab(book,'google_price_history',GN.priceHeaders,true);
    const rows=gnTaiwanPlan(gnRead(probe),gnRead(priceTab),day,at);
    if(rows.length)priceTab.getRange(priceTab.getLastRow()+1,1,rows.length,GN.priceHeaders.length).setValues(rows);
    const runs=gnTab(book,'google_capture_runs',GN.runHeaders,true);
    if(rows.length)runs.appendRow([at,'TW_PROBE_COMPLETE',day,rows.length,0,'Taiwan sample only; no research publication']);
    SpreadsheetApp.flush();gnCaptureDone('TW',day);return {status:'COMPLETE',day,quotes:rows.length};
  } finally {lock.releaseLock();}
}
if(typeof module!=='undefined')module.exports.gnTaiwanPlan=gnTaiwanPlan;
function installTaiwanProbe(){
  for(const t of ScriptApp.getProjectTriggers())if(t.getHandlerFunction()==='captureTaiwanProbe')ScriptApp.deleteTrigger(t);
  ScriptApp.newTrigger('captureTaiwanProbe').timeBased().everyMinutes(5).create();
  return captureTaiwanProbe();
}

function installGoogleCapture(){
  previewGoogleNav();
  for(const t of ScriptApp.getProjectTriggers())if(["captureGoogleNav","captureTaiwanProbe"].includes(t.getHandlerFunction()))ScriptApp.deleteTrigger(t);
  PropertiesService.getScriptProperties().setProperty("GOOGLE_NAV_WRITER_READY","true");
  ScriptApp.newTrigger("captureGoogleNav").timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger("captureTaiwanProbe").timeBased().everyMinutes(5).create();
  console.log(JSON.stringify({installed:true,handlers:["captureGoogleNav","captureTaiwanProbe"]}));
}

// Only Script Properties are checked after a successful session. No Sheet/API reads.
function gnGateDecision(state,day,now){
  if(state.day!==day)return '';
  if(state.done)return 'ALREADY_CAPTURED';
  if(state.attempts>=3)return 'DAILY_RETRY_LIMIT';
  if(now<Number(state.retryAfter||0))return 'RETRY_COOLDOWN';
  return '';
}
function gnCaptureGate(market,day,now){
  const p=PropertiesService.getScriptProperties(),key='GN_SESSION_'+market;
  const state=JSON.parse(p.getProperty(key)||'{}');
  const stop=gnGateDecision(state,day,now);if(stop)return stop;
  p.setProperty(key,JSON.stringify({day,done:false,attempts:state.day===day?Number(state.attempts||0)+1:1,retryAfter:now+20*60*1000}));
  return '';
}
function gnCaptureDone(market,day){
  const p=PropertiesService.getScriptProperties(),key='GN_SESSION_'+market;
  const state=JSON.parse(p.getProperty(key)||'{}');
  p.setProperty(key,JSON.stringify({...state,day,done:true,completedAt:new Date().toISOString()}));
}
if(typeof module!=='undefined')module.exports.gnGateDecision=gnGateDecision;

// Full-pool quotes take precedence; keep private custom tickers outside the pool.
function gnPoolQuotes(privateRows,poolRows){
  const us=poolRows.filter(r=>r.market==='US'),symbols=new Set(us.map(r=>String(r.symbol)));
  return us.concat(privateRows.filter(r=>!symbols.has(String(r.symbol))));
}
if(typeof module!=='undefined')module.exports.gnPoolQuotes=gnPoolQuotes;

function retryGoogleNav(){
  PropertiesService.getScriptProperties().deleteProperty("GN_SESSION_US");
  return captureGoogleNav();
}
