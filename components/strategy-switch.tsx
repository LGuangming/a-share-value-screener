"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowUpRight, Database, SlidersHorizontal } from "lucide-react";
import { findStrategy, requirementLabels, strategies, strategyStatusLabel } from "@/lib/strategies";
import StrategyMethodology from "@/components/strategy-methodology";

/** Changing strategy only switches the local research view: no database query or route wait. */
export default function StrategySwitch({
  mode, requestedId, children, summary,
}: {
  mode: "screen" | "signals" | "methodology";
  requestedId?: string;
  children?: ReactNode;
  summary?: ReactNode;
}) {
  const [selectedId, setSelectedId] = useState(
    mode==="screen" && requestedId==="summary" ? "summary" : findStrategy(requestedId ?? "")?.id ?? "netnet_v1"
  );

  useEffect(() => {
    // An explicit URL parameter owns the selection. A bare "/" ALWAYS
    // starts with the live Net-Net screener, not a stale research selection.
    const sync = () => {
      const id = new URLSearchParams(window.location.search).get("strategy");
      setSelectedId(mode==="screen" && id==="summary" ? "summary" : findStrategy(id ?? "")?.id ?? "netnet_v1");
    };
    sync();
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, [requestedId,mode]);

  const selected = findStrategy(selectedId) ?? strategies[0];
  const showSummary = mode === "screen" && selectedId === "summary";
  const canShowLive = selected.status === "live" && selected.id === "netnet_v1";

  function chooseStrategy(id: string) {
    if (!findStrategy(id) && !(mode==="screen" && id==="summary")) return;
    setSelectedId(id);
    // The URL persists selection across pages; do not persist an inactive
    // strategy as the default when the visitor opens the home page.
    const url = new URL(window.location.href);
    url.searchParams.set("strategy", id);
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    window.dispatchEvent(new CustomEvent("a-share-strategy-change", { detail: id }));
  }

  return <>
    <section className="panel strategy-switch-panel" aria-label="策略选择">
      <div className="strategy-switch-top">
        <div>
          <div className="section-kicker">STRATEGY SELECTOR</div>
          <h2>{mode === "screen" ? "选股策略" : mode === "signals" ? "交易观察策略" : "筛选方法策略"}</h2>
          <p>切换策略查看规则、参数和数据条件。</p>
        </div>
        {!showSummary && <span className={`strategy-state strategy-state-${selected.status}`}>{strategyStatusLabel[selected.status]}</span>}
      </div>
      <div className="strategy-switch-options" role="tablist" aria-label="研究策略">
        {mode==="screen" && <button type="button" role="tab" aria-selected={showSummary} className={`strategy-switch-option${showSummary?" is-selected":""}`} onClick={()=>chooseStrategy("summary")}><strong>策略汇总</strong></button>}
        {strategies.map(strategy =>
          <button
            key={strategy.id}
            type="button"
            role="tab"
            aria-selected={!showSummary && strategy.id === selected.id}
            className={`strategy-switch-option${!showSummary && strategy.id === selected.id ? " is-selected" : ""}`}
            onClick={() => chooseStrategy(strategy.id)}
          >
            <strong>{strategy.name}</strong>
            <small>{strategy.family}</small>
          </button>
        )}
      </div>
      {!showSummary && <details className="strategy-context" key={selected.id}>
        <summary>策略参数与数据条件</summary>
        <div className="strategy-context-body">
      <div className="strategy-switch-summary">
        <div>
          <span className="strategy-switch-label">当前策略 · v{selected.version}</span>
          <strong>{selected.name}</strong>
          <p>{selected.description}</p>
        </div>
        <div>
          <span className="strategy-switch-label">股票池</span>
          <p>{selected.universe}</p>
        </div>
      </div>
      <div className="strategy-switch-details">
        <div><SlidersHorizontal size={15}/><strong>核心参数</strong>
          {selected.parameters.map(p => <span key={p.key}>{p.name}：{p.default}{p.unit}</span>)}
        </div>
        <div><Database size={15}/><strong>数据条件</strong>
          {selected.requirements.map(k => <span key={k}>{requirementLabels[k] ?? k}</span>)}
        </div>
      </div>
      <div className="strategy-switch-footer">
        <Link href={`/strategies/${encodeURIComponent(selected.id)}`} className="inline-link">
          查看完整规则与参数 <ArrowUpRight size={14}/>
        </Link>
        <span>策略ID：{selected.id}</span>
      </div>
        </div>
      </details>}
    </section>
    {showSummary ? <div className="strategy-view-enter">{summary}</div> : mode === "methodology"
      ? <StrategyMethodology key={selected.id} strategy={selected}/>
      : <>
          <div className={canShowLive ? "strategy-view-enter" : "strategy-inactive"} hidden={!canShowLive}>{children}</div>
          {!canShowLive && <section key={selected.id} className="panel strategy-unavailable strategy-view-enter" aria-live="polite">
      <div className="section-kicker">STRATEGY DATA STATUS</div>
      <h2>{selected.name} · {mode === "screen" ? "选股结果" : "交易观察"}</h2>
      <p>尚未发布{mode === "screen" ? "每日筛选结果" : "交易观察信号"}。</p>
      <Link className="inline-link" href={`/strategies/${encodeURIComponent(selected.id)}`}>
        查看策略详情 <ArrowUpRight size={14}/>
      </Link>
    </section>}
        </>}
  </>;
}
