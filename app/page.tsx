import Link from "next/link";
import { AlertTriangle, ArrowRight, ArrowUpRight, BarChart3, CalendarDays, CircleHelp, Database, Info, Layers, ShieldCheck, TrendingUp } from "lucide-react";
import { Empty } from "@/components/empty";
import StrategySwitch from "@/components/strategy-switch";
import ScreenerResults, { type ScreenTableRow } from "@/components/screener-results";
import { latestSnapshot, screeningRowsStatus } from "@/lib/data";

export const dynamic = "force-dynamic";

type Query = { strategy?: string; q?: string; view?: string; order?: string;
  max_price?: string; min_coverage?: string; max_pb?: string };

export default async function Dashboard({ searchParams }: { searchParams: Promise<Query> }) {
  const query = await searchParams;
  const { snapshot, error } = await latestSnapshot();
  // Only Net-Net has a published scanner. Other strategies display their own
  // condition catalog, not re-labeled Net-Net candidates.
  const scan = snapshot ? await screeningRowsStatus(snapshot.trade_date, "netnet_v1") : { rows: [], error: null };
  const rows = scan.rows;
  const qualified = rows.filter(r => r.is_candidate);
  const stressQualified = qualified.filter(r => r.stress_coverage !== null && r.stress_coverage >= 1);
  const gaps = rows.filter(r => r.status === "DATA_GAP" || r.status === "STALE_FINANCIALS");
  // Minimize client-side hydration payload: the table only needs these fields.
  const tableRows: ScreenTableRow[] = rows.map(r => ({
    ts_code: r.ts_code, name: r.name, industry: r.industry, price: r.price, pb: r.pb,
    market_cap_yuan: r.market_cap_yuan, ncav_coverage: r.ncav_coverage,
    stress_coverage: r.stress_coverage, is_candidate: r.is_candidate,
    status: r.status, signal: r.signal,
  }));

  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">RESEARCH DASHBOARD / 多策略每日扫描</div>
      <h1>选股中心</h1>
      <p className="page-lead">按策略独立展示筛选规则与研究结果。只有已部署且发布真实数据的策略才会出现股票清单。</p>
    </div><span className="date-tag"><CalendarDays size={17}/>{snapshot?.trade_date ?? "等待数据发布"}</span></div>

    <StrategySwitch mode="screen" requestedId={query.strategy}>
      <div className="hero-strip">
        <div><span className="hero-label">NET-NET / 已上线策略</span>
          <h2>Net-Net每日选股 · 价格低，不等于价值低估。</h2>
          <p>使用已披露财报计算资产折价，结合压力价值和经营风险验证安全边际。</p></div>
        <div className="hero-formula"><div>净流动资产价值 <strong>NCAV</strong></div>
          <strong>流动资产 − 全部负债</strong><small>以总市值衡量折价程度</small></div>
      </div>
      <div className="stats-grid">
        <div className="stat-card"><span><Layers size={16}/>筛选预检股票</span>
          <strong>{snapshot ? (snapshot.metrics.pre_screened ?? rows.length).toLocaleString("zh-CN") : "—"}</strong>
          <small>低价及PB预筛后的样本</small></div>
        <div className="stat-card"><span><ShieldCheck size={16}/>满足NCAV门槛</span>
          <strong className="stat-green">{snapshot ? qualified.length.toLocaleString("zh-CN") : "—"}</strong>
          <small>覆盖率≥125%且未被排除</small></div>
        <div className="stat-card"><span><BarChart3 size={16}/>压力测试通过</span>
          <strong>{snapshot ? stressQualified.length.toLocaleString("zh-CN") : "—"}</strong>
          <small>调整后覆盖率≥100%</small></div>
        <div className="stat-card"><span><AlertTriangle size={16}/>财报待核验</span>
          <strong className="stat-orange">{snapshot ? gaps.length.toLocaleString("zh-CN") : "—"}</strong>
          <small>缺失值不作为零</small></div>
      </div>
      {!snapshot && <div className="panel notice-panel">
        <Empty title="尚未取得每日筛选数据" detail={error || "请检查数据库和每日扫描任务。"} type="warning"/>
      </div>}
      {snapshot && <div className="source-note"><Database size={15}/><span>
        数据截止 {snapshot.trade_date} · 已取得行情 {snapshot.metrics.quoted_stocks ?? "—"} 只 /
        已上市 {snapshot.metrics.listed_stocks ?? "—"} 只 · 本轮财务刷新 {snapshot.metrics.financial_successes ?? 0} 家。
        查询结果短时缓存90秒，筛选条件在浏览器内切换，无需重新查询。
      </span></div>}
      {scan.error && <div className="panel notice-panel">
        <Empty title="Net-Net股票结果暂时无法读取" detail={scan.error} type="warning"/>
      </div>}
      {snapshot && !scan.error ? <ScreenerResults rows={tableRows} initial={query}/>
        : <section className="panel"><div className="section-kicker">NET-NET SCREEN RESULTS</div>
          <h2>历史筛选结果暂不可用</h2>
          <p className="note">读取尚未成功，系统不会把数据库错误解释为零只股票。无需重新采集历史行情或删除原有筛选记录。</p>
        </section>}
      <div className="bottom-grid">
        <div className="panel info-panel"><div className="section-kicker">NET-NET METHODOLOGY</div>
          <h3>从账面折价到真实安全边际</h3>
          <div className="step"><div>01</div><p><strong>严格资产折价</strong>
            <span>NCAV = 流动资产 − 全部负债，要求市值覆盖率≥125%。</span></p></div>
          <div className="step"><div>02</div><p><strong>资产回收压力测试</strong>
            <span>应收、存货及预付款折价，并考虑少数股东权益。</span></p></div>
          <div className="step"><div>03</div><p><strong>困境反转与交易观察</strong>
            <span>利润、现金流风险独立标记；技术信号仅供研究。</span></p></div>
          <Link className="inline-link" href="/methodology">了解完整方法 <ArrowRight size={15}/></Link>
        </div>
        <div className="panel info-panel"><div className="section-kicker">DATA QUALITY</div>
          <h3>计算结果必须可核验</h3>
          <div className="quality-item"><Info size={17}/><div><strong>历史时点披露</strong>
            <span>不使用交易日后才公布的财报。</span></div></div>
          <div className="quality-item"><CircleHelp size={17}/><div><strong>财报缺失</strong>
            <span>缺数据明确标识，避免错误排除或假阳性。</span></div></div>
          <div className="quality-item"><TrendingUp size={17}/><div><strong>信号非交易指令</strong>
            <span>所有观察需通过交易规则与流动性核验。</span></div></div>
          <Link className="inline-link" href="/signals">查看交易观察 <ArrowRight size={15}/></Link>
        </div>
      </div>
    </StrategySwitch>
  </main>;
}
