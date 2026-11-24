export type HistoryPoint={period:string;balanceDate:string|null;incomeDate:string|null;cashflowDate:string|null;indicatorDate:string|null;assets:number|null;liabilities:number|null;currentAssets:number|null;receivables:number|null;inventory:number|null;cash:number|null;revenue:number|null;profit:number|null;operatingProfit:number|null;operatingCash:number|null;roe:number|null;roa:number|null;ncav:number|null;leverage:number|null};
export type FinancialKind="balancesheet"|"income"|"cashflow"|"fina_indicator";
export const financialKinds:FinancialKind[]=["balancesheet","income","cashflow","fina_indicator"];
export type FinancialRecord=Record<string,unknown>;
export function reportDate(v:unknown):string|null{
  if(typeof v!=="string"||!/^\d{8}$/.test(v))return null;
  const iso=v.slice(0,4)+"-"+v.slice(4,6)+"-"+v.slice(6);
  const d=new Date(iso+"T00:00:00Z");
  return Number.isFinite(d.getTime())&&d.toISOString().slice(0,10)===iso?iso:null;
}
function n(v:unknown):number|null{
  if(v===null||v===undefined||v===""||typeof v==="boolean")return null;
  const x=Number(v);return Number.isFinite(x)?x:null;
}
export function financialHistory(records:Partial<Record<FinancialKind,FinancialRecord[]>>,asof:string):HistoryPoint[]{
  const periods=new Map<string,Partial<Record<FinancialKind,{date:string;row:FinancialRecord|null}>>>();
  for(const kind of financialKinds)for(const row of records[kind]??[]){
    // Standard consolidated statements only. Avoid merging parent-only or single-quarter reports.
    if(kind!=="fina_indicator"&&String(row.report_type)!=="1")continue;
    const period=reportDate(row.end_date),date=kind==="fina_indicator"?reportDate(row.ann_date):reportDate(row.f_ann_date);
    if(!period||!date||date>asof||period>date||!["03-31","06-30","09-30","12-31"].includes(period.slice(5)))continue;
    const entry=periods.get(period)??{};
    const old=entry[kind];
    if(!old||date>old.date)entry[kind]={date,row};
    else if(date===old.date&&old.row&&JSON.stringify(old.row)!==JSON.stringify(row))entry[kind]={date,row:null};
    periods.set(period,entry);
  }
  return [...periods.entries()].sort((a,b)=>a[0].localeCompare(b[0])).map(([period,k])=>{
    const b=k.balancesheet?.row,i=k.income?.row,c=k.cashflow?.row,f=k.fina_indicator?.row;
    const assets=n(b?.total_assets),liabilities=n(b?.total_liab),currentAssets=n(b?.total_cur_assets);
    return {period,balanceDate:b?k.balancesheet!.date:null,incomeDate:i?k.income!.date:null,cashflowDate:c?k.cashflow!.date:null,indicatorDate:f?k.fina_indicator!.date:null,
      assets,liabilities,currentAssets,receivables:n(b?.accounts_receiv),inventory:n(b?.inventories),cash:n(b?.money_cap),
      revenue:n(i?.revenue),profit:n(i?.n_income),operatingProfit:n(i?.operate_profit),operatingCash:n(c?.n_cashflow_act),roe:n(f?.roe),roa:n(f?.roa),
      ncav:currentAssets!==null&&liabilities!==null?currentAssets-liabilities:null,leverage:assets!==null&&assets>0&&liabilities!==null?liabilities/assets:null};
  });
}
export function industryDistribution(rows:{industry:string|null}[]){
  const counts=new Map<string,number>();
  for(const r of rows){const k=r.industry||"未分类";counts.set(k,(counts.get(k)??0)+1);}
  return [...counts].map(([name,count])=>({name,count})).sort((a,b)=>b.count-a.count||a.name.localeCompare(b.name,"zh-CN"));
}
