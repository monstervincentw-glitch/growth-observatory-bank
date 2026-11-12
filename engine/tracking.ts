import type {Instrument} from './types.ts';

type PriceObservation=Pick<Instrument,'symbol'|'priceDate'|'history'|'integrity'|'sourceError'|'marketEvent'|'priceValidation'>;
export type ArchivedPortfolio={
 year:number;variant:string;capital:number;nav:number;cash:number;cost:number;
 positions:{symbol:string;role:string;value:number}[];
};

export function estimatePortfolios(portfolios:ArchivedPortfolio[], stocks:PriceObservation[], anchor:string, marketDate:string|null){
 if(!marketDate||marketDate<=anchor)return null;
 const symbols=[...new Set(portfolios.flatMap(p=>p.positions.map(h=>h.symbol)))];
 const bySymbol=new Map(stocks.map(stock=>[stock.symbol,stock]));
 const prices=new Map<string,Map<string,number>>();
 for(const symbol of symbols){
  const stock=bySymbol.get(symbol);
  if(!stock?.integrity||stock.sourceError||stock.marketEvent||stock.priceValidation?.cutoff!==stock.priceDate)return null;
  const bars=new Map(stock.history.filter(b=>b.date>=anchor&&b.date<=marketDate&&b.date<=stock.priceDate&&Number.isFinite(b.close)&&b.close>0).map(b=>[b.date,b.close]));
  if(!bars.has(anchor))return null;
  prices.set(symbol,bars);
 }
 const candidateDates=symbols.length?[...prices.get(symbols[0])!.keys()].sort().reverse():[marketDate];
 const date=candidateDates.find(day=>day>anchor&&symbols.every(symbol=>prices.get(symbol)!.has(day)));
 if(!date)return null;
 return {date,portfolios:portfolios.map(portfolio=>{
  const positions=portfolio.positions.map(position=>{
   const series=prices.get(position.symbol)!;
   return {...position,markedValue:position.value*series.get(date)!/series.get(anchor)!};
  });
  const value=positions.reduce((sum,position)=>sum+position.markedValue,portfolio.cash);
  return {variant:portfolio.variant,value,positions};
 })};
}
