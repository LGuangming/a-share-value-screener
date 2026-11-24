export type FilterBounds={maxPrice:number|null;maxPb:number|null;includeMissingPb:boolean};
type Row={price:number|null;pb:number|null;is_candidate:boolean;ncav_coverage:number|null;stress_coverage:number|null;status:string};
export function parseBound(value:string,allowNegative=false):number|null{
  if(value.trim()==="")return null;
  const n=Number(value);
  return Number.isFinite(n)&&(allowNegative||n>=0)?n:null;
}
export function initialBound(value:string|undefined,fallback:number,allowNegative=false):string{
  if(value===undefined)return String(fallback);
  if(value===""||value==="all")return "";
  const parsed=parseBound(value,allowNegative);
  return parsed===null?String(fallback):String(parsed);
}
export function matchesBounds(row:Pick<Row,"price"|"pb">,bounds:FilterBounds):boolean{
  if(bounds.maxPrice!==null&&(row.price===null||row.price>bounds.maxPrice))return false;
  if(row.pb===null)return bounds.includeMissingPb;
  return bounds.maxPb===null||row.pb<=bounds.maxPb;
}
export function passesStress(row:Row,minCoverage:number):boolean{
  return row.is_candidate&&row.ncav_coverage!==null&&row.ncav_coverage>=minCoverage&&row.stress_coverage!==null&&row.stress_coverage>=1;
}
export function screenCounts(rows:Row[],minCoverage:number){
  const candidates=rows.filter(r=>r.is_candidate&&r.ncav_coverage!==null&&r.ncav_coverage>=minCoverage);
  return {total:rows.length,candidates:candidates.length,stress:rows.filter(r=>passesStress(r,minCoverage)).length,
    gaps:rows.filter(r=>r.status==="DATA_GAP"||r.status==="STALE_FINANCIALS").length,
    ignored:rows.filter(r=>r.status==="FINANCIAL_IGNORED").length};
}
