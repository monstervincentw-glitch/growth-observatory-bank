/** Wake the existing daily worker and mirror its completed public research. */
const PR_STATUS=['market','session_date','status','stage','completed','expected','attempts','at','error','next_retry','financial_reused','financial_refreshed','batch'];
const PR_LATEST=['market','day','status','expected','succeeded','inactive','missing','captured_at','payload'];

function installPipelineRunner(){
  const book=SpreadsheetApp.openById(MP_BOOK);
  // This account already reads the public research book. Let the same account
  // publish research here; private portfolio permissions are not changed.
  const accounts=book.getViewers().map(user=>user.getEmail()).filter(email=>email.endsWith('.gserviceaccount.com'));
  for(const email of accounts)book.addEditor(email);
  const latest=gnTab(book,'market_latest',PR_LATEST,true),status=gnTab(book,'pipeline_status',PR_STATUS,true);
  const source=gnRead(book.getSheetByName('market_quote_batches'));
  for(const market of ['US','TW']){
    const rows=source.filter(row=>row.market===market&&row.status==='REFERENCE_QUOTES_COMPLETE').sort((a,b)=>String(a.day).localeCompare(String(b.day)));
    const row=rows.at(-1);if(row)latest.getRange(market==='US'?2:3,1,1,9).setValues([PR_LATEST.map(key=>row[key]??'')]);
  }
  for(const trigger of ScriptApp.getProjectTriggers())if(['syncBankResearch','pulseResearchPipeline'].includes(trigger.getHandlerFunction()))ScriptApp.deleteTrigger(trigger);
  ScriptApp.newTrigger('pulseResearchPipeline').timeBased().everyMinutes(5).create();
  console.log(JSON.stringify({installed:true,writerAccounts:accounts.length,handler:'pulseResearchPipeline'}));
  return pulseResearchPipeline();
}

function syncPipelineLegacy(book){
  const ranges=['bank_snapshot!A1:E300','system_status!A1:C19','macro_latest!A1:J7','us_candidates!A1:U100','tw_candidates!A1:U100','backtest_summary!A1:J100','risk_dashboard!A1:M100'];
  const result=Sheets.Spreadsheets.Values.batchGet(MP_BOOK,{ranges,valueRenderOption:'UNFORMATTED_VALUE'}).valueRanges;
  const rows=result[0].values,snapshot=verifyEnvelope_(rows),batch=rows[1][0];
  const properties=PropertiesService.getScriptProperties();
  if(properties.getProperty('PR_LEGACY_BATCH')===batch)return;
  Sheets.Spreadsheets.batchUpdate({requests:legacyRequests_(snapshot,batch,result.slice(1).map(value=>value.values||[]))},MP_BOOK);
  properties.setProperty('PR_LEGACY_BATCH',batch);
}

function pulseResearchPipeline(){
  const properties=PropertiesService.getScriptProperties(),clock=new Date();
  const wanted=['US','TW'].flatMap(market=>{
    const day=mpSession(market,clock);
    return day&&properties.getProperty('PR_DONE_'+market)!==day?[{market,day}]:[];
  });
  if(!wanted.length)return;
  const book=SpreadsheetApp.openById(MP_BOOK);
  const envelopeRows=Sheets.Spreadsheets.Values.get(MP_BOOK,'bank_snapshot!A1:E300',{valueRenderOption:'UNFORMATTED_VALUE'}).values;
  const snapshot=verifyEnvelope_(envelopeRows);
  const states=gnRead(book.getSheetByName('pipeline_status'));
  let pending=false,completed=false;const wakes=[];
  for(const {market,day} of wanted){
    const liveReady=market!=='US'||snapshot.markets.US.stocks.filter(stock=>!stock.marketEvent).every(stock=>stock.financial.date_basis==='LIVE_OBSERVED_SEC');
    if(snapshot.markets[market].macro.marketAsOf>=day&&liveReady){properties.setProperty('PR_DONE_'+market,day);completed=true;continue;}
    const state=states.find(row=>row.market===market&&row.session_date===day);
    if(state&&state.status==='FAILED'&&Number(state.attempts)>=3){properties.setProperty('PR_DONE_'+market,day);continue;}
    const key='PR_WAKE_'+market,previous=JSON.parse(properties.getProperty(key)||'{}');
    const count=previous.day===day?Number(previous.count||0):0;
    if(count>=24){properties.setProperty('PR_DONE_'+market,day);continue;}
    wakes.push({key,day,count:count+1});pending=true;
  }
  if(completed)syncPipelineLegacy(book);
  if(!pending)return;
  for(const item of wakes)properties.setProperty(item.key,JSON.stringify({day:item.day,count:item.count}));
  const response=UrlFetchApp.fetch('https://growth-portfolio-private-v1.onrender.com/_stcore/health',{muteHttpExceptions:true});
  console.log(JSON.stringify({pipelineWakeStatus:response.getResponseCode(),pending:true}));
}

function continueLatestFinancialPipeline(){PropertiesService.getScriptProperties().deleteProperty('PR_DONE_US');return pulseResearchPipeline();}