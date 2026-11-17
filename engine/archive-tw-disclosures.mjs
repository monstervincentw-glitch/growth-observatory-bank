/**
 * Preserve official TWSE MOPS material-announcement timestamps for Taiwan stock
 * research. IMPORTANT: an Article 31 material announcement is NOT, by itself,
 * proof that an entire quarterly financial report (or the same exact numbers)
 * was uploaded. This archive is evidence for later reconciliation ONLY.
 *
 * The public TWSE endpoint provides a rolling daily feed rather than a
 * historical range; run this independently every day and persist each item.
 */
import {createHash} from 'node:crypto';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

export const SOURCE = 'https://openapi.twse.com.tw/v1/opendata/t187ap04_L';
const sha256 = value => createHash('sha256').update(value).digest('hex');
const stringify = obj => JSON.stringify(obj);

export function rocDay(input) {
  const raw = String(input ?? '').trim();
  if (!/^\d{7}$/.test(raw)) throw Error('Invalid MOPS ROC day');
  const y = Number(raw.slice(0, 3)) + 1911;
  const m = Number(raw.slice(3, 5));
  const d = Number(raw.slice(5, 7));
  const utc = new Date(Date.UTC(y, m - 1, d));
  if (utc.getUTCFullYear() !== y || utc.getUTCMonth() !== m - 1 || utc.getUTCDate() !== d)
    throw Error('Invalid MOPS calendar day');
  return utc.toISOString().slice(0, 10);
}

export function announcedAt(rocDate, rocTime) {
  const day = rocDay(rocDate);
  const raw = String(rocTime ?? '').trim();
  if (!/^\d{1,6}$/.test(raw)) throw Error('Invalid MOPS announcement time');
  const hhmmss = raw.padStart(6, '0');
  const h = Number(hhmmss.slice(0, 2));
  const m = Number(hhmmss.slice(2, 4));
  const s = Number(hhmmss.slice(4, 6));
  if (h > 23 || m > 59 || s > 59) throw Error('Invalid MOPS announcement clock');
  return `${day}T${hhmmss.slice(0,2)}:${hhmmss.slice(2,4)}:${hhmmss.slice(4,6)}+08:00`;
}

export function parseDisclosureFeed(rows, symbols, retrievedAt, endpoint = SOURCE) {
  if (!Array.isArray(rows) || !Array.isArray(symbols) || symbols.length < 1)
    throw Error('MOPS disclosure feed or target universe is invalid');
  if (!Number.isFinite(Date.parse(retrievedAt)) || !endpoint.startsWith('https://openapi.twse.com.tw/'))
    throw Error('MOPS retrieval provenance missing');
  const allowed = new Set(symbols);
  const result = [];
  for (const original of rows) {
    if (!original || typeof original !== 'object' || Array.isArray(original))
      throw Error('MOPS response contains invalid row');
    const symbol = String(original['公司代號'] ?? '').trim();
    if (!allowed.has(symbol)) continue;
    const publishedAt = announcedAt(original['發言日期'], original['發言時間']);
    // Some MOPS rows have an event date well before publication. Never use
    // 事實發生日 as an evidence release timestamp.
    const sourcePrintDate = rocDay(original['出表日期']);
    const subject = String(original['主旨 '] ?? original['主旨'] ?? '').trim();
    const statement = String(original['說明'] ?? '').trim();
    const article = String(original['符合條款'] ?? '').trim();
    if (!subject) throw Error(`MOPS disclosure missing subject for ${symbol}`);
    const material = /財務報告|財報|合併財務|營運成果|每股盈餘|自結損益|財務資訊/.test(subject);
    const meetingOnly = /預計召開|董事會召開日期|召開董事會/.test(subject) && !/通過|決議/.test(subject);
    const rawHash = sha256(stringify(original));
    result.push({
      id: sha256([symbol, publishedAt, article, subject, rawHash].join('|')),
      symbol,
      name: String(original['公司名稱'] ?? '').trim(),
      publishedAt,
      firstObservedAt: retrievedAt,
      lastObservedAt: retrievedAt,
      sourcePrintDate,
      eventDate: original['事實發生日'] ? rocDay(original['事實發生日']) : null,
      article,
      subject,
      statement,
      financialTopic: material,
      boardMeetingNoticeOnly: meetingOnly,
      // A material fact is not conclusive evidence of an exact financial
      // report's filing time or of the numbers in the FinMind snapshot.
      officialFinancialReportVerified: false,
      evidenceKind: 'TWSE_MOPS_MATERIAL_ANNOUNCEMENT',
      sourceUrl: endpoint,
      originalRowSha256: rawHash,
    });
  }
  return result.sort((a, b) => a.publishedAt.localeCompare(b.publishedAt) || a.id.localeCompare(b.id));
}

