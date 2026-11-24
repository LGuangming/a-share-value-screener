"use client";
import {useEffect,useState} from "react";
import {moneyCny,pct,fixed} from "@/lib/format";
import type {HistoryPoint,FinancialKind} from "@/lib/research-analysis";
type Metric="assets"|"liabilities"|"currentAssets"|"receivables"|"inventory"|"cash"|"revenue"|"profit"|"operatingProfit"|"operatingCash"|"roe"|"roa"|"ncav"|"leverage";
const names:Record<Metric,string>={assets:"总资产",liabilities:"总负债",currentAssets:"流动资产",receivables:"应收账款",inventory:"存货",cash:"货币资金",revenue:"营业收入",profit:"合并净利润",operatingProfit:"营业利润",operatingCash:"经营现金流",roe:"ROE",roa:"ROA",ncav:"NCAV",leverage:"资产负债率"};
const show=(p:HistoryPoint,k:Metric)=>k==="leverage"?pct(p[k],1):k==="roe"||k==="roa"?p[k]===null?"—":fixed(p[k],2)+"%":moneyCny(p[k]);
const colors=["#D04A02","#8293a6","#237551"];
function Trend({title,points,metrics}:{title:string;points:HistoryPoint[];metrics:Metric[]}){
  const values=points.flatMap(p=>metrics.flatMap(k=>p[k]===null?[]:[p[k] as number]));
  if(!values.length)return <div className="v4-fin-chart"><h3>{title}</h3><p className="muted">暂无数据</p></div>;
  const max=Math.max(0,...values),min=Math.min(0,...values),range=max-min||1;
  const y=(n:number)=>18+(max-n)/range*112;
  const width=300,step=width/Math.max(1,points.length),bar=Math.min(22,step/(metrics.length+1));
  return <div className="v4-fin-chart"><h3>{title}</h3><div className="v4-chart-unit">人民币亿元</div>
    <svg viewBox="0 0 360 165" role="img" aria-label={title}>
      {[0,.25,.5,.75,1].map(f=><g key={f}><line x1="50" x2="350" y1={y(min+range*f)} y2={y(min+range*f)} stroke="currentColor" opacity=".1"/><text x="45" y={y(min+range*f)+3} textAnchor="end" fontSize="8" fill="currentColor">{((min+range*f)/1e8).toFixed(1)}</text></g>)}
      {points.map((p,i)=><g key={p.period}>{metrics.map((k,j)=>{const v=p[k];return v===null?null:<rect key={k} x={50+i*step+step/2-metrics.length*bar/2+j*bar} y={Math.min(y(v),y(0))} width={Math.max(1,bar-1)} height={Math.max(.5,Math.abs(y(v)-y(0)))} fill={colors[j]}><title>{p.period+" "+names[k]+": "+show(p,k)}</title></rect>;})}{(points.length<=8||i%Math.ceil(points.length/6)===0)&&<text x={50+i*step+step/2} y="149" textAnchor="middle" fontSize="8" fill="currentColor">{p.period.slice(0,7)}</text>}</g>)}
    </svg><div className="v4-chart-legend">{metrics.map((k,i)=><span key={k}><i style={{background:colors[i]}}/>{names[k]}</span>)}</div>
  </div>;
}
export default function FinancialHistory({code}:{code:string}){
  const [loaded,setLoaded]=useState(false);
  const [mode,setMode]=useState<"annual"|"quarter">("annual");
  const [kind,setKind]=useState<"overview"|"balance"|"income"|"cashflow">("overview");
  const [state,setState]=useState<{points:HistoryPoint[];sources:{kind:FinancialKind;state:string;rows?:number}[]}|null>(null);
  const [error,setError]=useState("");
  useEffect(()=>{if(!loaded)return;const controller=new AbortController();fetch("/api/research/"+encodeURIComponent(code)+"/financial",{signal:controller.signal}).then(async r=>{const data=await r.json();if(!r.ok||data.code!==code)throw Error(data.error||"历史财务读取失败");return data;}).then(d=>{if(!controller.signal.aborted)setState(d);}).catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:"读取失败");});return()=>controller.abort();},[code,loaded]);
  const points=(state?.points??[]).filter(p=>mode==="quarter"||p.period.endsWith("-12-31"));
  const metrics:Metric[]=kind==="balance"?["assets","liabilities","currentAssets","receivables","inventory","cash","ncav","leverage"]:kind==="income"?["revenue","profit","operatingProfit","roe","roa"]:kind==="cashflow"?["operatingCash"]:["revenue","profit","operatingCash","assets","liabilities","ncav","leverage","roe"];
  const sourceNames={balancesheet:"资产负债表",income:"利润表",cashflow:"现金流量表",fina_indicator:"财务指标"};
  return <section className="panel v4-financial"><div className="panel-heading"><div><h2>历史财务分析</h2><p>2018年以来 · 合并报表 · 报告期累计金额</p></div>{!loaded&&<button className="tool-button" onClick={()=>setLoaded(true)}>读取历史财务</button>}</div>
    {loaded&&!state&&!error&&<p className="muted" role="status">正在读取历史财务档案…</p>}{error&&<p role="alert">{error}</p>}
    {state&&<><div className="v4-fin-controls"><div className="tabs">{[["annual","年报"],["quarter","全部报告期"]].map(([v,label])=><button key={v} className={"tab"+(mode===v?" active":"")} onClick={()=>setMode(v as typeof mode)}>{label}</button>)}</div><div className="tabs">{[["overview","概览"],["balance","资产负债表"],["income","利润表"],["cashflow","现金流量表"]].map(([v,label])=><button key={v} className={"tab"+(kind===v?" active":"")} onClick={()=>setKind(v as typeof kind)}>{label}</button>)}</div></div>
    <div className="v4-source-list">{state.sources.map(s=><span key={s.kind}>{sourceNames[s.kind]} · {s.state==="ready"?(s.rows??0)+"条原始记录":s.state==="unpublished"?"未发布":"读取失败"}</span>)}</div>
    {points.length?<><div className="v4-fin-grid">{kind==="overview"?<><Trend title="营业收入与净利润" points={points} metrics={["revenue","profit"]}/><Trend title="经营活动现金流" points={points} metrics={["operatingCash"]}/><Trend title="资产与负债" points={points} metrics={["assets","liabilities"]}/><Trend title="账面净流动资产" points={points} metrics={["ncav"]}/></>:kind==="balance"?<><Trend title="资产负债结构" points={points} metrics={["assets","liabilities"]}/><Trend title="流动资产构成" points={points} metrics={["receivables","inventory","cash"]}/></>:kind==="income"?<Trend title="收入与利润" points={points} metrics={["revenue","profit","operatingProfit"]}/>:<Trend title="经营活动现金流" points={points} metrics={["operatingCash"]}/>}</div>
    <div className="table-scroll"><table className="results-table v4-fin-table"><thead><tr><th>报告期</th>{metrics.map(k=><th key={k}>{names[k]}</th>)}<th>资产负债表披露</th><th>利润表披露</th><th>现金流量表披露</th><th>指标公告</th></tr></thead><tbody>{[...points].reverse().map(p=><tr key={p.period}><td>{p.period}</td>{metrics.map(k=><td key={k}>{show(p,k)}</td>)}<td>{p.balanceDate??"—"}</td><td>{p.incomeDate??"—"}</td><td>{p.cashflowDate??"—"}</td><td>{p.indicatorDate??"—"}</td></tr>)}</tbody></table></div><p className="note">历史档案版本：研究快照 · 非历史时点认证</p></>:<p className="muted">当前报告期暂无可显示的财务记录。</p>}</>}
  </section>;
}
