"use client";
import {useEffect,useMemo,useState,type CSSProperties} from "react";
import Link from "next/link";
import {ArrowUpRight,PanelLeftClose,PanelLeftOpen,PanelRightClose,PanelRightOpen,Search} from "lucide-react";
import MarketChart from "@/components/market-chart";
import {useResearchDetail,useStockSearch} from "@/components/research-data";
import {WatchButton,useWatchlist} from "@/components/workspace-preferences";
import {fixed,moneyCny,pct,signalLabel,stateLabel} from "@/lib/format";
import type {WorkspaceStock,WorkspaceDetail} from "@/lib/workspace-types";

export function ResearchMetrics({detail,hits}:{detail:WorkspaceDetail;hits?:{id:string;name:string}[]}){
  const s=detail.stock;
  const o=detail.overview,m=o.market,b=o.balance,i=o.income,c=o.cashflow;
  return <div className="workspace-metrics">
    <div className="workspace-security"><div><strong>{detail.identity.name}</strong><small>{detail.identity.ts_code} · {detail.identity.industry||"—"}</small></div><WatchButton stock={detail.identity}/></div>
    <div className="workspace-price">{fixed(detail.prices.at(-1)?.close??m?.close??null)}<small>元 · {detail.prices.at(-1)?.day??m?.day??"—"}</small></div>
    {hits!==undefined&&<><h3>命中策略</h3><div className="summary-hit-list">{hits.length?hits.map(h=><span key={h.id}>{h.name}</span>):<span className="muted">当前候选池未命中</span>}</div></>}
    <h3>通用估值与财务</h3><dl className="workspace-dl">{[["总市值",moneyCny(m?.marketCap??null)],["市净率",fixed(m?.pb??null)],["换手率",pct(m?.turnover??null,2)],["估值日期",m?.day??"—"],["资产负债率",pct(b?.leverage??null,1)],["营业收入",moneyCny(i?.revenue??null)],["归母净利润",moneyCny(i?.parentProfit??null)],["经营现金流",moneyCny(c?.operatingCash??null)],["利润表报告期",i?.period??"—"],["现金流报告期",c?.period??"—"]].map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    {s&&<details><summary>Net-Net 策略摘要</summary><dl className="workspace-dl">{[["筛选日期",s.trade_date],["NCAV / 市值",pct(s.ncav_coverage,1)],["压力覆盖率",pct(s.stress_coverage,1)],["筛选状态",stateLabel(s.status)],["交易观察",signalLabel(s.signal)]].map(([k,v])=><div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl></details>}
    <Link className="inline-link" href={"/stocks/"+encodeURIComponent(detail.identity.ts_code)}>完整个股研究 <ArrowUpRight size={13}/></Link>
  </div>;
}
export default function ResearchWorkspace({candidates,initialCode,day}:{candidates:WorkspaceStock[];initialCode?:string;day:string|null}){
  const {items}=useWatchlist();
  const [mode,setMode]=useState<"candidates"|"watch">("candidates");
  const [code,setCode]=useState<string|null>(initialCode??candidates[0]?.ts_code??null);
  const [q,setQ]=useState("");
  useEffect(()=>{if(initialCode)setCode(initialCode);},[initialCode]);
  const [left,setLeft]=useState(true),[right,setRight]=useState(true);
  const [leftWidth,setLeftWidth]=useState(220),[rightWidth,setRightWidth]=useState(270);
  const [bottom,setBottom]=useState(false);
  const lookup=useStockSearch(q,!!q.trim());
  const {detail,error,loading}=useResearchDetail(code);
  const base=mode==="watch"?items:candidates;
  const list=q.trim()?lookup.stocks:base;
  const hits=candidates.find(c=>c.ts_code===code)?.hits??[];
  function choose(next:string){setCode(next);const url=new URL(location.href);url.searchParams.set("code",next);window.history.replaceState(window.history.state,"",url);}
  useEffect(()=>{const sync=()=>{const next=new URLSearchParams(location.search).get("code");if(next&&/^\d{6}\.(SH|SZ|BJ)$/.test(next))setCode(next);};window.addEventListener("popstate",sync);return()=>window.removeEventListener("popstate",sync);},[]);
  return <div className="workspace-shell">
    <div className="workspace-toolbar"><button type="button" className="tool-button" onClick={()=>setLeft(v=>!v)}>{left?<PanelLeftClose size={15}/>:<PanelLeftOpen size={15}/>}股票池</button><span>{day??"暂无筛选日期"}</span><div className="workspace-widths">{left&&<label>左栏 <input type="range" min={180} max={300} step={10} value={leftWidth} onChange={e=>setLeftWidth(+e.target.value)}/></label>}{right&&<label>右栏 <input type="range" min={220} max={340} step={10} value={rightWidth} onChange={e=>setRightWidth(+e.target.value)}/></label>}</div><button type="button" className="tool-button" onClick={()=>setRight(v=>!v)}>研究摘要 {right?<PanelRightClose size={15}/>:<PanelRightOpen size={15}/>}</button></div>
    <div className="workspace-grid" style={{"--work-left":left?leftWidth+"px":"0px","--work-right":right?rightWidth+"px":"0px"} as CSSProperties}>
      {left&&<aside className="workspace-side workspace-left"><div className="workspace-side-tabs"><button type="button" aria-pressed={mode==="candidates"} onClick={()=>{setMode("candidates");setQ("");}}>候选池 {candidates.length}</button><button type="button" aria-pressed={mode==="watch"} onClick={()=>{setMode("watch");setQ("");}}>自选 {items.length}</button></div><div className="search-field"><Search size={15}/><input aria-label="搜索全市场股票" maxLength={40} value={q} onChange={e=>setQ(e.target.value)} placeholder="名称 / 代码"/></div>{lookup.loading&&<p className="muted">搜索中…</p>}{lookup.error&&<p role="status">{lookup.error}</p>}<div className="workspace-stock-list">{list.map(s=><div key={s.ts_code} className={"workspace-stock"+(s.ts_code===code?" active":"")}><button type="button" onClick={()=>choose(s.ts_code)}><strong>{s.name}</strong><small>{s.ts_code}</small></button><WatchButton stock={s}/></div>)}{!list.length&&!lookup.loading&&<p className="muted">暂无股票</p>}</div></aside>}
      <section className="workspace-main">{loading?<div className="workspace-loading" role="status">正在读取个股行情…</div>:error?<div className="workspace-loading" role="alert">{error}</div>:detail?<><div className="workspace-chart-title"><strong>{detail.identity.name} <small>{detail.identity.ts_code}</small></strong><Link className="inline-link" href={"/stocks/"+encodeURIComponent(detail.identity.ts_code)}>个股详情 <ArrowUpRight size={14}/></Link></div><MarketChart key={detail.identity.ts_code} code={detail.identity.ts_code} name={detail.identity.name} prices={detail.prices}/></>:<div className="workspace-loading">选择候选股、自选股或搜索证券</div>}</section>
      {right&&<aside className="workspace-side workspace-right">{detail?<ResearchMetrics detail={detail} hits={hits}/>:<p className="muted">暂无研究摘要</p>}</aside>}
    </div>
    <section className="workspace-bottom"><button type="button" aria-expanded={bottom} onClick={()=>setBottom(v=>!v)}>Net-Net 历史观察 {bottom?"−":"+"}</button>{bottom&&<div className="table-scroll"><table className="results-table"><thead><tr><th>交易日</th><th>收盘价</th><th>NCAV覆盖率</th><th>信号</th></tr></thead><tbody>{detail?.recent.map(r=><tr key={r.trade_date}><td>{r.trade_date}</td><td>{fixed(r.price)}</td><td>{pct(r.ncav_coverage,1)}</td><td>{signalLabel(r.signal)}</td></tr>)}</tbody></table>{!detail?.recent.length&&<p className="muted">暂无观察记录</p>}</div>}</section>
  </div>;
}
