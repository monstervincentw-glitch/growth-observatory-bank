/** Research overlay only. Never mutate official CSV or enable in historical replay. */
export const CPI_SERIES = ['CPIAUCSL','CPIAUCNS','CPILFESL','CPILFENS'];
export const ESTIMATE_STATUS = 'ESTIMATED_INTERPOLATED';
export const NEIGHBOURS_AVAILABLE_AT = '2025-12-18T13:30:00Z';
export function interpolateCpi(series, rows, {vintage, acquiredAt, enabled=false}={}) {
  const original = rows.map(row=>({...row}));
  if (!enabled || !CPI_SERIES.includes(series) || vintage < '2025-12-19' ||
      !acquiredAt || acquiredAt < NEIGHBOURS_AVAILABLE_AT) return {rows:original, estimates:[]};
  if (rows.some(r=>r.date==='2025-10-01' && Number.isFinite(r.value))) return {rows:original, estimates:[]};
  const left=rows.find(r=>r.date==='2025-09-01'), right=rows.find(r=>r.date==='2025-11-01');
  if (!(left?.value>0 && right?.value>0)) return {rows:original, estimates:[]};
  const estimate={series,date:'2025-10-01',value:Math.sqrt(left.value*right.value),
    dataStatus:ESTIMATE_STATUS,method:'geometric_interpolation',seasonalAdjustment:
      ['CPIAUCSL','CPILFESL'].includes(series)?'SA':'NSA',
    source:'https://alfred.stlouisfed.org/graph/alfredgraph.csv?id='+series+'&vintage_date='+vintage,
    vintageDate:vintage,neighbours:[{...left},{...right}],neighboursAvailableAt:NEIGHBOURS_AVAILABLE_AT,
    firstObservedAt:acquiredAt,availableAt:acquiredAt,usage:'LIVE_RESEARCH_ONLY_NOT_HISTORICAL_PIT'};
  return {rows:[...original.filter(r=>r.date!==estimate.date),{date:estimate.date,value:estimate.value}]
    .sort((a,b)=>a.date.localeCompare(b.date)), estimates:[estimate]};
}
