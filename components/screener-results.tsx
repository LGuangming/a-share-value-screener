"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import Link from "next/link";
import { ArrowRight, ArrowUpRight, ArrowUpDown, ChevronDown, Download, Filter, Search, Settings2 } from "lucide-react";
import type { ScreenerRow } from "@/lib/data";
import { fixed, moneyCny, pct, signalLabel, stateLabel } from "@/lib/format";
import { Empty } from "@/components/empty";
import ToolbarFlyout from "@/components/toolbar-flyout";
import {WatchButton} from "@/components/workspace-preferences";
import {PreviewButton} from "@/components/stock-preview";

export type ScreenTableRow = Pick<ScreenerRow,
  "ts_code" | "name" | "industry" | "price" | "pb" | "market_cap_yuan" |
  "ncav_coverage" | "stress_coverage" | "is_candidate" | "status" | "signal"
>;

type Filters = { q?: string; view?: string; order?: string; max_price?: string;
  min_coverage?: string; max_pb?: string };

const numericColumns = [
  {key:"price", label:"收盘价"},
  {key:"market_cap_yuan", label:"总市值"},
  {key:"ncav_coverage", label:"NCAV / 市值"},
  {key:"stress_coverage", label:"压力价值覆盖率"},
  {key:"pb", label:"市净率"},
] as const;
type NumericKey = typeof numericColumns[number]["key"];
type ColumnKey = NumericKey | "status" | "signal";
const optionalColumns: ReadonlyArray<{key:ColumnKey;label:string}> = [
  ...numericColumns, {key:"status",label:"初筛状态"}, {key:"signal",label:"交易观察"}
];
function displayValue(row: ScreenTableRow, key: ColumnKey) {
  if (key === "status") return stateLabel(row.status);
  if (key === "signal") return signalLabel(row.signal);
  if (key === "market_cap_yuan") return moneyCny(row[key]);
  if (key === "ncav_coverage" || key === "stress_coverage") return pct(row[key],1);
  return fixed(row[key]);
}
function csvCell(value: unknown) {
  const raw = String(value ?? "");
  const safe = /^[=+@\t\r\n]/.test(raw) || (/^-/.test(raw) && !/^-\d+(\.\d+)?$/.test(raw)) ? "'" + raw : raw;
  return '"' + safe.replace(/"/g,'""') + '"';
}
function tagClass(status: string) {
  if (status === "NET_NET") return "pill pill-good";
  if (status === "DATA_GAP" || status === "STALE_FINANCIALS") return "pill pill-warn";
  return "pill pill-neutral";
}

/** Search the published Net-Net universe instantly, with exact/prefix matches first. */
function matchRank(row: ScreenTableRow, needle: string): number | null {
  const code = row.ts_code.toLowerCase();
  const name = row.name.toLocaleLowerCase();
  if (code === needle) return 0;
  if (name === needle) return 1;
  if (code.startsWith(needle)) return 2;
  if (name.startsWith(needle)) return 3;
  if (code.includes(needle)) return 4;
  if (name.includes(needle)) return 5;
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
  const [order, setOrder] = useState<NumericKey>(initial.order === "stress" ? "stress_coverage" : "ncav_coverage");
  const [ascending, setAscending] = useState(false);
  const [columns, setColumns] = useState<ColumnKey[]>(optionalColumns.map(c=>c.key));
  const [columnMenu, setColumnMenu] = useState(false);
  const [visibleCount, setVisibleCount] = useState(100);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(-1);
  const suggestionListId = useId();
  const searchAnchor=useRef<HTMLDivElement>(null);
  const columnAnchor=useRef<HTMLDivElement>(null);
  const needle = q.trim().toLocaleLowerCase();
  useEffect(()=>{const close=()=>{setColumnMenu(false);setSuggestionsOpen(false);};window.addEventListener("a-share-strategy-change",close);return()=>window.removeEventListener("a-share-strategy-change",close);},[]);


  const viewLabel = view === "strict" ? "严格候选" : view === "gaps" ? "数据缺口" : "全部预筛";

  // Scope each search to the exact row set that the active tab can show.
  // Apply price/PB/NCAV controls *before* searching; search never expands
  // into another tab or silently bypasses the selected thresholds.
  const tabRows = useMemo(() => rows.filter(row => {
    if (row.price !== null && row.price > maxPrice) return false;
    if (row.pb !== null && row.pb > maxPb) return false;
    if (view === "strict") return row.is_candidate && row.ncav_coverage !== null
      && row.ncav_coverage >= minCoverage;
    if (view === "gaps") return row.status === "DATA_GAP" || row.status === "STALE_FINANCIALS";
    return true;
  }).sort((a,b)=>{
    const av=a[order], bv=b[order];
    if(av===null) return bv===null ? 0 : 1;
    if(bv===null) return -1;
    return ascending ? av-bv : bv-av;
  }), [rows, view, order, ascending, maxPrice, maxPb, minCoverage]);

  const matches = useMemo(() => {
    if (!needle) return [];
    return tabRows.map((row, index) => ({ row, index, rank: matchRank(row, needle) }))
      .filter((match): match is { row: ScreenTableRow; index: number; rank: number } =>
        match.rank !== null)
      .sort((a, b) => a.rank - b.rank || a.index - b.index)
      .map(match => match.row);
  }, [tabRows, needle]);

  const filtered = needle ? matches : tabRows;
  const suggestions = matches.slice(0, 8);
  const shownColumns=optionalColumns.filter(c=>columns.includes(c.key));
  function sortBy(key:NumericKey) {
    if(order===key) setAscending(v=>!v);
    else {setOrder(key);setAscending(false);}
    setVisibleCount(100);
  }
  function exportRows() {
    const header=["证券代码","股票名称","所属行业",...shownColumns.map(c=>c.label)];
    const lines=[header.map(csvCell).join(","),...filtered.map(row=>[
      row.ts_code,row.name,row.industry,...shownColumns.map(c=>
        c.key==="status"||c.key==="signal" ? displayValue(row,c.key):row[c.key])
    ].map(csvCell).join(","))];
    const url=URL.createObjectURL(new Blob(["\ufeff"+lines.join("\r\n")],{type:"text/csv;charset=utf-8"}));
    const a=document.createElement("a");a.href=url;a.download="value-radar-netnet.csv";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }

  function setFilter(_param: string, _value: string, update: () => void) {
    // Filter views and keystrokes are local-only UI state. Updating the
    // URL's searchParams here may route through Next.js App Router and
    // momentarily mount the global loading boundary while the server page
    // re-evaluates. Explicit links can still initialize filters from a URL.
    update();
    setVisibleCount(100);
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

  function handleSearchKeyDown(event: KeyboardEvent<HTMLInputElement>) {
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
      <p>共 {filtered.length.toLocaleString("zh-CN")} 只 · 当前显示 {Math.min(visibleCount,filtered.length).toLocaleString("zh-CN")} 只<span className="filter-local-hint" style={{marginInlineStart:10,whiteSpace:"nowrap",verticalAlign:"middle"}}><Filter size={13}/> 即时筛选</span></p>
    </div><Link className="inline-link" href="/methodology">查看筛选口径 <ArrowUpRight size={15}/></Link></div>
    <div className="filters">
      <div className="tabs" role="tablist" aria-label="筛选视图">
        {([["strict","严格候选"],["all","全部预筛"],["gaps","数据缺口"]] as const)
          .map(([key,label]) => <button type="button" role="tab" aria-selected={view === key}
            className={`tab${view === key ? " active" : ""}`} key={key}
            onClick={() => setFilter("view",key,()=>setView(key))}>{label}</button>)}
      </div>
      <div className="filter-form">
        <div className="stock-search" ref={searchAnchor}>
          <div className="search-field"><Search size={16}/>
            <input aria-label="搜索股票" autoComplete="off" type="search"
              placeholder={`在${viewLabel}中搜索名称 / 代码`} value={q}
              role="combobox" aria-autocomplete="list"
              aria-expanded={Boolean(needle && suggestionsOpen && suggestions.length)}
              aria-controls={needle && suggestionsOpen ? suggestionListId : undefined}
              aria-activedescendant={highlightedIndex >= 0 && suggestionsOpen && suggestions[highlightedIndex]
                ? `${suggestionListId}-${highlightedIndex}` : undefined}
              onFocus={() => setSuggestionsOpen(true)}
              onBlur={() => {setSuggestionsOpen(false);setHighlightedIndex(-1);}}
              onKeyDown={handleSearchKeyDown}
              onChange={e => updateSearch(e.target.value)}/>
          </div>
          {needle && suggestionsOpen && <ToolbarFlyout anchor={searchAnchor} width={300}><div
            id={suggestionListId} role="listbox" aria-label={`${viewLabel}股票搜索结果`}
            className="stock-suggestions">
            <div className="stock-suggestions-heading">
              {viewLabel} · 匹配 {matches.length} 只
              {matches.length > 8 ? " · 显示前8只" : ""}
            </div>
            {suggestions.length ? suggestions.map((row,i) => <button type="button"
              id={`${suggestionListId}-${i}`}
              role="option" aria-selected={highlightedIndex === i}
              className={`stock-suggestion${highlightedIndex === i ? " is-highlighted" : ""}`}
              key={row.ts_code}
              onMouseDown={e => e.preventDefault()}
              onClick={() => chooseSuggestion(row.ts_code)}>
              <span className="stock-suggestion-name">{row.name}</span>
              <span className="stock-suggestion-code">{row.ts_code}</span>
            </button>) : <p className="stock-suggestion-empty">当前选项卡没有匹配股票，请缩短搜索词。</p>}
          </div></ToolbarFlyout>}
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
          onChange={e=>setFilter("order",e.target.value,()=>{setOrder(e.target.value as NumericKey);setAscending(false);})}>
          {numericColumns.map(c=><option key={c.key} value={c.key}>{c.label}排序</option>)}
        </select>
        <div className="column-control" ref={columnAnchor} onKeyDown={e=>{if(e.key==="Escape")setColumnMenu(false);}}>
          <button type="button" className="tool-button" aria-expanded={columnMenu} onClick={()=>setColumnMenu(v=>!v)}><Settings2 size={14}/> 列设置 <ChevronDown size={12}/></button>
          {columnMenu && <ToolbarFlyout anchor={columnAnchor} align="right" width={190}><div className="column-menu">
            {optionalColumns.map(c=><label key={c.key}><input type="checkbox" checked={columns.includes(c.key)} onChange={e=>setColumns(prev=>e.target.checked?[...prev,c.key]:prev.filter(k=>k!==c.key))}/>{c.label}</label>)}
            <button type="button" className="tool-button" onClick={()=>{setColumns(optionalColumns.map(c=>c.key));setColumnMenu(false);}}>恢复默认</button>
          </div></ToolbarFlyout>}
        </div>
        <button type="button" className="tool-button" onClick={exportRows} disabled={!filtered.length}><Download size={14}/> 导出 CSV</button>
      </div>
    </div>
    {needle && <div className="stock-search-feedback" role="status" aria-live="polite">
      {viewLabel} · 匹配 {matches.length} 只 / 当前 {tabRows.length} 只
    </div>}
    {filtered.length ? <div className="table-scroll"><table className="results-table">
      <thead><tr><th className="security-col">股票 / 行业</th>
        {shownColumns.map(c=><th key={c.key} className={c.key==="status"||c.key==="signal"?"":"numeric-col"}
          aria-sort={order===c.key?(ascending?"ascending":"descending"):undefined}>
          {c.key==="status"||c.key==="signal"?c.label:<button type="button" className="sort-heading" onClick={()=>sortBy(c.key as NumericKey)}>{c.label}<ArrowUpDown size={12} className={order===c.key?"sort-active":""}/></button>}
        </th>)}<th aria-label="研究操作">操作</th></tr></thead>
      <tbody>{filtered.slice(0,visibleCount).map(row=><tr key={row.ts_code}>
        <td className="security-col"><Link className="stock-name" href={`/stocks/${encodeURIComponent(row.ts_code)}`}>
          {row.name}<ArrowUpRight size={13}/></Link>
          <div className="stock-meta">{row.ts_code} · {row.industry || "行业待补充"}</div></td>
        {shownColumns.map(c=><td key={c.key} className={c.key==="status"||c.key==="signal"?"":"number numeric-col"}>
          {c.key==="status"?<span className={tagClass(row.status)}>{displayValue(row,c.key)}</span>:
           <span className={c.key==="ncav_coverage" && row.ncav_coverage!==null && row.ncav_coverage>=1.25?"highlight":""}>{displayValue(row,c.key)}</span>}
        </td>)}
        <td><div className="row-actions"><WatchButton stock={row}/><PreviewButton stock={row}/><Link aria-label={`查看${row.name}`} href={`/stocks/${encodeURIComponent(row.ts_code)}`}>
          <ArrowRight size={16}/></Link></div></td>
      </tr>)}</tbody></table></div>
    : <Empty title={needle ? "暂无匹配的股票" : "暂无符合筛选条件的股票"}
      detail={needle ? `${viewLabel}中没有匹配的股票，试试更短的名称或代码。` : "可调整筛选条件或切换到“全部预筛”视图。"}/>}
    {filtered.length > visibleCount && <button type="button" className="filter-button screener-load-more"
      onClick={()=>setVisibleCount(n=>n+100)}>
      再显示100条（已显示{Math.min(visibleCount,filtered.length)} / {filtered.length}）
    </button>}
  </section>;
}
