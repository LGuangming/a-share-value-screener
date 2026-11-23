import Link from "next/link";
import { ArrowUpRight, Activity } from "lucide-react";
import { latestSnapshot, screeningRows } from "@/lib/data";
import { fixed, pct, signalLabel } from "@/lib/format";
import {
  evaluateFundamentalReview, severityLabel, priorityLabel,
} from "@/lib/review";
import { Empty } from "@/components/empty";
import StrategySwitch from "@/components/strategy-switch";
import ReviewReasons from "@/components/review-reasons";

export const dynamic = "force-dynamic";
export const metadata = { title: "交易观察" };

export default async function SignalsPage({ searchParams }: { searchParams: Promise<{strategy?: string}> }) {
  const query = await searchParams;
  const {snapshot} = await latestSnapshot();
  const rows = snapshot ? await screeningRows(snapshot.trade_date, "netnet_v1") : [];
  const observed = rows
    .filter(row => row.is_candidate && row.signal !== "NOT_ELIGIBLE")
    .map(row => ({row, review: evaluateFundamentalReview(row)}))
    .sort((a,b) => {
      const priority = {P0: 0, P1: 1, P2: 2};
      return priority[a.review.priority] - priority[b.review.priority] ||
        Number(b.row.signal === "BUY_WATCH") - Number(a.row.signal === "BUY_WATCH") ||
        (b.row.ncav_coverage ?? 0) - (a.row.ncav_coverage ?? 0);
    });
  const approved = observed.filter(x => x.review.conclusion === "通过").length;
  const pending = observed.filter(x => x.review.conclusion === "待补充资料").length;
  const blocked = observed.filter(x => x.review.conclusion === "不建议进入买入观察").length;

  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">TRADING OBSERVATIONS / MULTI-STRATEGY</div><h1>交易观察</h1>
      <p className="page-lead">按复核优先级查看Net-Net候选股票及风险事项。</p>
    </div><div className="date-tag"><Activity size={17}/> {snapshot?.trade_date ?? "等待发布"}</div></div>
    <StrategySwitch mode="signals" requestedId={query.strategy}>
    <div className="review-overview" aria-label="观察池自动复核概况">
      <div><span>候选观察股票</span><strong>{observed.length}</strong><small>Net-Net候选池</small></div>
      <div><span>初评通过</span><strong>{approved}</strong><small>暂无重点风险项</small></div>
      <div><span>待补充资料</span><strong>{pending}</strong><small>资料待核验</small></div>
      <div><span>不建议进入买入观察</span><strong>{blocked}</strong><small>存在高风险事项</small></div>
    </div>
    <section className="panel"><div className="panel-heading"><div>
      <div className="section-kicker">MONITORING LIST / RULE-BASED REVIEW</div>
      <h2>Net-Net候选股票复核清单</h2>
      <p>按风险优先级排序，点击股票查看复核明细。</p>
    </div></div>
      {observed.length ? <div className="table-scroll"><table className="results-table review-table">
        <thead><tr>
          <th>股票</th><th>收盘价</th><th>NCAV覆盖率</th><th>风险等级</th>
          <th>复核优先级 / 类别</th><th>主要复核原因</th><th>系统初评结论</th><th>交易观察</th>
        </tr></thead>
        <tbody>{observed.map(({row,review}) =>
          <tr key={row.ts_code}>
            <td><Link className="stock-name" href={`/stocks/${encodeURIComponent(row.ts_code)}`}>{row.name}<ArrowUpRight size={13}/></Link>
              <div className="stock-meta">{row.ts_code}</div></td>
            <td>{fixed(row.price)}</td><td>{pct(row.ncav_coverage,1)}</td>
            <td><span className={`review-level review-level-${review.severity.toLowerCase()}`}>{severityLabel[review.severity]}</span></td>
            <td><strong className="review-priority">{priorityLabel[review.priority]}</strong>
              <div className="review-meta">{review.focus}</div></td>
            <td><ReviewReasons reasons={review.reasons.map(reason => ({
              code: reason.code, label: reason.label,
            }))}/></td>
            <td><span className={`review-conclusion review-conclusion-${review.conclusion === "通过" ? "pass" : review.conclusion === "待补充资料" ? "pending" : "block"}`}>{review.conclusion}</span></td>
            <td>{signalLabel(row.signal)}</td>
          </tr>
        )}</tbody>
      </table></div> : <Empty title="当前没有可显示的交易观察信号" detail="完成财务扫描和历史价格回填后，符合条件的候选股票将显示在这里。"/>}
      <div className="review-footnote">P0：高风险优先核查；P1：资料或中风险待核验；P2：常规跟踪。</div>
    </section>
    </StrategySwitch>
  </main>;
}
