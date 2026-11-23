import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { ArrowLeft, ClipboardCheck, Database } from "lucide-react";
import StrategyDraft from "@/components/strategy-draft";
import { findStrategy, strategies, requirementLabels, strategyStatusLabel } from "@/lib/strategies";

type Props = {params: Promise<{id:string}>};

export function generateStaticParams() {
  return strategies.map(s=>({id:s.id}));
}

export async function generateMetadata({params}:Props): Promise<Metadata> {
  const {id}=await params;
  const strategy=findStrategy(id);
  return {title:strategy? `${strategy.name} | 策略中心`:"策略不存在"};
}

export default async function StrategyDetailPage({params}:Props) {
  const {id}=await params;
  const strategy=findStrategy(id);
  if(!strategy) notFound();
  return <main className="page-container">
    <Link className="back-link" href="/strategies"><ArrowLeft size={16}/> 返回策略中心</Link>
    <div className="page-top"><div>
      <div className="eyebrow">STRATEGY DETAIL / VERSIONED DEFINITION</div>
      <h1>{strategy.name}</h1>
      <p className="page-lead">{strategy.description}</p>
    </div><span className={`strategy-state strategy-state-${strategy.status}`}>{strategyStatusLabel[strategy.status]}</span></div>
    <div className="detail-grid">
      <section className="panel"><div className="section-kicker">IMPLEMENTATION STATUS</div>
        <h2>策略状态与适用范围</h2>
        <dl className="detail-list">
          <div><dt>策略ID</dt><dd>{strategy.id}</dd></div>
          <div><dt>策略版本</dt><dd>{strategy.version}</dd></div>
          <div><dt>策略分类</dt><dd>{strategy.family}</dd></div>
          <div><dt>目标股票池</dt><dd className="strategy-wrap">{strategy.universe}</dd></div>
        </dl>
        <p className="note">{strategy.notes}</p>
      </section>
      <section className="panel"><div className="section-kicker">DATA DEPENDENCIES</div>
        <h2>数据条件</h2>
        <div className="strategy-data-list">{strategy.requirements.map(req=>
          <div key={req}><Database size={16}/><span>{requirementLabels[req]??req}</span></div>
        )}</div>
        <p className="note"></p>
      </section>
    </div>
    <section className="panel">
      <div className="panel-heading"><div><div className="section-kicker">PARAMETER DRAFT</div>
        <h2>策略参数草稿</h2>
        <p>调整并复制策略参数。</p>
      </div><ClipboardCheck size={18} className="muted"/></div>
      <StrategyDraft strategy={strategy}/>
    </section>
  </main>;
}
