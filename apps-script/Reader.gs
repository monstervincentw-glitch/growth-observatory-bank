const RESEARCH_BOOK = '1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0';
const RESEARCH_RANGE = 'bank_snapshot!A1:E300';

function doGet() {
  try {
    const rows = Sheets.Spreadsheets.Values.get(RESEARCH_BOOK,RESEARCH_RANGE,
      {valueRenderOption:'UNFORMATTED_VALUE'}).values;
    if (!rows || rows.length < 2 || rows.length > 300 ||
        rows[0].join(',') !== 'batch_id,part,parts,sha256,payload_json') throw new Error('invalid header');
    const [batch,,count,sha] = rows[1];
    if (!/^v29-[a-f0-9]{16}$/.test(batch) || !/^[a-f0-9]{64}$/.test(sha) || rows.length !== count+1 ||
        !rows.slice(1).every((r,i)=>r.length===5&&r[0]===batch&&r[1]===i+1&&r[2]===count&&r[3]===sha&&/^[A-Za-z0-9+/=]+$/.test(r[4])))
      throw new Error('invalid envelope');
    return ContentService.createTextOutput(rows.map(row=>row.join(',')).join('\n')+'\n')
      .setMimeType(ContentService.MimeType.CSV);
  } catch (error) {
    console.error('Bank research envelope is unavailable');
    return ContentService.createTextOutput('research_unavailable');
  }
}
