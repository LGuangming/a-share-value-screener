import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft, CalendarClock, FileCheck2, TrendingUp, AlertTriangle } from "lucide-react";
import { stockDetail } from "@/lib/data";
import { fixed, pct, moneyCny, stateLabel, signalLabel, flagLabel } from "@/lib/format";
import { evaluateFundamentalReview, severityLabel, priorityLabel } from "@/lib/review";
import MarketChart from "@/components/market-chart";
import { Empty } from "@/components/empty";
import ResearchTabs from "@/components/research-tabs";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{code: string}> };
export async function generateMetadata({params}: Props): Promise<Metadata> {
  const {code} = await params;
  return {title: `${decodeURIComponent(code)} 个股研究`};
}

export default async function StockPage({params}: Props) {
  const {code} = await params;
  const {stock,prices,recent} = await stockDetail(decodeURIComponent(code));
  const review = stock ? evaluateFundamentalReview(stock) : null;
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
        <div className="stat-card"><span>当前信号</span><strong className="status-stat">{signalLabel(stock.signal)}</strong><small>趋势观察</small></div>
      </div>
      <ResearchTabs label="个股研究" tabs={[
        {id:"overview",label:"概览",content:<><section className="panel market-chart-panel">
        <div className="panel-heading"><div><h2>行情分析</h2></div><TrendingUp size={18} className="muted"/></div>
        <MarketChart key={stock.ts_code} code={stock.ts_code} name={stock.name} prices={prices}/>
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
      <section className="panel"><div className="section-kicker">OBSERVATION HISTORY</div><h2>最近观察记录</h2>
        <div className="table-scroll"><table className="results-table compact"><thead><tr><th>交易日</th><th>价格</th><th>NCAV覆盖</th><th>压力测试</th><th>交易信号</th></tr></thead><tbody>
          {recent.map(r=><tr key={r.trade_date}><td>{r.trade_date}</td><td>¥{fixed(r.price)}</td><td>{pct(r.ncav_coverage,1)}</td><td>{pct(r.stress_coverage,1)}</td><td>{signalLabel(r.signal)}</td></tr>)}
        </tbody></table></div>
      </section></>},
        {id:"finance",label:"财务指标",content:<section className="panel"><h2>财务指标</h2><p className="note">报告期：{stock.report_date || "—"} · 披露日期：{stock.disclosed_at || "—"}</p>
        <div className="table-scroll"><table className="results-table financial-table"><thead><tr><th>项目</th><th>已披露指标</th><th>单位 / 口径</th></tr></thead><tbody>
          <tr><td>资产负债率</td><td>{pct(stock.liability_ratio,1)}</td><td>合并口径</td></tr>
          <tr><td>净流动资产</td><td>{moneyCny(stock.ncav_yuan)}</td><td>流动资产 − 全部负债</td></tr>
          <tr><td>经营活动现金流量净额</td><td>{moneyCny(stock.operating_cf_yuan)}</td><td>报告期累计</td></tr>
          <tr><td>合并净利润</td><td>{moneyCny(stock.net_profit_yuan)}</td><td>报告期累计</td></tr>
        </tbody></table></div>
      </section>},
        {id:"valuation",label:"估值分析",content:<section className="panel"><h2>资产折价与压力测试</h2>
        <div className="table-scroll"><table className="results-table financial-table"><thead><tr><th>估值项目</th><th>金额 / 比率</th><th>计算口径</th></tr></thead><tbody>
          <tr><td>总市值</td><td>{moneyCny(stock.market_cap_yuan)}</td><td>收盘日总市值</td></tr>
          <tr><td>账面净流动资产</td><td>{moneyCny(stock.ncav_yuan)}</td><td>NCAV</td></tr>
          <tr><td>NCAV覆盖率</td><td>{pct(stock.ncav_coverage,1)}</td><td>NCAV / 市值</td></tr>
          <tr><td>统一压力测试价值</td><td>{moneyCny(stock.stress_ncav_yuan)}</td><td>调整后净流动资产</td></tr>
          <tr><td>压力价值覆盖率</td><td>{pct(stock.stress_coverage,1)}</td><td>压力价值 / 市值</td></tr>
          <tr><td>账面资产折价率</td><td>{stock.ncav_yuan!==null && stock.ncav_yuan>0 && stock.market_cap_yuan!==null ? pct(1-stock.market_cap_yuan/stock.ncav_yuan,1):"—"}</td><td>1 − 市值 / NCAV</td></tr>
          <tr><td>市净率</td><td>{fixed(stock.pb)}</td><td>PB</td></tr>
        </tbody></table></div>
        <Link className="inline-link" href="/methodology?strategy=netnet_v1">查看资产回收率及筛选口径</Link>
      </section>},
        {id:"risk",label:"风险复核",content:<>{review && <section className="panel review-detail-panel">
        <div className="panel-heading"><div>
          <div className="section-kicker">FUNDAMENTAL RISK REVIEW</div>
          <h2>基本面复核摘要</h2>
          <p>财务指标与风险事项汇总。</p>
        </div></div>
        <div className="review-summary-grid">
          <div><span>风险等级</span><strong className={`review-level review-level-${review.severity.toLowerCase()}`}>{severityLabel[review.severity]}</strong></div>
          <div><span>复核优先级</span><strong>{priorityLabel[review.priority]}</strong></div>
          <div><span>主要复核类别</span><strong>{review.focus}</strong></div>
          <div><span>系统初评结论</span><strong className={`review-conclusion review-conclusion-${review.conclusion === "通过" ? "pass" : review.conclusion === "待补充资料" ? "pending" : "block"}`}>{review.conclusion}</strong></div>
        </div>
        <h3 className="review-subhead">复核原因与建议核查事项</h3>
        {review.reasons.length ? <div className="review-findings">
          {review.reasons.map(reason => <div className="review-finding" key={reason.code}>
            <div className="review-finding-heading"><strong>{reason.label}</strong>
              <span>{reason.focus} · 风险{severityLabel[reason.severity]}</span></div>
            <p>{reason.explanation}</p>
          </div>)}
        </div> : <p className="review-muted">暂无重点复核事项。</p>}
      </section>}<div className="detail-grid">
        <section className="panel"><div className="section-kicker">FUNDAMENTAL REVIEW</div><h2>基本面初步评价</h2>
          <p className="assessment">{stock.assessment}</p>
        </section>
        <section className="panel"><div className="section-kicker">RISK CHECKS</div><h2>财务风险和未核验项目</h2>
          {stock.flags.length ? <div className="flag-list">{stock.flags.map((flag,i)=><span key={`${flag}-${i}`} className="risk-flag"><AlertTriangle size={14}/>{flag === "CASHFLOW_NOT_VERIFIED" ? review?.reasons.find(x => x.code.startsWith("CASHFLOW_"))?.label ?? flagLabel(flag) : flagLabel(flag)}</span>)}</div>
          : <div className="no-flags"><FileCheck2 size={20}/> 暂无已标记风险事项。</div>}
          <div className="dates"><div><CalendarClock size={15}/> 财务报告期：{stock.report_date || "未取得"}</div>
            <div><CalendarClock size={15}/> 财报披露日：{stock.disclosed_at || "未取得"}</div></div>
        </section>
      </div></>},
      ]}/>
    </>}
  </main>;
}
