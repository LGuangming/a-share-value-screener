import ResearchWorkspace from "@/components/research-workspace";
import {latestSnapshot,screeningRowsStatus} from "@/lib/data";
import {strategies} from "@/lib/strategies";
import {combineStrategyResults,type SummaryGroup} from "@/lib/strategy-summary";
export const dynamic="force-dynamic";
export const metadata={title:"行情研究"};
export default async function ResearchPage({searchParams}:{searchParams:Promise<{code?:string}>}){
  const {code}=await searchParams;
  const {snapshot}=await latestSnapshot();
  const groups:SummaryGroup[]=snapshot?await Promise.all(strategies.filter(s=>s.status==="live").map(async(s):Promise<SummaryGroup>=>{
    const result=await screeningRowsStatus(snapshot.trade_date,s.id);
    return {id:s.id,name:s.name,state:result.error?"error":"ready",rows:result.rows.filter(r=>r.is_candidate).map(({ts_code,name,industry,price,market_cap_yuan,is_candidate})=>({ts_code,name,industry,price,market_cap_yuan,is_candidate}))};
  })): [];
  const candidates=combineStrategyResults(groups,groups.map(g=>g.id));
  const initialCode=typeof code==="string"&&/^\d{6}\.(SH|SZ|BJ)$/.test(code)?code:undefined;
  return <main className="page-container research-workspace-page"><div className="page-top"><div><h1>行情研究</h1></div></div><ResearchWorkspace candidates={candidates} initialCode={initialCode} day={snapshot?.trade_date??null}/></main>;
}
