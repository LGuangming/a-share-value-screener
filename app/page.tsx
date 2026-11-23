import Link from "next/link";
import { AlertTriangle, ArrowRight, ArrowUpRight, BarChart3, CalendarDays, CircleHelp, Database, Filter, Info, Layers, Search, ShieldCheck, TrendingUp } from "lucide-react";
import { Empty } from "@/components/empty";
import { latestSnapshot, screeningRows, ScreenerRow } from "@/lib/data";
import { fixed, moneyCny, pct, signalLabel, stateLabel } from "@/lib/format";

export const dynamic = "force-dynamic";

type Query = { q?: string; view?: string; order?: string; max_price?: string; min_coverage?: string; max_pb?: string };
function tagClass(status: string) {
  if (status === "NET_NET") return "pill pill-good";
  if (status === "DATA_GAP" || status === "STALE_FINANCIALS") return "pill pill-warn";
  return "pill pill-neutral";
}

function StockTable({rows}: {rows: ScreenerRow[]}) {
  if (!rows.length) return <Empty title="暂无匹配的股票" detail="可以清空搜索关键词或切换筛选视图。"/>;
  return <div className="table-scroll"><table className="results-table">
    <thead><tr>
      <th>股票 / 行业</th><th>收盘价</th><th>总市值</th><th>NCAV / 市值</th>
      <th>压力价值覆盖率</th><th>市净率</th><th>初筛状态</th><th>交易观察</th><th></th>
    </tr></thead>
    <tbody>{rows.map(r => <tr key={r.ts_code}>
      <td><Link className="stock-name" href={`/stocks/${encodeURIComponent(r.ts_code)}`}>{r.name}<ArrowUpRight size={13}/></Link>
        <div className="stock-meta">{r.ts_code} · {r.industry || "行业待补充"}</div></td>
      <td className="number strong">{fixed(r.price)}</td>
      <td className="number">{moneyCny(r.market_cap_yuan)}</td>
      <td><span className={r.ncav_coverage !== null && r.ncav_coverage >= 1.25 ? "number highlight" : "number"}>{pct(r.ncav_coverage,1)}</span></td>
      <td className="number">{pct(r.stress_coverage,1)}</td>
      <td className="number">{fixed(r.pb)}</td>
      <td><span className={tagClass(r.status)}>{stateLabel(r.status)}</span></td>
      <td className="signal-cell">{signalLabel(r.signal)}</td>
      <td><Link aria-label={`查看${r.name}`} href={`/stocks/${encodeURIComponent(r.ts_code)}`}><ArrowRight size={16}/></Link></td>
    </tr>)}</tbody>
  </table></div>;
}