export function mergeEvidence(previous, incoming, day, receipt) {
  const base = previous ?? {schema:1, date:day, sourceUrl: SOURCE, receipts:[], records:[]};
  if (base.schema !== 1 || base.date !== day || !Array.isArray(base.records) || !Array.isArray(base.receipts))
    throw Error('Existing TWSE evidence archive has incompatible schema');
  const merged = new Map(base.records.map(row => [row.id, row]));
  for (const row of incoming) {
    if (row.publishedAt.slice(0, 10) !== day) throw Error('MOPS record archived under wrong day');
    const before = merged.get(row.id);
    if (before) {
      if (before.originalRowSha256 !== row.originalRowSha256) throw Error('MOPS evidence changed under same ID');
      merged.set(row.id, {...before, lastObservedAt:row.lastObservedAt});
    } else merged.set(row.id, row);
  }
  const receipts = [...base.receipts];
  if (!receipts.some(r => r.observedAt === receipt.observedAt && r.responseSha256 === receipt.responseSha256))
    receipts.push(receipt);
  return {
    ...base, receipts:receipts.sort((a,b)=>a.observedAt.localeCompare(b.observedAt)),
    records:[...merged.values()].sort((a,b)=>a.publishedAt.localeCompare(b.publishedAt)||a.id.localeCompare(b.id)),
  };
}

export async function archiveOfficialFeed(registryPath, outputFolder, {fetcher = fetch, now = () => new Date()} = {}) {
  const registry = JSON.parse(await readFile(registryPath, 'utf8'));
  const symbols = Object.keys(registry).filter(x => /^\d{4}$/.test(x)).sort();
  if (symbols.length < 60) throw Error('Insufficient Taiwan research symbols in version registry');
  const retrievedAt = now().toISOString();
  const response = await fetcher(SOURCE, {headers:{'Accept':'application/json'},signal:AbortSignal.timeout(18000)});
  if (!response.ok) throw Error(`TWSE official disclosure unavailable: HTTP ${response.status}`);
  const raw = await response.text();
  if (raw.length > 8_000_000) throw Error('TWSE disclosure payload unexpectedly large');
  const rows = JSON.parse(raw);
  const evidence = parseDisclosureFeed(rows, symbols, retrievedAt);
  const receipt = {
    observedAt:retrievedAt, responseSha256:sha256(raw),
    totalRecords:rows.length, matchingRecords:evidence.length, targetStocks:symbols.length, sourceUrl:SOURCE,
  };
  const byDate = new Map();
  for (const row of evidence) {
    const day = row.publishedAt.slice(0,10);
    if (!byDate.has(day)) byDate.set(day, []);
    byDate.get(day).push(row);
  }
  for (const [day, records] of byDate) {
    const path = join(outputFolder,'announcements',day+'.json');
    let previous;
    try {previous=JSON.parse(await readFile(path,'utf8'));}
    catch(error) {if (error.code !== 'ENOENT') throw error;}
    const updated = mergeEvidence(previous,records,day,receipt);
    await mkdir(join(outputFolder,'announcements'),{recursive:true});
    await writeFile(path,JSON.stringify(updated)+'\n');
  }
  // Capture source provenance even on days with no announcements by the
  // tracked stocks. Do not misinterpret zero rows as proof of no filing.
  const dated = retrievedAt.slice(0,10);
  const receiptPath = join(outputFolder,'fetches',dated+'.json');
  let existing = [];
  try {existing=JSON.parse(await readFile(receiptPath,'utf8')).receipts;}
  catch(error){if(error.code !== 'ENOENT')throw error;}
  if (!Array.isArray(existing)) throw Error('Invalid prior TWSE receipt archive');
  await mkdir(join(outputFolder,'fetches'),{recursive:true});
  await writeFile(receiptPath,JSON.stringify({schema:1,date:dated,sourceUrl:SOURCE,receipts:[...existing,receipt]})+'\n');
  return {retrievedAt, targetStocks:symbols.length, announcementCount:evidence.length, storedDates:[...byDate.keys()].sort()};
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const [, , registry, destination] = process.argv;
  if (!registry || !destination) throw Error('Usage: node engine/archive-tw-disclosures.mjs REGISTRY_JSON OUTPUT_FOLDER');
  archiveOfficialFeed(registry,destination)
    .then(result => process.stdout.write(JSON.stringify(result)+'\n'))
    .catch(error => {console.error('TWSE disclosure archive failed: '+error.message);process.exitCode=1;});
}
