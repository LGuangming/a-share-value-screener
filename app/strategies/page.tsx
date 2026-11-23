import Link from "next/link";
import { ArrowUpRight, Layers3, SlidersHorizontal, ShieldCheck } from "lucide-react";
import { strategies, strategyStatusLabel, requirementLabels } from "@/lib/strategies";

export const metadata = {title:"策略中心"};

export default function StrategiesPage() {
  const enabled = strategies.filter(x => x.status === "live").length;
  const research = strategies.filter(x => x.status === "research").length;
  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">STRATEGY WORKSPACE / REUSABLE CATALOG</div>
      <h1>策略中心</h1>
      <p className="page-lead">以独立策略定义和可追溯数据源构建选股方法；当前Net-Net正常运行，其它策略在验证后逐步启用。</p>
    </div><span className="date-tag"><Layers3 size={17}/> 已登记 {strategies.length} 套策略</span></div>
    <div className="stats-grid">
      <div className="stat-card"><span>策略总数</span><strong>{strategies.length}</strong><small>统一策略ID和版本</small></div>
      <div className="stat-card"><span>每日筛选已启用</span><strong className="stat-green">{enabled}</strong><small>生产运行，不等于已完成历史回测</small></div>
      <div className="stat-card"><span>工程研究</span><strong>{research}</strong><small>仅用于开发和验证执行逻辑</small></div>
      <div className="stat-card"><span>等待开发</span><strong>{strategies.length-enabled-research}</strong><small>先补齐历史时点数据再开放</small></div>
    </div>
    <section className="panel">
      <div className="panel-heading"><div><div className="section-kicker">STRATEGY LIBRARY</div>
        <h2>选股策略库</h2>
        <p>每套策略拥有独立ID、版本、所需数据和参数。未来新增策略不需要重新设计网站菜单和回测框架。</p>
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
            查看规则与配置草稿 <ArrowUpRight size={15}/>
          </Link>
        </article>)}
      </div>
    </section>
    <section className="panel"><div className="section-kicker">PLATFORM PRINCIPLES</div>
      <h2>策略扩展原则</h2>
      <div className="strategy-principles">
        <div><ShieldCheck size={18}/><strong>独立策略，不互相污染</strong>
          <p>每个策略必须独立标识版本与运行结果，不能把Net-Net交易观察当作另一套策略的回测证明。</p></div>
        <div><SlidersHorizontal size={18}/><strong>先配置、再验证、后启用</strong>
          <p>未上线的策略只允许查看条件和准备参数草稿；完整历史数据与交易执行验证通过后才允许正式发布选股结果。</p></div>
      </div>
    </section>
  </main>;
}
