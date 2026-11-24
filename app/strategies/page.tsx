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
    <section className="panel strategy-library">
      <div className="panel-heading"><div><div className="section-kicker">STRATEGY LIBRARY</div>
        <h2>选股策略库</h2>
        <p>查看各策略的筛选条件、参数与数据要求。</p>
      </div></div>
      <div className="table-scroll">
        <table className="results-table strategy-library-table">
          <thead><tr><th>策略 / 版本</th><th>分类</th><th>策略规则</th><th>数据要求</th><th>运行状态</th><th>持仓周期</th><th>回测</th><th>详情</th></tr></thead>
          <tbody>{strategies.map(s=><tr key={s.id}>
            <td><Link className="stock-name" href={`/strategies/${encodeURIComponent(s.id)}`}>{s.name}</Link>
              <div className="stock-meta">v{s.version} · {s.id}</div></td>
            <td>{s.family}</td>
            <td className="strategy-description">{s.description}</td>
            <td className="strategy-description">{s.requirements.map(req=>requirementLabels[req] ?? req).join("、")}</td>
            <td><span className={`strategy-state strategy-state-${s.status}`}>{strategyStatusLabel[s.status]}</span></td>
            <td>{s.id==="oversold_reversion_v0"?"5–20交易日（拟）":"—"}</td>
            <td><Link className="inline-link" href={`/backtest?strategy=${encodeURIComponent(s.id)}`}>查看回测</Link></td>
            <td><Link className="inline-link" href={`/strategies/${encodeURIComponent(s.id)}`} aria-label={`查看${s.name}详情`}>查看 <ArrowUpRight size={14}/></Link></td>
          </tr>)}</tbody>
        </table>
      </div>
    </section>
  </main>;
}
