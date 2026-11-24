// Strategy-independent company facts. Amounts in CNY; ratios as decimals.
export type CompanyMarket={day:string;close:number|null;marketCap:number|null;pb:number|null;turnover:number|null;peTtm:number|null;psTtm:number|null;dividendYield:number|null};
export type CompanyReport={period:string;disclosed:string;assets:number|null;liabilities:number|null;equity:number|null;leverage:number|null;cash:number|null;revenue:number|null;profit:number|null;parentProfit:number|null;operatingCash:number|null};
export type CompanyOverview={market:CompanyMarket|null;balance:CompanyReport|null;income:CompanyReport|null;cashflow:CompanyReport|null;marketType:string|null;listDate:string|null;isST:boolean};
export function numberOrNull(v:unknown):number|null{
  if(v===null||v===undefined||typeof v==="boolean"||(typeof v==="string"&&!v.trim())||!["number","string"].includes(typeof v))return null;
  const n=Number(v);return Number.isFinite(n)?n:null;
}
export function companyReport(period:string,disclosed:string,raw:Record<string,unknown>,kind:string):CompanyReport{
  const n=numberOrNull;
  const assets=kind==="balancesheet"?n(raw.total_assets):null,liabilities=kind==="balancesheet"?n(raw.total_liab):null;
  return {period,disclosed,assets,liabilities,equity:assets!==null&&liabilities!==null?assets-liabilities:null,leverage:assets!==null&&assets>0&&liabilities!==null?liabilities/assets:null,cash:kind==="balancesheet"?n(raw.money_cap):null,
    revenue:kind==="income"?n(raw.revenue):null,profit:kind==="income"?n(raw.n_income):null,parentProfit:kind==="income"?n(raw.n_income_attr_p):null,operatingCash:kind==="cashflow"?n(raw.n_cashflow_act):null};
}
export function companyMarket(day:string,row:Record<string,unknown>):CompanyMarket{
  const raw=(row.raw??{}) as Record<string,unknown>,n=numberOrNull;
  const percent=(v:unknown)=>{const x=n(v);return x===null?null:x/100;};
  return {day,close:n(row.close),marketCap:n(row.market_cap_yuan),pb:n(row.pb),turnover:percent(row.turnover_rate),peTtm:n(raw.pe_ttm),psTtm:n(raw.ps_ttm),dividendYield:percent(raw.dv_ttm)};
}
