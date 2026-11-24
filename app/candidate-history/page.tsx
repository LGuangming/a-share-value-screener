import Link from "next/link";
import {publishedCandidateDates,publishedCandidateChanges} from "@/lib/data";
import {fixed,pct} from "@/lib/format";
export const dynamic="force-dynamic";
export const metadata={title:"历史候选变化"};
export default async function CandidateHistory({searchParams}:{searchParams:Promise<{day?:string;previous?:string;change?:string}>}){
  const q=await searchParams;
  let dates:string[]=[];let error="";
  try{dates=await publishedCandidateDates();}catch{error="历史筛选发布记录暂不可用";}
  const day=typeof q.day==="string"&&dates.includes(q.day)?q.day:dates[0];
  const older=dates.filter(d=>d<day);
  const previous=typeof q.previous==="string"&&older.includes(q.previous)?q.previous:older[0];
  let rows:Awaited<ReturnType<typeof publishedCandidateChanges>>=[];
  if(day&&previous)try{rows=await publishedCandidateChanges(day,previous);}catch{error="候选变化读取失败";}
  const filters=[["all","全部"],["added","新增入选"],["removed","移出候选"],["retained","持续入选"]] as const;
  const mode=filters.some(([k])=>k===q.change)?q.change:"all";
  const labels={added:"新增入选",removed:"移出候选",retained:"持续入选"};
  const filtered=rows.filter(r=>mode==="all"||r.change===mode);
  return <main className="page-container"><div className="page-top"><div><h1>历史候选变化</h1><p className="page-lead">Net-Net · 已发布筛选结果对比</p></div><Link className="inline-link" href="/">返回选股中心</Link></div>
    <section className="panel"><form className="history-date-form" method="get"><label>本期<select name="day" defaultValue={day??""} disabled={!dates.length}>{dates.map(d=><option key={d}>{d}</option>)}</select></label><label>对比期<select name="previous" defaultValue={previous??""} disabled={!dates.length}><option value="">上一发布日</option>{dates.map(d=><option key={d}>{d}</option>)}</select></label><button className="filter-button" disabled={dates.length<2}>对比</button></form>
    {error?<p role="alert">{error}</p>:!day||!previous?<p className="muted">暂无两期可对比的发布记录</p>:<><div className="stats-grid">{(["added","retained","removed"] as const).map(k=><div className="stat-card" key={k}><span>{labels[k]}</span><strong>{rows.filter(r=>r.change===k).length}</strong></div>)}</div><div className="tabs history-change-tabs">{filters.map(([k,label])=><Link key={k} className={"tab"+(mode===k?" active":"")} href={"/candidate-history?"+new URLSearchParams({day,previous,change:k})}>{label}</Link>)}</div><div className="table-scroll"><table className="results-table"><thead><tr><th>股票</th><th>行业</th><th>变化</th><th>{previous} 收盘价</th><th>{day} 收盘价</th><th>前期NCAV覆盖</th><th>本期NCAV覆盖</th></tr></thead><tbody>{filtered.map(r=><tr key={r.code}><td><Link className="stock-name" href={"/stocks/"+encodeURIComponent(r.code)}>{r.name}</Link><div className="stock-meta">{r.code}</div></td><td>{r.industry??"—"}</td><td><span className={"pill "+(r.change==="added"?"pill-good":r.change==="removed"?"pill-warn":"pill-neutral")}>{labels[r.change]}</span></td><td>{fixed(r.previousPrice)}</td><td>{fixed(r.currentPrice)}</td><td>{pct(r.previousCoverage,1)}</td><td>{pct(r.currentCoverage,1)}</td></tr>)}</tbody></table></div>{!filtered.length&&<p className="muted">当前分类暂无记录</p>}</>}
    </section></main>;
}
