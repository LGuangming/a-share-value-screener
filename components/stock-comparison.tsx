"use client";
import {useEffect,useState} from "react";
import Link from "next/link";
import {X,ArrowUpRight,Search} from "lucide-react";
import {useStockSearch} from "@/components/research-data";
import type {WorkspaceDetail,StockIdentity} from "@/lib/workspace-types";
import {fixed,pct,moneyCny,signalLabel,stateLabel} from "@/lib/format";
import {evaluateFundamentalReview,severityLabel} from "@/lib/review";
type Entry={code:string;detail?:WorkspaceDetail;error?:string};
export default function StockComparison({initialCodes}:{initialCodes:string[]}){
  const [codes,setCodes]=useState(initialCodes),[query,setQuery]=useState("");
  const [results,setResults]=useState<Entry[]>([]);
  const lookup=useStockSearch(query,!!query.trim());
  const key=codes.join(",");
  useEffect(()=>{const controller=new AbortController();const list=key?key.split(","):[];
    setResults([]);Promise.all(list.map(async code=>{try{const r=await fetch("/api/research/"+encodeURIComponent(code),{signal:controller.signal});const d=await r.json();if(!r.ok||d.identity?.ts_code!==code)throw Error(d.error||"读取失败");return {code,detail:d as WorkspaceDetail};}catch{return {code,error:"研究数据暂不可用"};}})).then(r=>{if(!controller.signal.aborted)setResults(r);});return()=>controller.abort();},[key]);
  function update(next:string[]){setCodes(next);const url=new URL(location.href);if(next.length)url.searchParams.set("codes",next.join(","));else url.searchParams.delete("codes");window.history.replaceState(window.history.state,"",url);}
  function add(s:StockIdentity){if(codes.length<5&&!codes.includes(s.ts_code)){update([...codes,s.ts_code]);setQuery("");}}
  const metrics:{name:string;value:(d:WorkspaceDetail)=>string}[]=[
    {name:"所属行业",value:d=>d.identity.industry??"—"},
    {name:"筛选日期",value:d=>d.stock?.trade_date??"—"},
    {name:"财务报告期",value:d=>d.stock?.report_date??"—"},
    {name:"财报披露日",value:d=>d.stock?.disclosed_at??"—"},
    {name:"最近收盘价",value:d=>fixed(d.prices.at(-1)?.close??d.stock?.price??null)},
    {name:"行情日期",value:d=>d.prices.at(-1)?.day??d.stock?.trade_date??"—"},
    {name:"总市值",value:d=>moneyCny(d.stock?.market_cap_yuan??null)},
    {name:"市净率",value:d=>fixed(d.stock?.pb??null)},
    {name:"NCAV",value:d=>moneyCny(d.stock?.ncav_yuan??null)},
    {name:"NCAV覆盖率",value:d=>pct(d.stock?.ncav_coverage??null,1)},
    {name:"压力NCAV覆盖率",value:d=>pct(d.stock?.stress_coverage??null,1)},
    {name:"资产负债率",value:d=>pct(d.stock?.liability_ratio??null,1)},
    {name:"累计合并净利润",value:d=>moneyCny(d.stock?.net_profit_yuan??null)},
    {name:"累计经营现金流",value:d=>moneyCny(d.stock?.operating_cf_yuan??null)},
    {name:"筛选状态",value:d=>d.stock?stateLabel(d.stock.status):"—"},
    {name:"交易观察",value:d=>d.stock?signalLabel(d.stock.signal):"—"},
    {name:"风险等级",value:d=>d.stock?severityLabel[evaluateFundamentalReview(d.stock).severity]:"—"},
    {name:"主要风险",value:d=>d.stock?evaluateFundamentalReview(d.stock).reasons.map(r=>r.label).join("；")||"暂无重点事项":"—"}
  ];
  return <section className="panel"><div className="panel-heading"><div><h2>股票对比</h2><p>已选择 {codes.length}/5 只股票</p></div><button className="tool-button" onClick={()=>update([])} disabled={!codes.length}>清空</button></div>
    <div className="v4-compare-search"><div className="search-field"><Search size={15}/><input value={query} maxLength={40} onChange={e=>setQuery(e.target.value)} placeholder="搜索股票名称 / 代码" aria-label="添加对比股票"/></div>{query&&<div className="v4-compare-options">{lookup.loading?<p>搜索中…</p>:lookup.error?<p>{lookup.error}</p>:lookup.stocks.length?lookup.stocks.map(s=><button className="tool-button" key={s.ts_code} disabled={codes.includes(s.ts_code)||codes.length>=5} onClick={()=>add(s)}>{s.name} · {s.ts_code}</button>):<p>暂无匹配股票</p>}</div>}</div>
    {codes.length?<div className="table-scroll"><table className="results-table v4-compare-table"><thead><tr><th>指标</th>{codes.map(code=>{const d=results.find(r=>r.code===code);return <th key={code}><div className="v4-compare-heading"><Link className="stock-name" href={"/research?code="+encodeURIComponent(code)}>{d?.detail?.identity.name??code}<ArrowUpRight size={12}/></Link><button className="watch-button" aria-label={"移除"+code} onClick={()=>update(codes.filter(c=>c!==code))}><X size={14}/></button></div><small>{code}</small>{d?.error&&<p>{d.error}</p>}</th>;})}</tr></thead><tbody>{metrics.map(m=><tr key={m.name}><th scope="row">{m.name}</th>{codes.map(code=>{const r=results.find(x=>x.code===code);return <td key={code}>{r?.detail?m.value(r.detail):r?"—":"…"}</td>;})}</tr>)}</tbody></table></div>:<p className="muted">请添加股票开始对比。</p>}
  </section>;
}
