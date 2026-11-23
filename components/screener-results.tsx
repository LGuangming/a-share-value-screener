"use client";

import { useId, useMemo, useState } from "react";
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

/** Search the published Net-Net universe instantly, with exact/prefix matches first. */
function matchRank(row: ScreenTableRow, needle: string): number | null {
  const code = row.ts_code.toLowerCase();
  const name = row.name.toLocaleLowerCase();
  const industry = (row.industry ?? "").toLocaleLowerCase();
  if (code === needle) return 0;
  if (name === needle) return 1;
  if (code.startsWith(needle)) return 2;
  if (name.startsWith(needle)) return 3;
  if (code.includes(needle)) return 4;
  if (name.includes(needle)) return 5;
  if (industry.includes(needle)) return 6;
  return null;
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
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const suggestionListId = useId();
  const needle = q.trim().toLocaleLowerCase();

  const matches = useMemo(() => {
    if (!needle) return [];
    return rows.map(row => ({ row, rank: matchRank(row, needle) }))
      .filter((match): match is { row: ScreenTableRow; rank: number } => match.rank !== null)
      .sort((a,b) => a.rank - b.rank || a.row.ts_code.localeCompare(b.row.ts_code))
      .map(match => match.row);
  }, [rows, needle]);
  const suggestions = matches.slice(0, 8);

  const filtered = useMemo(() => {
    // Searching is global within the published strategy's pre-screen rows.
    // Do not hide a matching stock merely because "strict" or a PB/price
    // threshold is selected; those filters resume when search is cleared.
    if (needle) return matches;
    return rows.filter(row => {
      if (row.price !== null && row.price > maxPrice) return false;
      if (row.pb !== null && row.pb > maxPb) return false;
      if (view === "strict") return row.is_candidate && row.ncav_coverage !== null
        && row.ncav_coverage >= minCoverage;
      if (view === "gaps") return row.status === "DATA_GAP" || row.status === "STALE_FINANCIALS";
      return true;
    }).sort((a, b) => order === "stress"
      ? (b.stress_coverage ?? -Infinity) - (a.stress_coverage ?? -Infinity)
      : (b.ncav_coverage ?? -Infinity) - (a.ncav_coverage ?? -Infinity));
  }, [rows, needle, matches, view, order, maxPrice, minCoverage, maxPb]);

  function setFilter(param: string, value: string, update: () => void) {
    update();
    setVisibleCount(100);
    // Native history API changes the shareable filters without re-running SQL
    // or fetching a new React Server Component tree.
    const url = new URL(window.location.href);
    if (param === "q" && !value.trim()) url.searchParams.delete(param);
    else url.searchParams.set(param, value);
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }

  function updateSearch(value: string) {
    setFilter("q", value, () => setQ(value));
    setSuggestionsOpen(true);
    setHighlightedIndex(-1);
  }

  function chooseSuggestion(code: string) {
    setFilter("q", code, () => setQ(code));
    setSuggestionsOpen(false);
    setHighlightedIndex(-1);
  }

  function handleSearchKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setSuggestionsOpen(false);
      setHighlightedIndex(-1);
    }
    if (!suggestions.length || !suggestionsOpen) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setHighlightedIndex(i => (i + 1) % suggestions.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setHighlightedIndex(i => (i <= 0 ? suggestions.length - 1 : i - 1));
    } else if (event.key === "Enter" && highlightedIndex >= 0) {
      event.preventDefault();
      chooseSuggestion(suggestions[highlightedIndex].ts_code);
    }
  }

  return <section className="panel market-panel">
    <div className="panel-heading"><div><div className="section-kicker">SCREENER RESULTS · NET-NET</div>
      <h2>{needle ? "股票即时搜索" : "Net-Net筛选结果"} <span className="count-badge">{filtered.length}</span></h2>
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
        <div className="stock-search">
          <div className="search-field"><Search size={16}/>
            <input aria-label="搜索股票" autoComplete="off" type="search"
              placeholder="实时搜索名称 / 代码" value={q}
              role="combobox" aria-autocomplete="list"
              aria-expanded={Boolean(needle && suggestionsOpen && suggestions.length)}
              aria-controls={suggestionListId}
              aria-activedescendant={highlightedIndex >= 0 && suggestionsOpen && suggestions[highlightedIndex]
                ? `${suggestionListId}-${highlightedIndex}` : undefined}
              onFocus={() => setSuggestionsOpen(true)}
              onBlur={() => {setSuggestionsOpen(false);setHighlightedIndex(-1);}}
              onKeyDown={handleSearchKeyDown}
              onChange={e => updateSearch(e.target.value)}/>
          </div>
          {needle && suggestionsOpen && suggestions.length > 0 && <div
            id={suggestionListId} role="listbox" aria-label="匹配的股票"
            className="stock-suggestions">
            <div className="stock-suggestions-heading">实时匹配 {matches.length} 只 · 显示最相关的 {suggestions.length} 只</div>
            {suggestions.map((row,i) => <button type="button"
              id={`${suggestionListId}-${i}`}
              role="option" aria-selected={highlightedIndex === i}
              className={`stock-suggestion${highlightedIndex === i ? " is-highlighted" : ""}`}
              key={row.ts_code}
              onMouseDown={e => e.preventDefault()}
              onClick={() => chooseSuggestion(row.ts_code)}>
              <strong>{row.name}</strong><span>{row.ts_code}</span>
              <small>{row.industry || "行业待补充"}</small>
            </button>)}
          </div>}
        </div>
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
    {needle && <div className="stock-search-feedback" role="status" aria-live="polite">
      已即时匹配 {matches.length} 只股票。搜索范围为当前Net-Net已发布的 {rows.length} 只预筛股票；
      搜索期间暂不应用“严格候选”、股价和PB条件，清空搜索后恢复原筛选设置。
    </div>}
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
    : <Empty title={needle ? "暂无匹配的股票" : "暂无符合筛选条件的股票"}
      detail={needle ? "试试更短的股票代码或公司名称；搜索仅覆盖当前已发布的Net-Net预筛股票。" : "可调整筛选条件或切换到“全部预筛”视图。"}/>}
    {filtered.length > visibleCount && <button type="button" className="filter-button screener-load-more"
      onClick={()=>setVisibleCount(n=>n+100)}>
      再显示100条（已显示{Math.min(visibleCount,filtered.length)} / {filtered.length}）
    </button>}
    <div className="panel-foot">筛选于本地即时完成；股票详情、策略定义及历史结果仍以数据库真实记录为准。</div>
  </section>;
}
