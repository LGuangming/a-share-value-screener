import Link from "next/link";
import { ArrowUpRight, AlertTriangle, Activity } from "lucide-react";
import { latestSnapshot, screeningRows } from "@/lib/data";
import { fixed, pct, signalLabel } from "@/lib/format";
import { Empty } from "@/components/empty";

export const dynamic = "force-dynamic";
export const metadata = { title: "交易观察" };
export default async function SignalsPage() {
  const {snapshot} = await latestSnapshot();
  const rows = snapshot ? await screeningRows(snapshot.trade_date) : [];
  const observed = rows.filter(x=>x.is_candidate && x.signal!=="NOT_ELIGIBLE")
    .sort((a,b) => (a.signal === "BUY_WATCH" ? -1 : 0) - (b.signal === "BUY_WATCH" ? -1 : 0));
  return <main className="page-container"><div className="page-top"><div>
      <div className="eyebrow">TRADING OBSERVATIONS / RESEARCH SIGNALS</div><h1>交易观察</h1>
      <p className="page-lead">基本面先过滤，技术面后观察；没有充分历史数据时不生成入场信号。</p>
    </div><div className="date-tag"><Activity size={17}/> {snapshot?.trade_date ?? "等待发布"}</div></div>
    <div className="panel signal-alert"><AlertTriangle size={20}/><p>当前信号规则仅为待回测的研究假设，不是自动交易指令，也不代表策略已证明具有正期望收益。禁止据此直接自动下单。</p></div>
    <section className="panel"><div className="panel-heading"><div><div className="section-kicker">MONITORING LIST</div><h2>候选股票观察池</h2><p>至少50个完整交易日的历史收盘价才能计算技术信号。</p></div></div>
      {observed.length ? <div className="table-scroll"><table className="results-table"><thead><tr><th>股票</th><th>收盘价</th><th>NCAV覆盖率</th><th>当前研究信号</th></tr></thead><tbody>
        {observed.map(row=><tr key={row.ts_code}><td><Link className="stock-name" href={`/stocks/${encodeURIComponent(row.ts_code)}`}>{row.name}<ArrowUpRight size={13}/></Link><div className="stock-meta">{row.ts_code}</div></td><td>{fixed(row.price)}</td><td>{pct(row.ncav_coverage,1)}</td><td>{signalLabel(row.signal)}</td></tr>)}
      </tbody></table></div> : <Empty title="当前没有可显示的交易观察信号" detail="完成每日财务扫描和历史价格回填后，这里会显示具备足够数据的候选股。"/>}
    </section>
  </main>;
}
