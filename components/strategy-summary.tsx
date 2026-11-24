"use client";
import {useMemo,useState} from "react";
import Link from "next/link";
import {ArrowUpRight,Download,Search} from "lucide-react";
import {filterStrategyResults,type SummaryMode,type SummaryGroup} from "@/lib/strategy-summary";
import {fixed,moneyCny} from "@/lib/format";
import {Empty} from "@/components/empty";
import {industryDistribution} from "@/lib/research-analysis";
import {useCompareSelection,CompareCheckbox,CompareToolbar} from "@/components/compare-selection";
import {WatchButton} from "@/components/workspace-preferences";
import {PreviewButton} from "@/components/stock-preview";

export default function StrategySummary({groups,day}:{groups:SummaryGroup[];day:string|null}){
  const [selected,setSelected]=useState(groups.map(g=>g.id));
  const [query,setQuery]=useState("");
  const [limit,setLimit]=useState(100);
  const [mode,setMode]=useState<SummaryMode>("union");
  const [minimum,setMinimum]=useState(2);
  const {codes,toggle,clear}=useCompareSelection();
  const selection=useMemo(()=>filterStrategyResults(groups,selected,mode,minimum),[groups,selected,mode,minimum]);
  const combined=selection.rows;
  const needle=query.trim().toLocaleLowerCase();
  const rows=useMemo(()=>combined.filter(r=>!needle||r.ts_code.toLowerCase().includes(needle)||r.name.toLocaleLowerCase().includes(needle)),[combined,needle]);
  const industries=useMemo(()=>industryDistribution(rows),[rows]);
  const active=groups.filter(g=>selected.includes(g.id));
  const ready=active.filter(g=>g.state==="ready");
  const unavailable=active.length-ready.length;
  function update(ids:string[]){setSelected(ids);setLimit(100);clear();}
  function exportRows(){
    const cell=(v:unknown)=>{const s=String(v??"");return '"'+(/^[=+@\t\r\n-]/.test(s)?"'"+s:s).replace(/"/g,'""')+'"';};
    const csv=[["证券代码","名称","行业","收盘价（元）","总市值（元）","命中策略数","命中策略","筛选日期"],...rows.map(r=>[r.ts_code,r.name,r.industry,r.price,r.market_cap_yuan,r.hits.length,r.hits.map(h=>h.name).join(" / "),day])].map(r=>r.map(cell).join(",")).join("\r\n");
    const url=URL.createObjectURL(new Blob(["\ufeff"+csv],{type:"text/csv;charset=utf-8"}));
    const a=document.createElement("a");a.href=url;a.download="value-radar-strategy-summary.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <section className="panel market-panel strategy-summary-panel">
    <div className="panel-heading"><div><h2>策略汇总 <span className="count-badge">{combined.length}</span></h2><p>{day??"等待数据发布"} · 已选 {active.length}/{groups.length} 个策略 · 已发布 {ready.length} 个{unavailable>0?" · "+unavailable+" 个暂无可用结果":""}</p></div></div>
    <div className="summary-selection">
      <div className="summary-select-heading"><strong>纳入策略</strong><div><button type="button" onClick={()=>update(groups.map(g=>g.id))}>全选</button><button type="button" onClick={()=>update([])}>清空</button></div></div>
      <div className="summary-checkboxes">{groups.map(g=><label key={g.id} className="summary-choice"><input type="checkbox" checked={selected.includes(g.id)} onChange={e=>update(e.target.checked?[...selected,g.id]:selected.filter(id=>id!==g.id))}/><span>{g.name}</span><small>{g.state==="ready"?g.rows.filter(r=>r.is_candidate).length+" 只":g.state==="error"?"读取失败":"未发布"}</small></label>)}</div>
    </div>
    <div className="v4-summary-mode"><div className="tabs" role="group" aria-label="汇总方式">{([["union","并集"],["intersection","交集"],["minimum","至少命中N个"]] as const).map(([v,label])=><button key={v} type="button" className={"tab"+(mode===v?" active":"")} aria-pressed={mode===v} onClick={()=>{setMode(v);setLimit(100);clear();}}>{label}</button>)}</div>{mode==="minimum"&&<label>最低命中 <select value={minimum} onChange={e=>{setMinimum(Number(e.target.value));setLimit(100);clear();}}>{Array.from({length:Math.max(1,groups.length)},(_,i)=><option key={i+1} value={i+1}>{i+1} 个</option>)}</select></label>}{!selection.complete&&<span role="status" className="muted">所选策略尚未全部发布，无法确认结果</span>}</div>
    <div className="summary-toolbar"><div className="search-field"><Search size={16}/><input type="search" aria-label="搜索汇总股票" placeholder="搜索名称 / 代码" value={query} onChange={e=>{setQuery(e.target.value);setLimit(100);}}/></div><CompareToolbar codes={codes} clear={clear}/><span className="muted">{mode==="union"?"并集":mode==="intersection"?"交集":"至少命中 "+minimum+" 个"} · {rows.length} 只</span><button type="button" className="tool-button" disabled={!rows.length} onClick={exportRows}><Download size={14}/> 导出 CSV</button></div>
    {rows.length?<div className="table-scroll"><table className="results-table summary-results"><thead><tr><th className="security-col">股票 / 行业</th><th>收盘价</th><th>总市值</th><th>命中策略数</th><th>命中策略</th><th>筛选日期</th><th>操作</th></tr></thead><tbody>{rows.slice(0,limit).map(r=><tr key={r.ts_code}><td className="security-col"><div className="v4-security-compare"><CompareCheckbox code={r.ts_code} name={r.name} codes={codes} toggle={toggle}/><div><Link href={"/stocks/"+encodeURIComponent(r.ts_code)} className="stock-name">{r.name}<ArrowUpRight size={13}/></Link><div className="stock-meta">{r.ts_code} · {r.industry||"—"}</div></div></div></td><td>{fixed(r.price)}</td><td>{moneyCny(r.market_cap_yuan)}</td><td>{r.hits.length}</td><td><div className="summary-hit-list">{r.hits.map(h=><span key={h.id}>{h.name}</span>)}</div></td><td>{day??"—"}</td><td><div className="row-actions"><WatchButton stock={r}/><PreviewButton stock={r}/></div></td></tr>)}</tbody></table></div>:<Empty title={!selection.complete?"汇总结果待完整发布":!selected.length?"请选择需要汇总的策略":!ready.length?"所选策略暂无可用结果":needle?"暂无匹配股票":"暂无候选股票"} detail={!selection.complete?"所选策略结果尚未全部发布。":mode==="union"&&unavailable>0?"仅汇总已发布策略。":"当前汇总条件下没有股票。"}/>}
    {rows.length>0&&<details className="v4-industry"><summary>行业分布 · {industries.length} 个行业</summary><div className="v4-industry-list">{industries.map(x=><div key={x.name}><span>{x.name}</span><div className="v4-industry-track"><i style={{width:(x.count/rows.length*100)+"%"}}/></div><span>{x.count}只 · {(x.count/rows.length*100).toFixed(1)}%</span></div>)}</div></details>}
    {rows.length>limit&&<button type="button" className="filter-button screener-load-more" onClick={()=>setLimit(n=>n+100)}>再显示100条（已显示{limit} / {rows.length}）</button>}
  </section>;
}
