"use client";

import { useMemo, useState } from "react";
import { Copy } from "lucide-react";
import type { StrategyDefinition } from "@/lib/strategies";

export default function StrategyDraft({strategy}: {strategy: StrategyDefinition}) {
  const [values,setValues] = useState<Record<string,number>>(() =>
    Object.fromEntries(strategy.parameters.map(p => [p.key,p.default]))
  );
  const [message,setMessage]=useState("");
  const valid = strategy.parameters.every(p => {
    const value=values[p.key];
    return Number.isFinite(value) && value>=p.min && value<=p.max &&
      (p.type!=="integer" || Number.isInteger(value));
  });
  const config=useMemo(()=>JSON.stringify({
    strategy_id:strategy.id, version:strategy.version,
    status:"draft_not_executable",
    parameters:values,
  },null,2),[strategy,values]);

  const copy=async()=>{
    try {
      await navigator.clipboard.writeText(config);
      setMessage("参数配置已复制。");
    } catch {
      setMessage("复制失败，可选中下方文本自行复制。");
    }
  };
  return <div className="strategy-builder">
    <div className="strategy-builder-grid">
      {strategy.parameters.map(p=><label key={p.key} className="strategy-builder-field">
        <span>{p.name} <small>({p.unit})</small></span>
        <input type="number" min={p.min} max={p.max} step={p.type==="integer"?1:0.05}
          value={Number.isFinite(values[p.key])?values[p.key]:""}
          onChange={event=>{
            const input=event.target.value;
            setMessage("");
            setValues(prev=>({...prev,[p.key]:input===""?NaN:Number(input)}));
          }}/>
        <small>可配置范围：{p.min}–{p.max}</small>
      </label>)}
    </div>
    <p className="note">当前为参数预览。</p>
    <textarea className="strategy-draft-json" aria-label="策略配置草稿JSON" readOnly rows={Math.max(6,strategy.parameters.length*2+5)} value={config}/>
    <button type="button" className="filter-button" disabled={!valid} onClick={copy}>
      <Copy size={15}/> 复制参数
    </button>
    {!valid&&<p className="strategy-error">请将所有数值调整至允许范围内。</p>}
    {message&&<p className="strategy-message" role="status">{message}</p>}
  </div>;
}
