export type SummaryStock = {ts_code:string;name:string;industry:string|null;price:number|null;market_cap_yuan:number|null;is_candidate:boolean};
export type SummaryGroup = {id:string;name:string;state:"ready"|"unpublished"|"error";rows:SummaryStock[]};
export type SummaryRow = SummaryStock & {hits:{id:string;name:string}[]};
export function combineStrategyResults(groups:SummaryGroup[],selected:string[]):SummaryRow[]{
  const included=new Set(selected);
  const byCode=new Map<string,SummaryRow>();
  for(const group of groups){
    if(group.state!=="ready"||!included.has(group.id))continue;
    for(const row of group.rows){
      if(!row.is_candidate)continue;
      let entry=byCode.get(row.ts_code);
      if(!entry){entry={...row,hits:[]};byCode.set(row.ts_code,entry);}
      if(!entry.hits.some(h=>h.id===group.id))entry.hits.push({id:group.id,name:group.name});
    }
  }
  return [...byCode.values()].sort((a,b)=>b.hits.length-a.hits.length||a.ts_code.localeCompare(b.ts_code));
}
