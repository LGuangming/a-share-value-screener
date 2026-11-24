import StockComparison from "@/components/stock-comparison";
import {comparisonDirectory,latestSnapshot,screeningRowsStatus} from "@/lib/data";
import {strategies} from "@/lib/strategies";
import type {WorkspaceStock} from "@/lib/workspace-types";
export const metadata={title:"股票对比"};
export default async function ComparePage({searchParams}:{searchParams:Promise<{codes?:string}>}){
  const [{codes},directory,{snapshot,error}]=await Promise.all([searchParams,comparisonDirectory(),latestSnapshot()]);
  const selected=[...new Set((typeof codes==="string"?codes:"").split(",").filter(c=>/^\d{6}\.(SH|SZ|BJ)$/.test(c)))].slice(0,5);
  const groups=await Promise.all(strategies.filter(s=>s.status==="live").map(async s=>{
    const result=snapshot?await screeningRowsStatus(snapshot.trade_date,s.id):{rows:[],error:error??"暂无已发布结果"};
    return {id:s.id,name:s.name,error:result.error,rows:result.rows.filter(r=>r.is_candidate)};
  }));
  const stocks=new Map<string,WorkspaceStock>(directory.stocks.map(s=>[s.ts_code,{...s,hits:[]}]));
  for(const g of groups)for(const r of g.rows){
    const stock=stocks.get(r.ts_code)??{ts_code:r.ts_code,name:r.name,industry:r.industry,hits:[]};
    stock.hits=[...(stock.hits??[]),{id:g.id,name:g.name}];
    stocks.set(r.ts_code,stock);
  }
  return <main className="page-container"><div className="page-top"><div><h1>股票对比</h1></div></div><StockComparison key={selected.join(",")} initialCodes={selected} stocks={Array.from(stocks.values())} groups={groups.map(({id,name,error,rows})=>({id,name,error,count:rows.length}))} day={snapshot?.trade_date??null} directoryError={directory.error}/></main>;
}
