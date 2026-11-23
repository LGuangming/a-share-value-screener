"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, Filter, Search } from "lucide-react";
import type { ScreenerRow } from "@/lib/data";
import { fixed, moneyCny, pct, signalLabel, stateLabel } from "@/lib/format";
import { Empty } from "@/components/empty";

export type ScreenTableRow = Pick<ScreenerRow,
  "ts_code" | "name" | "industry" | "price" | "pb" | "market_cap_yuan" |
  "ncav_coverage" | "stress_coverage" | "is_candidate" | "status" | "signal"
>;

type Filters = { q?: string; view?: string; order?: string; max_price?: string;
  min_coverage?: string; max_pb?: string };

function tagClass(status: string) {
  if (status === "NET_NET") return "pill pill-good";
  if (status === "DATA_GAP" || status === "STALE_FINANCIALS") return "pill pill-warn";
  return "pill pill-neutral";
}

export default function ScreenerResults({ rows, initial }: {
  rows: ScreenTableRow[]; initial: Filters;
}) {
  const [view, setView] = useState(["all","strict","gaps"].includes(initial.view ?? "")
    ? initial.view! : "strict");
  const [q, setQ] = useState(initial.q ?? "");
  const [maxPrice, setMaxPrice] = useState([5,8,10].includes(Number(initial.max_price))
    ? Number(initial.max_price) : 10);
  const [minCoverage, setMinCoverage] = useState([1.25,1.5,1.75].includes(Number(initial.min_coverage))
    ? Number(initial.min_coverage) : 1.25);
  const [maxPb, setMaxPb] = useState([0.5,0.7,1,1.2].includes(Number(initial.max_pb))
    ? Number(initial.max_pb) : 1.2);
  const [order, setOrder] = useState(initial.order === "stress" ? "stress" : "ncav");
  const [visibleCount, setVisibleCount] = useState(100);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows.filter(row => {
      if (needle && !`${row.name} ${row.ts_code} ${row.industry ?? ""}`.toLowerCase().includes(needle)) return false;
      if (row.price !== null && row.price > maxPrice) return false;
      if (row.pb !== null && row.pb > maxPb) return false;
      if (view === "strict") return row.is_candidate && row.ncav_coverage !== null
        && row.ncav_coverage >= minCoverage;
      if (view === "gaps") return row.status === "DATA_GAP" || row.status === "STALE_FINANCIALS";
      return true;
    }).sort((a, b) => order === "stress"
      ? (b.stress_coverage ?? -Infinity) - (a.stress_coverage ?? -Infinity)
      : (b.ncav_coverage ?? -Infinity) - (a.ncav_coverage ?? -Infinity));
  }, [rows, q, view, order, maxPrice, minCoverage, maxPb]);

  function setFilter(param: string, value: string, update: () => void) {
    update();
    setVisibleCount(100);
    // Native history API changes the shareable filters without re-running SQL
    // or fetching a new React Server Component tree.
    const url = new URL(window.location.href);
    url.searchParams.set(param, value);
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }

  return <section className="panel market-panel">
    <div className="panel-heading"><div><div className="section-kicker">SCREENER RESULTS · NET-NET</div>
      <h2>Net-Net筛选结果 <span className="count-badge">{filtered.length}</span></h2>
      <p>只有当前已上线的Net-Net使用以下NCAV筛选条件；修改标签和参数仅在本地过滤，无需重新读取数据库。</p>
    </div><Link className="inline-link" href="/methodology">查看筛选口径 <ArrowUpRight size={15}/></Link></div>
    <div className="filters">
      <div className="tabs" role="tablist" aria-label="筛选视图">
        {([["strict","严格候选"],["all","全部预筛"],["gaps","数据缺口"]] as const)
          .map(([key,label]) => <button type="button" role="tab" aria-selected={view === key}
            className={`tab${view === key ? " active" : ""}`} key={key}
            onClick={() => setFilter("view",key,()=>setView(key))}>{label}</button>)}
      </div>
      <div className="filter-form">
        <label className="search-field"><Search size={16}/><input aria-label="搜索股票"
          placeholder="公司名称 / 证券代码" value={q}
          onChange={e => setFilter("q",e.target.value,()=>setQ(e.target.value))}/></label>
        <select aria-label="最高价格" value={maxPrice}
          onChange={e=>setFilter("max_price",e.target.value,()=>setMaxPrice(Number(e.target.value)))}>
          <option value={5}>股价≤5元</option><option value={8}>股价≤8元</option>
          <option value={10}>股价≤10元</option>
        </select>
        <select aria-label="NCAV最低覆盖率" value={minCoverage}
          onChange={e=>setFilter("min_coverage",e.target.value,()=>setMinCoverage(Number(e.target.value)))}>
          <option value={1.25}>NCAV≥125%</option><option value={1.5}>NCAV≥150%</option>
          <option value={1.75}>NCAV≥175%</option>
        </select>
        <select aria-label="市净率上限" value={maxPb}
          onChange={e=>setFilter("max_pb",e.target.value,()=>setMaxPb(Number(e.target.value)))}>
          <option value={0.5}>PB≤0.5</option><option value={0.7}>PB≤0.7</option>
          <option value={1}>PB≤1.0</option><option value={1.2}>PB≤1.2</option>
        </select>
        <select aria-label="排序字段" value={order}
          onChange={e=>setFilter("order",e.target.value,()=>setOrder(e.target.value === "stress" ? "stress" : "ncav"))}>
          <option value="ncav">NCAV覆盖率排序</option><option value="stress">压力价值排序</option>
        </select>
        <span className="filter-local-hint"><Filter size={14}/> 即时筛选</span>
      </div>
    </div>
    {filtered.length ? <div className="table-scroll"><table className="results-table">
      <thead><tr><th>股票 / 行业</th><th>收盘价</th><th>总市值</th>
        <th>NCAV / 市值</th><th>压力价值覆盖率</th><th>市净率</th>
        <th>初筛状态</th><th>交易观察</th><th></th></tr></thead>
      <tbody>{filtered.slice(0,visibleCount).map(row=><tr key={row.ts_code}>
        <td><Link className="stock-name" href={`/stocks/${encodeURIComponent(row.ts_code)}`}>
          {row.name}<ArrowUpRight size={13}/></Link>
          <div className="stock-meta">{row.ts_code} · {row.industry || "行业待补充"}</div></td>
        <td className="number strong">{fixed(row.price)}</td>
        <td className="number">{moneyCny(row.market_cap_yuan)}</td>
        <td><span className={row.ncav_coverage !== null && row.ncav_coverage >= 1.25
          ? "number highlight" : "number"}>{pct(row.ncav_coverage,1)}</span></td>
        <td className="number">{pct(row.stress_coverage,1)}</td><td className="number">{fixed(row.pb)}</td>
        <td><span className={tagClass(row.status)}>{stateLabel(row.status)}</span></td>
        <td className="signal-cell">{signalLabel(row.signal)}</td>
        <td><Link aria-label={`查看${row.name}`} href={`/stocks/${encodeURIComponent(row.ts_code)}`}>
          <ArrowRight size={16}/></Link></td>
      </tr>)}</tbody></table></div>
    : <Empty title="暂无匹配的股票" detail="可调整筛选条件或切换到“全部预筛”视图。"/>}
    {filtered.length > visibleCount && <button type="button" className="filter-button screener-load-more"
      onClick={()=>setVisibleCount(n=>n+100)}>
      再显示100条（已显示{Math.min(visibleCount,filtered.length)} / {filtered.length}）
    </button>}
    <div className="panel-foot">筛选于本地即时完成；股票详情、策略定义及历史结果仍以数据库真实记录为准。</div>
  </section>;
}
