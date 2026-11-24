"use client";

import { useState } from "react";
import { Download } from "lucide-react";
import type { StrategyDefinition } from "@/lib/strategies";

export default function BacktestConfig({strategy}:{strategy:StrategyDefinition}) {
  const [start,setStart]=useState("");
  const [end,setEnd]=useState("");
  const [capital,setCapital]=useState("100000");
  const [cost,setCost]=useState("0.10");
  const [holding,setHolding]=useState("");
  const [params,setParams]=useState<Record<string,string>>(()=>Object.fromEntries(strategy.parameters.map(p=>[p.key,String(p.default)])));
  const valid=start!==""&&end!==""&&start<=end&&capital.trim()!==""&&Number.isFinite(Number(capital))&&Number(capital)>0&&cost.trim()!==""&&Number.isFinite(Number(cost))&&Number(cost)>=0&&Number(cost)<=100&&(!holding||Number.isInteger(Number(holding))&&Number(holding)>0)&&strategy.parameters.every(p=>params[p.key]?.trim()!==""&&Number.isFinite(Number(params[p.key]))&&Number(params[p.key])>=p.min&&Number(params[p.key])<=p.max&&(p.type!=="integer"||Number.isInteger(Number(params[p.key]))));
  function saveDraft(){
    if(!valid)return;
    const draft={status:"draft_not_executable",strategy_id:strategy.id,strategy_version:strategy.version,universe:strategy.universe,start_date:start,end_date:end,initial_capital_cny:Number(capital),assumed_cost_percent_per_side:Number(cost),holding_days:holding?Number(holding):null,parameters:Object.fromEntries(strategy.parameters.map(p=>[p.key,Number(params[p.key])]))};
    const url=URL.createObjectURL(new Blob([JSON.stringify(draft,null,2)],{type:"application/json"}));
    const a=document.createElement("a");a.href=url;a.download=strategy.id+"-backtest-draft.json";a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return <div className="backtest-config">
    <h3>实验参数草稿</h3>
    <label>股票池<span className="config-universe">{strategy.universe}</span></label>
    <label>起始日期<input type="date" value={start} max={end||undefined} onChange={e=>setStart(e.target.value)}/></label>
    <label>结束日期<input type="date" value={end} min={start||undefined} onChange={e=>setEnd(e.target.value)}/></label>
    <label>初始资金（元）<input type="number" min="1" value={capital} onChange={e=>setCapital(e.target.value)}/></label>
    <label>单边交易成本假设（%）<input type="number" min="0" max="100" step="0.01" value={cost} onChange={e=>setCost(e.target.value)}/></label>
    <label>持仓周期（交易日，可选）<input type="number" min="1" step="1" value={holding} onChange={e=>setHolding(e.target.value)} placeholder="未设定"/></label>
    {strategy.parameters.map(p=><label key={p.key}>{p.name}（{p.unit}）<input type="number" min={p.min} max={p.max} step={p.type==="integer"?1:"any"} value={params[p.key]} onChange={e=>setParams(v=>({...v,[p.key]:e.target.value}))}/></label>)}
    <button type="button" className="filter-button" disabled={!valid} onClick={saveDraft}><Download size={15}/> 导出参数草稿</button>
    <button type="button" className="tool-button" disabled>运行回测 · 暂未开放</button>
  </div>;
}
