"use client";
import {useMemo,useState} from "react";
import Link from "next/link";
import {ArrowUpRight,Download,Search} from "lucide-react";
import {combineStrategyResults,type SummaryGroup} from "@/lib/strategy-summary";
import {fixed,moneyCny} from "@/lib/format";
import {Empty} from "@/components/empty";
import {WatchButton} from "@/components/workspace-preferences";
import {PreviewButton} from "@/components/stock-preview";

export default function StrategySummary({groups,day}:{groups:SummaryGroup[];day:string|null}){
  const [selected,setSelected]=useState(groups.map(g=>g.id));
  const [query,setQuery]=useState("");
  const [limit,setLimit]=useState(100);
  const combined=useMemo(()=>combineStrategyResults(groups,selected),[groups,selected]);
  const needle=query.trim().toLocaleLowerCase();
  const rows=useMemo(()=>combined.filter(r=>!needle||r.ts_code.toLowerCase().includes(needle)||r.name.toLocaleLowerCase().includes(needle)),[combined,needle]);
  const active=groups.filter(g=>selected.includes(g.id));
  const ready=active.filter(g=>g.state==="ready");
  const unavailable=active.length-ready.length;
  function update(ids:string[]){setSelected(ids);setLimit(100);}
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
    <div className="summary-toolbar"><div className="search-field"><Search size={16}/><input type="search" aria-label="搜索汇总股票" placeholder="搜索名称 / 代码" value={query} onChange={e=>{setQuery(e.target.value);setLimit(100);}}/></div><span className="muted">并集 · 按股票去重 · {rows.length} 只</span><button type="button" className="tool-button" disabled={!rows.length} onClick={exportRows}><Download size={14}/> 导出 CSV</button></div>
    {rows.length?<div className="table-scroll"><table className="results-table summary-results"><thead><tr><th className="security-col">股票 / 行业</th><th>收盘价</th><th>总市值</th><th>命中策略数</th><th>命中策略</th><th>筛选日期</th><th>操作</th></tr></thead><tbody>{rows.slice(0,limit).map(r=><tr key={r.ts_code}><td className="security-col"><Link href={"/stocks/"+encodeURIComponent(r.ts_code)} className="stock-name">{r.name}<ArrowUpRight size={13}/></Link><div className="stock-meta">{r.ts_code} · {r.industry||"—"}</div></td><td>{fixed(r.price)}</td><td>{moneyCny(r.market_cap_yuan)}</td><td>{r.hits.length}</td><td><div className="summary-hit-list">{r.hits.map(h=><span key={h.id}>{h.name}</span>)}</div></td><td>{day??"—"}</td><td><div className="row-actions"><WatchButton stock={r}/><PreviewButton stock={r}/></div></td></tr>)}</tbody></table></div>:<Empty title={!selected.length?"请选择需要汇总的策略":!ready.length?"所选策略暂无可用结果":needle?"暂无匹配股票":"暂无候选股票"} detail={unavailable>0?"未发布或读取失败的策略不参与汇总。":"按已选策略的候选股票合并展示。"}/>}
    {rows.length>limit&&<button type="button" className="filter-button screener-load-more" onClick={()=>setLimit(n=>n+100)}>再显示100条（已显示{limit} / {rows.length}）</button>}
  </section>;
}
