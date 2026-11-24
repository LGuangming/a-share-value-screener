import Link from "next/link";
import { AlertTriangle, BarChart3, CalendarDays, Database, Layers, ShieldCheck } from "lucide-react";
import { Empty } from "@/components/empty";
import StrategySwitch from "@/components/strategy-switch";
import StrategySummary from "@/components/strategy-summary";
import { strategies } from "@/lib/strategies";
import type { SummaryGroup } from "@/lib/strategy-summary";
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
  const groups: SummaryGroup[] = await Promise.all(strategies.map(async (strategy): Promise<SummaryGroup> => {
    if(strategy.status !== "live") return {id:strategy.id,name:strategy.name,state:"unpublished",rows:[]};
    if(!snapshot) return {id:strategy.id,name:strategy.name,state:error?"error":"unpublished",rows:[]};
    const result = strategy.id === "netnet_v1" ? scan : await screeningRowsStatus(snapshot.trade_date,strategy.id);
    return {id:strategy.id,name:strategy.name,state:result.error?"error":strategy.id==="netnet_v1"||result.rows.length?"ready":"unpublished",rows:result.rows.filter(r=>r.is_candidate).map(({ts_code,name,industry,price,market_cap_yuan,is_candidate})=>({ts_code,name,industry,price,market_cap_yuan,is_candidate}))};
  }));
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
    </div><div className="history-page-actions"><Link className="tool-button" href="/candidate-history">历史候选变化</Link><span className="date-tag"><CalendarDays size={17}/>{snapshot?.trade_date ?? "等待数据发布"}</span></div></div>

    <StrategySwitch mode="screen" requestedId={query.strategy} summary={<StrategySummary groups={groups} day={snapshot?.trade_date??null}/>}>
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
          <small>缺失及过期报告</small></div>
      </div>
      {!snapshot && <div className="panel notice-panel">
        <Empty title="尚未取得每日筛选数据" detail={error || "请检查数据库和每日扫描任务。"} type="warning"/>
      </div>}
      {snapshot && <div className="source-note"><Database size={15}/><span>
        数据截止 {snapshot.trade_date} · 已取得行情 {snapshot.metrics.quoted_stocks ?? "—"} 只 /
        已上市 {snapshot.metrics.listed_stocks ?? "—"} 只 · 本轮财务刷新 {snapshot.metrics.financial_successes ?? 0} 家。
        
      </span></div>}
      {scan.error && <div className="panel notice-panel">
        <Empty title="Net-Net股票结果暂时无法读取" detail={scan.error} type="warning"/>
      </div>}
      {snapshot && !scan.error ? <ScreenerResults rows={tableRows} initial={query}/>
        : <section className="panel"><div className="section-kicker">NET-NET SCREEN RESULTS</div>
          <h2>筛选结果暂不可用</h2>
          <p className="note">筛选结果读取失败，请稍后重试。</p>
        </section>}
    </StrategySwitch>
  </main>;
}
