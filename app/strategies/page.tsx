import Link from "next/link";
import { ArrowUpRight, Layers3 } from "lucide-react";
import { strategies, strategyStatusLabel, requirementLabels } from "@/lib/strategies";

export const metadata = {title:"策略中心"};

export default function StrategiesPage() {
  const enabled = strategies.filter(x => x.status === "live").length;
  const research = strategies.filter(x => x.status === "research").length;
  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">STRATEGY WORKSPACE / REUSABLE CATALOG</div>
      <h1>策略中心</h1>
      <p className="page-lead">资产折价、技术趋势、超跌反转及质量动量策略。</p>
    </div><span className="date-tag"><Layers3 size={17}/> 已登记 {strategies.length} 套策略</span></div>
    <div className="stats-grid">
      <div className="stat-card"><span>策略总数</span><strong>{strategies.length}</strong><small>按策略独立管理</small></div>
      <div className="stat-card"><span>每日筛选已启用</span><strong className="stat-green">{enabled}</strong><small>每日运行</small></div>
      <div className="stat-card"><span>研究测试</span><strong>{research}</strong><small>规则验证中</small></div>
      <div className="stat-card"><span>等待开发</span><strong>{strategies.length-enabled-research}</strong><small>待上线</small></div>
    </div>
    <section className="panel">
      <div className="panel-heading"><div><div className="section-kicker">STRATEGY LIBRARY</div>
        <h2>选股策略库</h2>
        <p>查看各策略的筛选条件、参数与数据要求。</p>
      </div></div>
      <div className="strategy-catalog-grid">
        {strategies.map(s=><article key={s.id} className="strategy-catalog-card">
          <div className="strategy-card-header"><span className="strategy-family">{s.family}</span>
            <span className={`strategy-state strategy-state-${s.status}`}>{strategyStatusLabel[s.status]}</span>
          </div>
          <h3>{s.name}</h3>
          <p>{s.description}</p>
          <div className="strategy-meta">ID：{s.id} · v{s.version}</div>
          <div className="strategy-requirements">
            {s.requirements.slice(0,3).map(req=><span key={req}>{requirementLabels[req] ?? req}</span>)}
            {s.requirements.length>3 && <span>+{s.requirements.length-3}类数据</span>}
          </div>
          <Link className="inline-link" href={`/strategies/${encodeURIComponent(s.id)}`}>
            查看策略详情 <ArrowUpRight size={15}/>
          </Link>
        </article>)}
      </div>
    </section>
  </main>;
}