export default async function Dashboard({searchParams}: {searchParams: Promise<Query>}) {
  const query = await searchParams;
  const {snapshot,error} = await latestSnapshot();
  const rows = snapshot ? await screeningRows(snapshot.trade_date) : [];
  const key = (typeof query.q === "string" ? query.q : "").trim().toLowerCase();
  const view = typeof query.view === "string" && ["all","strict","gaps"].includes(query.view) ? query.view : "strict";
  const maxPrice = [5,8,10].includes(Number(query.max_price)) ? Number(query.max_price) : 10;
  const minCoverage = [1.25,1.5,1.75].includes(Number(query.min_coverage)) ? Number(query.min_coverage) : 1.25;
  const maxPb = [0.5,0.7,1,1.2].includes(Number(query.max_pb)) ? Number(query.max_pb) : 1.2;
  const matching = rows.filter(r => {
    if (key && !`${r.name} ${r.ts_code} ${r.industry ?? ""}`.toLowerCase().includes(key)) return false;
    if (r.price !== null && r.price > maxPrice) return false;
    if (r.pb !== null && r.pb > maxPb) return false;
    if (view === "strict" && (r.ncav_coverage === null || r.ncav_coverage < minCoverage)) return false;
    if (view === "strict") return r.is_candidate;
    if (view === "gaps") return r.status === "DATA_GAP" || r.status === "STALE_FINANCIALS";
    return true;
  });
  const ordered = [...matching].sort((a,b) => query.order === "stress" ?
    (b.stress_coverage ?? -Infinity) - (a.stress_coverage ?? -Infinity) :
    (b.ncav_coverage ?? -Infinity) - (a.ncav_coverage ?? -Infinity));
  const qualified = rows.filter(r => r.is_candidate);
  const stressQualified = qualified.filter(r => r.stress_coverage !== null && r.stress_coverage >= 1);
  const gaps = rows.filter(r => r.status === "DATA_GAP" || r.status === "STALE_FINANCIALS");
  const tradeDay = snapshot?.trade_date;
  return <main className="page-container">
    <div className="page-top"><div><div className="eyebrow">RESEARCH DASHBOARD / 每日市场扫描</div>
      <h1>Net-Net每日选股 <span className="heading-period">/ 已上线策略</span></h1>
      <p className="page-lead">当前页面仅展示Net-Net资产折价筛选。其他选股方向在策略中心独立配置、验证和上线。 <Link className="inline-link" href="/strategies">浏览策略中心 <ArrowUpRight size={14}/></Link></p></div>
      <div className="date-tag"><CalendarDays size={17}/><span>{tradeDay ?? "尚无有效交易日快照"}</span></div>
    </div>
    <div className="hero-strip">
      <div><span className="hero-label">核心投资方法</span><h2>价格低，不等于价值低估。</h2><p>用财务过滤找资产折价，用经营质量验证安全边际，用交易观察信号辅助执行。</p></div>
      <div className="hero-formula"><div>净流动资产价值 <strong>NCAV</strong></div><strong>流动资产 − 全部负债</strong><small>以总市值衡量折价程度</small></div>
    </div>
    <div className="stats-grid">
      <div className="stat-card"><span><Layers size={16}/>筛选预检股票</span><strong>{snapshot ? (snapshot.metrics.pre_screened ?? rows.length).toLocaleString("zh-CN") : "—"}</strong><small>低价及PB预筛后的样本</small></div>
      <div className="stat-card"><span><ShieldCheck size={16}/>满足NCAV门槛</span><strong className="stat-green">{snapshot ? qualified.length.toLocaleString("zh-CN") : "—"}</strong><small>覆盖率≥125%且未被排除</small></div>
      <div className="stat-card"><span><BarChart3 size={16}/>压力测试通过</span><strong>{snapshot ? stressQualified.length.toLocaleString("zh-CN") : "—"}</strong><small>调整后覆盖率≥100%</small></div>
      <div className="stat-card"><span><AlertTriangle size={16}/>财报待核验</span><strong className="stat-orange">{snapshot ? gaps.length.toLocaleString("zh-CN") : "—"}</strong><small>禁止将缺失值当作零</small></div>
    </div>
    {!snapshot && <div className="panel notice-panel"><Empty title="网站已具备数据展示框架，但尚未完成首次实盘扫描" detail={error || "请配置数据库、Tushare Token并触发GitHub Actions任务。"} type="warning"/></div>}
    {snapshot && <div className="source-note"><Database size={15}/>
      <span>数据截止 {snapshot.trade_date} · 已取得行情 {snapshot.metrics.quoted_stocks ?? "—"} 只 / 已上市 {snapshot.metrics.listed_stocks ?? "—"} 只 · 本轮财务刷新 {snapshot.metrics.financial_successes ?? 0} 家。未补齐财报的股票不计入严格候选。</span>
    </div>}
    <section className="panel market-panel">
      <div className="panel-heading"><div><div className="section-kicker">SCREENER RESULTS</div><h2>筛选结果 <span className="count-badge">{snapshot ? ordered.length : "—"}</span></h2>
        <p>按最新成功发布的交易日快照展示；每条数据均可查看财报期间与披露日期；当日披露的信息从下一交易日开始使用。</p></div>
        <Link className="inline-link" href="/methodology">查看筛选口径 <ArrowUpRight size={15}/></Link>
      </div>
      <div className="filters"><div className="tabs">
        <Link className={`tab ${view==="strict"?"active":""}`} href="/?view=strict">严格候选</Link>
        <Link className={`tab ${view==="all"?"active":""}`} href="/?view=all">全部预筛</Link>
        <Link className={`tab ${view==="gaps"?"active":""}`} href="/?view=gaps">数据缺口</Link>
      </div>
        <form className="filter-form" action="/" method="get">
          <input type="hidden" name="view" value={view}/>
          <label className="search-field"><Search size={16}/><input aria-label="搜索股票" placeholder="公司名称 / 证券代码" name="q" defaultValue={key}/></label>
          <select name="max_price" aria-label="最高价格" defaultValue={String(maxPrice)}>
            <option value="5">股价≤5元</option><option value="8">股价≤8元</option><option value="10">股价≤10元</option>
          </select>
          <select name="min_coverage" aria-label="NCAV最低覆盖率" defaultValue={String(minCoverage)}>
            <option value="1.25">NCAV≥125%</option><option value="1.5">NCAV≥150%</option><option value="1.75">NCAV≥175%</option>
          </select>
          <select name="max_pb" aria-label="市净率上限" defaultValue={String(maxPb)}>
            <option value="0.5">PB≤0.5</option><option value="0.7">PB≤0.7</option><option value="1">PB≤1.0</option><option value="1.2">PB≤1.2</option>
          </select>
          <select name="order" aria-label="排序字段" defaultValue={query.order === "stress" ? "stress" : "ncav"}>
            <option value="ncav">NCAV覆盖率排序</option><option value="stress">压力价值排序</option>
          </select>
          <button type="submit" className="filter-button"><Filter size={15}/> 应用</button>
        </form>
      </div>
      {snapshot ? <StockTable rows={ordered.slice(0,300)}/> : <Empty title="等待首次数据发布" detail="当前未读取到任何真实股票筛选结果；页面不会展示模拟行情。"/>}
      {ordered.length > 300 && <div className="panel-foot">结果较多，仅显示前300条。建议使用搜索缩小范围。</div>}
    </section>
    <div className="bottom-grid">
      <div className="panel info-panel"><div className="section-kicker">SCREENING LOGIC</div><h3>从账面折价到真实安全边际</h3>
        <div className="step"><div>01</div><p><strong>严格资产折价</strong><span>NCAV = 流动资产 − 全部负债，要求市值覆盖率≥125%。</span></p></div>
        <div className="step"><div>02</div><p><strong>资产回收压力测试</strong><span>应收、存货、预付款分别折价，并考虑少数股东权益。</span></p></div>
        <div className="step"><div>03</div><p><strong>困境反转与交易观察</strong><span>利润、现金流异常独立标记；技术信号只作为研究参考。</span></p></div>
        <Link className="inline-link" href="/methodology">了解完整方法 <ArrowRight size={15}/></Link>
      </div>
      <div className="panel info-panel"><div className="section-kicker">DATA QUALITY</div><h3>计算结果必须可核验</h3>
        <div className="quality-item"><Info size={17}/><div><strong>数据披露日期</strong><span>仅使用筛选交易日前已公布的财务报表。</span></div></div>
        <div className="quality-item"><CircleHelp size={17}/><div><strong>财报缺失不等于不符合</strong><span>缺数据将明确标识，避免误报筛选结果。</span></div></div>
        <div className="quality-item"><TrendingUp size={17}/><div><strong>交易观察非自动下单</strong><span>所有信号需通过回测与流动性核验。</span></div></div>
        <Link className="inline-link" href="/signals">查看交易观察 <ArrowRight size={15}/></Link>
      </div>
    </div>
  </main>;
}
