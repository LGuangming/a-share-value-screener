import Link from "next/link";
import { ArrowUpRight, Beaker, CalendarDays, Database, GitBranch, ShieldAlert } from "lucide-react";
import { findStrategy, strategies, strategyStatusLabel } from "@/lib/strategies";

export const metadata={title:"历史策略回测"};
export const dynamic="force-dynamic";

type Query={strategy?:string};
export default async function BacktestPage({searchParams}:{searchParams:Promise<Query>}) {
  const query=await searchParams;
  const active=findStrategy(query.strategy??"")??strategies[0];
  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">BACKTEST LAB / MULTI-STRATEGY ENGINE</div>
      <h1>历史回测实验室</h1>
      <p className="page-lead">统一事件驱动交易模拟，策略规则独立注册。真实收益仅在历史股票池、财报时点和成交条件核验后发布。</p>
    </div><span className="date-tag"><Beaker size={16}/> 工程验证阶段</span></div>
    <section className="panel signal-alert"><ShieldAlert size={20}/><p>
      尚未具备可正式发布的Net-Net历史收益数据。
      当前Python回测内核仅供执行逻辑验证：T日收盘生成信号、T+1开盘尝试成交、按持仓期卖出，
      并检查涨跌停、停牌、T+1、100股整手及费用。未经复权和历史ST核验的实验结果不会展示为策略业绩。
    </p></section>
    <div className="detail-grid">
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
      </section>
      <section className="panel">
        <div className="section-kicker">HISTORY DATA PIPELINE</div>
        <h2>数据工作流</h2>
        <div className="quality-item"><Database size={18}/><div><strong>Neon容量审计</strong>
          <span>独立只读作业读取真实表占用；不会向数据库导入多年历史行情。</span></div></div>
        <div className="quality-item"><CalendarDays size={18}/><div><strong>2025年Parquet样本</strong>
          <span>独立工作流按交易日采集全市场OHLCV，按月压缩和校验。默认仅存短期GitHub Artifact。</span></div></div>
        <div className="quality-item"><GitBranch size={18}/><div><strong>正式历史回测</strong>
          <span>等待2020年以来的复权、历史ST、历史财报及交易限制数据通过核验。</span></div></div>
      </section>
    </div>
    <section className="panel">
      <div className="panel-heading"><div>
        <div className="section-kicker">VALIDATION CHECKLIST</div>
        <h2>可执行程序与正式策略回测的区别</h2>
      </div></div>
      <div className="backtest-phase-grid">
        <div><span>阶段 01</span><h3>交易内核</h3><p>Python模拟现金、持仓、买卖和费用。以合成数据测试执行一致性。</p><strong>已编写，需通过CI验证</strong></div>
        <div><span>阶段 02</span><h3>真实数据工程样本</h3><p>先测2025年全市场行情压缩率与存储体积；只用于工程可行性。</p><strong>独立工作流</strong></div>
        <div><span>阶段 03</span><h3>可信回测收益</h3><p>补齐历史财报披露、复权及ST变化，校验退市、停牌和成交成本。</p><strong>尚未发布</strong></div>
      </div>
      <div className="review-footnote">策略性能指标（收益、最大回撤、胜率、盈亏比）在具备可验证的历史时点数据后才显示；不存在默认模拟的优异收益。</div>
    </section>
  </main>;
}
