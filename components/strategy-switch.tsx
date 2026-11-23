"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowUpRight, Database, SlidersHorizontal } from "lucide-react";
import { findStrategy, requirementLabels, strategies, strategyStatusLabel } from "@/lib/strategies";

/** Changing strategy only switches the local research view: no database query or route wait. */
export default function StrategySwitch({
  mode, requestedId, children,
}: {
  mode: "screen" | "signals";
  requestedId?: string;
  children: ReactNode;
}) {
  const [selectedId, setSelectedId] = useState(
    findStrategy(requestedId ?? "")?.id ?? "netnet_v1"
  );

  useEffect(() => {
    // Preserve the selected strategy when moving between the screener and signals.
    if (!requestedId) {
      try {
        const saved = sessionStorage.getItem("a_share_selected_strategy");
        if (saved && findStrategy(saved)) setSelectedId(saved);
      } catch { /* Session storage is optional; use the default strategy. */ }
    }
    const onHistoryChange = () => {
      const id = new URLSearchParams(window.location.search).get("strategy");
      if (id && findStrategy(id)) setSelectedId(id);
    };
    window.addEventListener("popstate", onHistoryChange);
    return () => window.removeEventListener("popstate", onHistoryChange);
  }, [requestedId]);

  const selected = findStrategy(selectedId) ?? strategies[0];
  const canShowLive = selected.status === "live" && selected.id === "netnet_v1";

  function chooseStrategy(id: string) {
    if (!findStrategy(id)) return;
    setSelectedId(id);
    try { sessionStorage.setItem("a_share_selected_strategy", id); } catch { /* optional */ }
    const url = new URL(window.location.href);
    url.searchParams.set("strategy", id);
    window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
  }

  return <>
    <section className="panel strategy-switch-panel" aria-label="策略选择">
      <div className="strategy-switch-top">
        <div>
          <div className="section-kicker">STRATEGY SELECTOR</div>
          <h2>{mode === "screen" ? "选股策略" : "交易观察策略"}</h2>
          <p>选择策略查看各自的筛选条件与结果；未上线的策略不会显示其它策略的股票。</p>
        </div>
        <span className={`strategy-state strategy-state-${selected.status}`}>{strategyStatusLabel[selected.status]}</span>
      </div>
      <div className="strategy-switch-options" role="tablist" aria-label="研究策略">
        {strategies.map(strategy =>
          <button
            key={strategy.id}
            type="button"
            role="tab"
            aria-selected={strategy.id === selected.id}
            className={`strategy-switch-option${strategy.id === selected.id ? " is-selected" : ""}`}
            onClick={() => chooseStrategy(strategy.id)}
          >
            <strong>{strategy.name}</strong>
            <small>{strategy.family}</small>
          </button>
        )}
      </div>
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
    </section>
    {canShowLive ? children : <section className="panel strategy-unavailable" aria-live="polite">
      <div className="section-kicker">STRATEGY DATA STATUS</div>
      <h2>{selected.name} · {mode === "screen" ? "选股结果" : "交易观察"}</h2>
      <p>该策略当前处于“{strategyStatusLabel[selected.status]}”状态，尚未发布独立的每日
        {mode === "screen" ? "筛选股票数据" : "交易观察信号"}。上方展示的是已登记的规则和数据要求，不是经过筛选的实盘股票。</p>
      <p className="note">不会将 Net-Net 的股票、NCAV阈值或交易信号用于此策略。{selected.notes}</p>
      <Link className="inline-link" href={`/strategies/${encodeURIComponent(selected.id)}`}>
        前往策略中心查看开发状态 <ArrowUpRight size={14}/>
      </Link>
    </section>}
  </>;
}
