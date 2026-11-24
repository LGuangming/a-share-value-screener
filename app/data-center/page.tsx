import {dataCenterSnapshot} from "@/lib/data";
import {archiveOverview} from "@/lib/archive-research";
import {pct} from "@/lib/format";
export const metadata={title:"数据中心"};
export const dynamic="force-dynamic";
export const maxDuration=60;
const number=(v:number|null|undefined)=>v==null?"—":v.toLocaleString("zh-CN");
export default async function DataCenter(){
  const [db,archive]=await Promise.allSettled([dataCenterSnapshot(),archiveOverview()]);
  const d=db.status==="fulfilled"?db.value:null;
  const items=archive.status==="fulfilled"?archive.value:[];
  const m=d?.snapshot?.metrics;
  return <main className="page-container"><div className="page-top"><div><h1>数据中心</h1><p className="page-lead">每日数据、历史档案及任务运行记录</p></div></div>
    <div className="stats-grid">{[["最近行情日期",d?.day??"—"],["当日行情记录",number(d?.rows)],["最近筛选日期",d?.snapshot?.trade_date??"—"],["当日PB有效记录",number(d?.pb)]].map(([label,value])=><div className="stat-card" key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
    <section className="panel"><h2>每日数据</h2>{d?<div className="table-scroll"><table className="results-table v4-data-table"><thead><tr><th>数据集</th><th>统计日期</th><th>记录数</th><th>统计口径</th></tr></thead><tbody>
      <tr><td>每日行情</td><td>{d.day??"—"}</td><td>{number(d.rows)}</td><td>数据库当日记录数</td></tr>
      <tr><td>每日估值</td><td>{d.day??"—"}</td><td>{number(d.marketCap)}</td><td>总市值非空记录</td></tr>
      <tr><td>PB</td><td>{d.day??"—"}</td><td>{number(d.pb)}</td><td>PB非空 / 行情记录 · {d.rows&&d.pb!==null?pct(d.pb/d.rows,1):"—"}</td></tr>
      <tr><td>筛选行情覆盖</td><td>{d.snapshot?.trade_date??"—"}</td><td>{number(m?.quoted_stocks)} / {number(m?.listed_stocks)}</td><td>{m?.listed_stocks&&m.quoted_stocks!==undefined?pct(m.quoted_stocks/m.listed_stocks,1):"—"}</td></tr>
      <tr><td>财务刷新</td><td>{d.snapshot?.trade_date??"—"}</td><td>{number(m?.financial_successes)}</td><td>最近成功扫描</td></tr>
    </tbody></table></div>:<p role="status">数据库统计暂不可用</p>}</section>
    <section className="panel"><h2>历史档案发布情况</h2><p className="note">2020–2025完整年度清单 · 以已发布清单统计，不重新读取行情分区。</p>{items.length?<div className="table-scroll"><table className="results-table v4-data-table"><thead><tr><th>数据集</th><th>发布状态</th>{[2020,2021,2022,2023,2024,2025].map(y=><th key={y}>{y}</th>)}<th>统计</th></tr></thead><tbody>{items.map(a=><tr key={a.id}><td>{a.name}</td><td>{a.state==="ready"?"已发布":a.state==="partial"?"部分发布":a.state==="error"?"读取失败":"未发布"}</td>{[2020,2021,2022,2023,2024,2025].map(y=><td key={y}>{number(a.years.find(x=>x.year===y)?.rows)}</td>)}<td>{a.detail}</td></tr>)}</tbody></table></div>:<p role="status">历史档案统计暂不可用</p>}</section>
    <section className="panel"><h2>最近数据库任务</h2>{d?.jobs.length?<div className="table-scroll"><table className="results-table v4-data-table"><thead><tr><th>任务</th><th>交易日</th><th>状态</th><th>开始（UTC）</th><th>结束（UTC）</th></tr></thead><tbody>{d.jobs.map((j,i)=><tr key={j.started+i}><td>{j.type}</td><td>{j.day??"—"}</td><td>{({success:"成功",failed:"失败",skipped:"跳过",running:"运行中"} as Record<string,string>)[j.status]??j.status}</td><td>{j.started.replace("T"," ").slice(0,19)}</td><td>{j.finished?.replace("T"," ").slice(0,19)??"—"}</td></tr>)}</tbody></table></div>:<p className="muted">暂无可显示的任务记录</p>}</section>
  </main>;
}
