"use client";
import {useEffect,useMemo,useState,useRef} from "react";
import Link from "next/link";
import {useDismissOnOutside} from "@/components/use-dismiss-on-outside";
import {X,ArrowUpRight,Search} from "lucide-react";
import type {WorkspaceDetail,WorkspaceStock} from "@/lib/workspace-types";
import {fixed,pct,moneyCny,signalLabel,stateLabel} from "@/lib/format";
import {evaluateFundamentalReview,severityLabel} from "@/lib/review";
type Entry={code:string;detail?:WorkspaceDetail;error?:string};
type CandidateGroup={id:string;name:string;error:string|null;count:number};
const PAGE_SIZE=20;
export default function StockComparison({initialCodes,stocks,groups,day,directoryError}:{initialCodes:string[];stocks:WorkspaceStock[];groups:CandidateGroup[];day:string|null;directoryError:string|null}){
  const industryMenu=useRef<HTMLDetailsElement>(null);
  useDismissOnOutside(industryMenu,reason=>{
    const menu=industryMenu.current;
    if(!menu?.open)return;
    menu.open=false;
    if(reason==="escape")menu.querySelector("summary")?.focus();
  });
  const [scope,setScope]=useState("all"),[industries,setIndustries]=useState<string[]>([]),[page,setPage]=useState(1);
  const industryOptions=useMemo(()=>[...new Set(stocks.map(s=>s.industry||"未分类"))].sort((a,b)=>a.localeCompare(b,"zh-CN")),[stocks]);
  const identities=useMemo(()=>new Map(stocks.map(s=>[s.ts_code,s])),[stocks]);
  const [codes,setCodes]=useState(initialCodes),[query,setQuery]=useState("");
  const [results,setResults]=useState<Entry[]>([]);
  const filtered=useMemo(()=>{
    const q=query.trim().toLowerCase();
    return stocks.filter(s=>(scope==="all"||(scope==="candidates"?!!s.hits?.length:s.hits?.some(h=>h.id===scope)))&&
      (!industries.length||industries.includes(s.industry||"未分类"))&&
      (!q||s.name.toLowerCase().includes(q)||s.ts_code.toLowerCase().includes(q)));
  },[stocks,scope,industries,query]);
  const pageCount=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE)),currentPage=Math.min(page,pageCount);
  const visible=filtered.slice((currentPage-1)*PAGE_SIZE,currentPage*PAGE_SIZE);
  const scopeError=scope==="all"?directoryError:scope==="candidates"?groups.filter(g=>g.error).map(g=>g.name+"："+g.error).join("；"):groups.find(g=>g.id===scope)?.error;
  const key=codes.join(",");
  useEffect(()=>{const controller=new AbortController();const list=key?key.split(","):[];
    setResults([]);Promise.all(list.map(async code=>{try{const r=await fetch("/api/research/"+encodeURIComponent(code),{signal:controller.signal});const d=await r.json();if(!r.ok||d.identity?.ts_code!==code)throw Error(d.error||"读取失败");return {code,detail:d as WorkspaceDetail};}catch{return {code,error:"研究数据暂不可用"};}})).then(r=>{if(!controller.signal.aborted)setResults(r);});return()=>controller.abort();},[key]);
  function update(next:string[]){setCodes(next);const url=new URL(location.href);if(next.length)url.searchParams.set("codes",next.join(","));else url.searchParams.delete("codes");window.history.replaceState(window.history.state,"",url);}
  function toggle(code:string){if(codes.includes(code))update(codes.filter(c=>c!==code));else if(codes.length<5)update([...codes,code]);}
  function changeIndustry(industry:string){setIndustries(previous=>previous.includes(industry)?previous.filter(i=>i!==industry):[...previous,industry]);setPage(1);}
  const metrics:{name:string;value:(d:WorkspaceDetail)=>string}[]=[
    {name:"所属行业",value:d=>d.identity.industry??"—"},
    {name:"筛选日期",value:d=>d.stock?.trade_date??"—"},
    {name:"利润表报告期",value:d=>d.overview.income?.period??"—"},
    {name:"利润表披露日",value:d=>d.overview.income?.disclosed??"—"},
    {name:"最近收盘价",value:d=>fixed(d.prices.at(-1)?.close??d.overview.market?.close??null)},
    {name:"行情日期",value:d=>d.prices.at(-1)?.day??d.overview.market?.day??"—"},
    {name:"总市值",value:d=>moneyCny(d.overview.market?.marketCap??null)},
    {name:"市净率",value:d=>fixed(d.overview.market?.pb??null)},
    {name:"Net-Net NCAV",value:d=>moneyCny(d.stock?.ncav_yuan??null)},
    {name:"Net-Net NCAV覆盖率",value:d=>pct(d.stock?.ncav_coverage??null,1)},
    {name:"Net-Net 压力覆盖率",value:d=>pct(d.stock?.stress_coverage??null,1)},
    {name:"资产负债率",value:d=>pct(d.overview.balance?.leverage??null,1)},
    {name:"累计合并净利润",value:d=>moneyCny(d.overview.income?.profit??null)},
    {name:"累计经营现金流",value:d=>moneyCny(d.overview.cashflow?.operatingCash??null)},
    {name:"Net-Net 筛选状态",value:d=>d.stock?stateLabel(d.stock.status):"—"},
    {name:"Net-Net 交易观察",value:d=>d.stock?signalLabel(d.stock.signal):"—"},
    {name:"Net-Net 风险等级",value:d=>d.stock?severityLabel[evaluateFundamentalReview(d.stock).severity]:"—"},
    {name:"Net-Net 主要风险",value:d=>d.stock?evaluateFundamentalReview(d.stock).reasons.map(r=>r.label).join("；")||"暂无重点事项":"—"}
  ];
  return <section className="panel"><div className="panel-heading"><div><h2>股票对比</h2><p>已选择 {codes.length}/5 只股票</p></div><button className="tool-button" onClick={()=>update([])} disabled={!codes.length}>清空</button></div>
    <div className="compare-picker">
      <div className="compare-picker-toolbar">
        <label className="compare-picker-field"><span>股票范围</span><select aria-label="股票范围" value={scope} onChange={e=>{setScope(e.target.value);setPage(1);}}>
          <option value="all">全部股票</option><option value="candidates">全部策略候选</option>
          {groups.map(g=><option key={g.id} value={g.id}>{g.name}（{g.error?"暂不可用":g.count+"只"}）</option>)}
        </select></label>
        <div className="compare-picker-field"><span>所属行业</span><details ref={industryMenu} className="compare-industry-menu"><summary>{industries.length?"已选 "+industries.length+" 个行业":"全部行业"}</summary><div className="compare-industry-options">
          <button type="button" className="tool-button" onClick={()=>{setIndustries([]);setPage(1);}}>清除行业筛选</button>
          {industryOptions.map(i=><label key={i}><input type="checkbox" checked={industries.includes(i)} onChange={()=>changeIndustry(i)}/>{i}</label>)}
        </div></details></div>
        <div className="compare-picker-field compare-picker-query"><span>股票搜索</span><div className="search-field"><Search size={15}/><input value={query} maxLength={40} onChange={e=>{setQuery(e.target.value);setPage(1);}} placeholder="股票名称 / 代码" aria-label="搜索候选股票"/></div></div>
      </div>
      {codes.length>0&&<div className="compare-picked" aria-label="已选股票">{codes.map(code=><button key={code} type="button" className="tool-button" onClick={()=>toggle(code)} aria-label={"移除 "+code}>{identities.get(code)?.name??code}<X size={13}/></button>)}</div>}
      <div className="compare-picker-meta"><span>共 {filtered.length} 只{scope!=="all"&&day?" · "+day:""}</span><span>{codes.length>=5?"已达到5只上限":"可跨筛选条件多选"}</span></div>
      {scopeError&&<p role="status" className="muted">{scopeError}</p>}
      <div className="table-scroll compare-picker-scroll"><table className="results-table compare-picker-table"><thead><tr><th>选择</th><th>股票</th><th>所属行业</th><th>命中策略</th></tr></thead><tbody>
        {visible.map(s=>{const checked=codes.includes(s.ts_code);return <tr key={s.ts_code} className={checked?"compare-picked-row":undefined}><td><input type="checkbox" className="v4-compare-checkbox" aria-label={"选择 "+s.name+" "+s.ts_code} checked={checked} disabled={!checked&&codes.length>=5} onChange={()=>toggle(s.ts_code)}/></td><td><span className="strong">{s.name}</span><small>{s.ts_code}</small></td><td>{s.industry||"未分类"}</td><td>{s.hits?.length?<div className="compare-strategy-hits">{s.hits.map(h=><span className="compare-strategy-chip" data-strategy={h.id} key={h.id}>{h.name}</span>)}</div>:"—"}</td></tr>;})}
        {!visible.length&&<tr><td colSpan={4} className="muted">{scopeError?"当前范围暂不可用":"暂无符合条件的股票"}</td></tr>}
      </tbody></table></div>
      <div className="compare-picker-pagination"><button type="button" className="tool-button" disabled={currentPage<=1} onClick={()=>setPage(currentPage-1)}>上一页</button><span>{currentPage} / {pageCount}</span><button type="button" className="tool-button" disabled={currentPage>=pageCount} onClick={()=>setPage(currentPage+1)}>下一页</button></div>
    </div>
    {codes.length?<div className="table-scroll"><table className="results-table v4-compare-table"><thead><tr><th>指标</th>{codes.map(code=>{const d=results.find(r=>r.code===code);return <th key={code}><div className="v4-compare-heading"><Link className="stock-name" href={"/research?code="+encodeURIComponent(code)}>{d?.detail?.identity.name??code}<ArrowUpRight size={12}/></Link><button className="watch-button" aria-label={"移除"+code} onClick={()=>update(codes.filter(c=>c!==code))}><X size={14}/></button></div><small>{code}</small>{d?.error&&<p>{d.error}</p>}</th>;})}</tr></thead><tbody>{metrics.map(m=><tr key={m.name}><th scope="row">{m.name}</th>{codes.map(code=>{const r=results.find(x=>x.code===code);return <td key={code}>{r?.detail?m.value(r.detail):r?"—":"…"}</td>;})}</tr>)}</tbody></table></div>:<p className="muted">请添加股票开始对比。</p>}
  </section>;
}
