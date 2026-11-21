import test from 'node:test';
import assert from 'node:assert/strict';
import {liveMacroVintage,macroCutoff,scoreMacro,LIVE_MACRO_POLICY} from '../engine/macro.ts';

const csv=(series,vintage,rows)=>`observation_date,${series}_${vintage.replaceAll('-','')}\n`+rows.map(r=>r.join(',')).join('\n');
function data(vintage,last){
 return {
  DFII10:csv('DFII10',vintage,[['2026-08-20',2.35],['2026-08-21',2.40],[last,2.88]]),
  FEDFUNDS:csv('FEDFUNDS',vintage,[['2026-06-01',3.63],['2026-07-01',3.63],['2026-10-01',3.88]]),
  NFCI:csv('NFCI',vintage,[['2026-08-14',-.548],['2026-11-13',-.478]]),
  CPIAUCSL:csv('CPIAUCSL',vintage,[['2025-10-01',324.654],['2026-07-01',332.813],['2026-10-01',337.476]])
 };
}
test('live daily observation includes released version while historical cutoff stays unchanged',()=>{
 const clock=new Date('2026-11-20T22:17:00Z');
 assert.equal(macroCutoff('2026-11-20'),'2026-11-19');
 assert.equal(liveMacroVintage('2026-11-20',clock),'2026-11-20');
 const live=scoreMacro('2026-11-20','2026-11-20',data('2026-11-20','2026-11-19'),clock,'2026-11-20',true);
 assert.equal(live.liveMacroPolicy,LIVE_MACRO_POLICY);
 assert.equal(live.categoryStates.rates,0);
 assert.equal(live.seriesMeta.DFII10.observationDate,'2026-11-19');
 assert.equal(live.seriesMeta.DFII10.availableBy,clock.toISOString());
 const historical=scoreMacro('2026-11-20','2026-11-19',data('2026-11-19','2026-11-18'),clock);
 assert.equal(historical.categoryStates.rates,1);
 assert.equal(historical.liveMacroPolicy,undefined);
 assert.throws(()=>scoreMacro('2026-11-20','2026-11-20',data('2026-11-20','2026-11-19'),clock),/截止日/);
});
test('live release window follows New York daylight saving time and caps at captured price session',()=>{
 assert.equal(liveMacroVintage('2026-11-20',new Date('2026-11-20T21:59:59Z')),'2026-11-19');
 assert.equal(liveMacroVintage('2026-11-20',new Date('2026-11-20T22:00:00Z')),'2026-11-20');
 assert.equal(liveMacroVintage('2026-07-10',new Date('2026-07-10T20:59:59Z')),'2026-07-09');
 assert.equal(liveMacroVintage('2026-07-10',new Date('2026-07-10T21:00:00Z')),'2026-07-10');
 assert.equal(liveMacroVintage('2026-11-20',new Date('2026-11-23T22:00:00Z')),'2026-11-20');
});
