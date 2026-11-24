import Link from "next/link";
import BacktestConfig from "@/components/backtest-config";
import ResearchTabs from "@/components/research-tabs";
import { ArrowUpRight, Beaker, CalendarDays, Database, GitBranch } from "lucide-react";
import { findStrategy, strategies, strategyStatusLabel } from "@/lib/strategies";

export const metadata={title:"历史策略回测"};
export const dynamic="force-dynamic";

type Query={strategy?:string};
export default async function BacktestPage({searchParams}:{searchParams:Promise<Query>}) {
  const query=await searchParams;
  const active=findStrategy(query.strategy??"")??strategies[0];
  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">STRATEGY BACKTESTS</div>
      <h1>策略回测</h1>
      <p className="page-lead">选择策略查看回测状态及历史数据条件。</p>
    </div><span className="date-tag"><Beaker size={16}/> 结果待发布</span></div>
    <div className="detail-grid backtest-workspace">
      <section className="panel">
        <div className="section-kicker">SELECT RESEARCH STRATEGY</div>
        <h2>选择回测策略</h2>
        <form className="backtest-strategy-form" method="get" action="/backtest">
          <label htmlFor="backtest-strategy">策略类型</label>
          <select id="backtest-strategy" name="strategy" defaultValue={active.id}>
            {strategies.map(s=><option value={s.id} key={s.id}>{s.name}</option>)}
          </select>
          <button className="filter-button" type="submit">查看策略状态</button>
        </form>
        <div className="strategy-selected">
          <span>{active.family} · v{active.version}</span>
          <h3>{active.name}</h3>
          <p>{active.description}</p>
          <div>状态：<strong>{strategyStatusLabel[active.status]}</strong></div>
          <Link className="inline-link" href={`/strategies/${encodeURIComponent(active.id)}`}>
            查看参数草稿 <ArrowUpRight size={14}/>
          </Link>
        </div>
        <BacktestConfig key={active.id} strategy={active}/>
      </section>
      <section className="panel backtest-results-panel">
        <h2>回测绩效</h2>
        <div className="backtest-metrics">
          {["累计收益率","年化收益率","最大回撤","夏普比率"].map(label=><div key={label}><span>{label}</span><strong>—</strong></div>)}
        </div>
        <ResearchTabs label="回测结果" tabs={[
          {id:"equity",label:"净值与基准",content:<div className="backtest-empty"><Beaker size={24}/><strong>暂无已发布的历史回测绩效。</strong></div>},
          {id:"drawdown",label:"回撤",content:<div className="backtest-empty">暂无回撤序列</div>},
          {id:"monthly",label:"月度收益",content:<div className="backtest-empty">暂无月度收益记录</div>},
          {id:"trades",label:"交易明细",content:<div className="backtest-empty">暂无成交记录</div>},
          {id:"holdings",label:"持仓分析",content:<div className="backtest-empty">暂无持仓记录</div>},
        ]}/>
        <div className="section-kicker">BACKTEST DATA</div>
        <h2>回测数据条件</h2>
        <div className="quality-item"><Database size={18}/><div><strong>历史行情</strong>
          <span>复权价格、成交量及历史股票池。</span></div></div>
        <div className="quality-item"><CalendarDays size={18}/><div><strong>财务披露</strong>
          <span>历史资产负债表、利润表、现金流量表及披露日期。</span></div></div>
        <div className="quality-item"><GitBranch size={18}/><div><strong>交易限制</strong>
          <span>涨跌停、停复牌、T+1、交易成本及历史风险警示状态。</span></div></div>
      </section>
    </div>
  </main>;
}
