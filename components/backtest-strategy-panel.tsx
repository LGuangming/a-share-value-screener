"use client";

import Link from "next/link";
import {useEffect,useState} from "react";
import {ArrowUpRight,Database} from "lucide-react";
import {findStrategy,strategies,strategyStatusLabel,requirementLabels} from "@/lib/strategies";
import BacktestConfig from "@/components/backtest-config";

export default function BacktestStrategyPanel({initialId}:{initialId:string}){
  const [id,setId]=useState(initialId);
  useEffect(()=>setId(initialId),[initialId]);
  useEffect(()=>{
    const sync=()=>setId(findStrategy(new URLSearchParams(window.location.search).get("strategy")??"")?.id??strategies[0].id);
    window.addEventListener("popstate",sync);
    return()=>window.removeEventListener("popstate",sync);
  },[]);
  const active=findStrategy(id)??strategies[0];
  function choose(next:string){
    if(!findStrategy(next))return;
    setId(next);
    const url=new URL(window.location.href);
    url.searchParams.set("strategy",next);
    window.history.replaceState(window.history.state,"",url.pathname+url.search+url.hash);
  }
  return <section className="panel">
    <h2>选择回测策略</h2>
    <div className="backtest-strategy-form">
      <label htmlFor="backtest-strategy">策略类型</label>
      <select id="backtest-strategy" name="strategy" value={active.id} onChange={e=>choose(e.target.value)}>
        {strategies.map(s=><option value={s.id} key={s.id}>{s.name}</option>)}
      </select>
    </div>
    <div className="strategy-selected">
      <span>{active.family} · v{active.version}</span>
      <h3>{active.name}</h3>
      <p>{active.description}</p>
      <div>状态：<strong>{strategyStatusLabel[active.status]}</strong></div>
      <Link className="inline-link" href={"/strategies/"+encodeURIComponent(active.id)}>查看策略参数 <ArrowUpRight size={14}/></Link>
    </div>
    <BacktestConfig strategy={active}/>
    <section className="backtest-data-requirements">
      <h3><Database size={15}/> 所选策略数据要求</h3>
      <div>{active.requirements.map(req=><span key={req}>{requirementLabels[req]??req}</span>)}</div>
    </section>
  </section>;
}
