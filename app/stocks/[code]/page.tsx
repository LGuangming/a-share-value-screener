import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft, CalendarClock, FileCheck2, ShieldAlert, TrendingUp, AlertTriangle } from "lucide-react";
import { stockDetail } from "@/lib/data";
import { fixed, pct, moneyCny, stateLabel, signalLabel, flagLabel } from "@/lib/format";
import { Sparkline } from "@/components/sparkline";
import { Empty } from "@/components/empty";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{code: string}> };
export async function generateMetadata({params}: Props): Promise<Metadata> {
  const {code} = await params;
  return {title: `${decodeURIComponent(code)} 个股研究`};
}

export default async function StockPage({params}: Props) {
  const {code} = await params;
  const {stock,prices,recent} = await stockDetail(decodeURIComponent(code));
  return <main className="page-container">
    <Link className="back-link" href="/"><ArrowLeft size={16}/> 返回每日选股</Link>
    {!stock ? <div className="panel"><Empty title="暂未找到该股票的已发布研究记录" detail="可能尚未进入低价预筛范围，或者数据库还未完成首轮扫描。"/></div> : <>
      <div className="page-top stock-page-top"><div><div className="eyebrow">COMPANY RESEARCH / 个股研究</div>
        <h1>{stock.name} <span className="heading-period">{stock.ts_code}</span></h1>
        <p className="page-lead">{stock.industry || "行业待补充"} · {stateLabel(stock.status)}</p></div>
        <span className={`pill ${stock.is_candidate ? "pill-good" : "pill-neutral"}`}>{stock.is_candidate ? "账面符合筛选条件" : "尚未满足严格条件"}</span>
      </div>
      <div className="stats-grid">
        <div className="stat-card"><span>最近收盘价</span><strong>¥{fixed(stock.price)}</strong><small>交易日 {stock.trade_date}</small></div>
        <div className="stat-card"><span>NCAV覆盖率</span><strong className="stat-green">{pct(stock.ncav_coverage,1)}</strong><small>流动资产 − 全部负债</small></div>
        <div className="stat-card"><span>压力价值覆盖率</span><strong>{pct(stock.stress_coverage,1)}</strong><small>假设性回收率测算</small></div>
        <div className="stat-card"><span>当前信号</span><strong className="status-stat">{signalLabel(stock.signal)}</strong><small>并非交易指令</small></div>
      </div>
      <div className="detail-grid">
        <section className="panel"><div className="panel-heading"><div><div className="section-kicker">PRICE HISTORY</div><h2>收盘价变化</h2></div><TrendingUp size={18} className="muted"/></div>
          <Sparkline prices={prices}/><p className="note">历史价格为原始收盘价，尚未进行复权和交易成本处理，不代表策略收益。</p>
        </section>
        <section className="panel"><div className="section-kicker">ACCOUNTING SNAPSHOT</div><h2>关键财务指标</h2>
          <dl className="detail-list">
            <div><dt>总市值</dt><dd>{moneyCny(stock.market_cap_yuan)}</dd></div>
            <div><dt>账面净流动资产 NCAV</dt><dd>{moneyCny(stock.ncav_yuan)}</dd></div>
            <div><dt>统一压力测试价值</dt><dd>{moneyCny(stock.stress_ncav_yuan)}</dd></div>
            <div><dt>市净率 PB</dt><dd>{fixed(stock.pb)}</dd></div>
            <div><dt>资产负债率</dt><dd>{pct(stock.liability_ratio,1)}</dd></div>
            <div><dt>最近披露报告期累计经营现金流</dt><dd>{moneyCny(stock.operating_cf_yuan)}</dd></div>
            <div><dt>最近披露报告期累计合并净利润</dt><dd>{moneyCny(stock.net_profit_yuan)}</dd></div>
          </dl>
        </section>
      </div>
      <div className="detail-grid">
        <section className="panel"><div className="section-kicker">FUNDAMENTAL REVIEW</div><h2>基本面初步评价</h2>
          <p className="assessment">{stock.assessment}</p>
          <div className="research-disclaimer"><ShieldAlert size={18}/><span>根据财务数据和固定规则自动生成，不是经过人工审计的深入尽调报告。需要进一步核对附注、账龄、受限资金及关联交易。</span></div>
        </section>
        <section className="panel"><div className="section-kicker">RISK CHECKS</div><h2>财务风险和未核验项目</h2>
          {stock.flags.length ? <div className="flag-list">{stock.flags.map((flag,i)=><span key={`${flag}-${i}`} className="risk-flag"><AlertTriangle size={14}/>{flagLabel(flag)}</span>)}</div>
          : <div className="no-flags"><FileCheck2 size={20}/> 未由当前自动规则识别到风险标记；不代表不存在未披露或非结构化风险。</div>}
          <div className="dates"><div><CalendarClock size={15}/> 财务报告期：{stock.report_date || "未取得"}</div>
            <div><CalendarClock size={15}/> 财报披露日：{stock.disclosed_at || "未取得"}</div></div>
        </section>
      </div>
      <section className="panel"><div className="section-kicker">OBSERVATION HISTORY</div><h2>最近观察记录</h2>
        <div className="table-scroll"><table className="results-table compact"><thead><tr><th>交易日</th><th>价格</th><th>NCAV覆盖</th><th>压力测试</th><th>交易信号</th></tr></thead><tbody>
          {recent.map(r=><tr key={r.trade_date}><td>{r.trade_date}</td><td>¥{fixed(r.price)}</td><td>{pct(r.ncav_coverage,1)}</td><td>{pct(r.stress_coverage,1)}</td><td>{signalLabel(r.signal)}</td></tr>)}
        </tbody></table></div>
      </section>
    </>}
  </main>;
}
