"use client";

import { Activity, Clock3, Database, Scale, ShieldCheck, AlertTriangle } from "lucide-react";
import type { StrategyDefinition } from "@/lib/strategies";

type Step={title:string;detail:string};
type Guide={tag:string;title:string;subtitle:string;steps:Step[];limits:string[]};

const guides:Record<string,Guide>={
  netnet_v1:{
    tag:"NET CURRENT ASSET VALUE",
    title:"NCAV = 流动资产 − 全部负债",
    subtitle:"使用已披露的合并报表识别资产折价，不等于预计清算所得。",
    steps:[
      {title:"低价与PB预筛",detail:"初步按股价≤10元，PB≤1.2或PB缺失筛选；沪深主板、排除当前风险警示股票。预筛不是最终买入结论。"},
      {title:"净流动资产筛选",detail:"NCAV = 合并流动资产 − 合并全部负债；净流动资产/总市值≥1.25。注意不能只减流动负债。"},
      {title:"保守资产压力测试",detail:"应收账款、存货及预付款分别折价20%、30%、50%，另考虑少数股东权益；缺项不按零处理。"},
      {title:"经营及资产质量风险",detail:"独立标识经营现金流为负、持续亏损、高负债、应收或存货占比过高、陈旧财报及风险警示。"},
      {title:"技术面辅助观察",detail:"需有至少50个完整交易日收盘历史，再观察MA20、MA50；基本面风险优先于趋势信号。"},
    ],
    limits:["总市值以万元采集，计算时换算为人民币元；财报原始金额为元。","严格按实际披露时间控制可用性，公告当日信息保守延迟到下一交易日使用。","当前风险警示不能替代完整历史ST状态；压力价值不能替代资产评估。"],
  },
  ma_trend_research_v0:{
    tag:"PRICE TREND / ENGINEERING RESEARCH",
    title:"短期均线与长期均线趋势",
    subtitle:"仅定义技术趋势研究规则，尚未接入独立的正式每日筛选。",
    steps:[
      {title:"样本与价格序列",detail:"拟使用沪深主板历史交易日行情，并校对复权因子与公司行为，避免拆送股导致均线失真。"},
      {title:"趋势识别",detail:"研究默认MA20与MA50的相对位置，并辨识趋势延续与趋势变化；这些是待验证研究规则。"},
      {title:"信号确认",detail:"对拟定的回踩、反弹或均线突破条件进行单独测试，不将Net-Net资产折价条件混入。"},
      {title:"可成交性约束",detail:"纳入当时的停牌、涨跌停、成交限制、T+1及交易成本，再评价信号。"},
      {title:"验证和发布",detail:"先做样本内及样本外回测，再决定是否启动真实的每日策略扫描。"},
    ],
    limits:["当前状态为工程测试，页面不展示独立的真实候选股票。","历史复权数据、交易限制和历史ST可用性需要通过正式验收。"],
  },
  oversold_reversion_v0:{
    tag:"MEAN REVERSION / PLANNED",
    title:"短期超跌后的反转确认",
    subtitle:"研究5–20个交易日的超跌反弹，不把超跌本身视为买入信号。",
    steps:[
      {title:"基础股票池",detail:"拟选择沪深主板并使用历史可交易股票池；需正确识别当时ST、停牌及退市风险。"},
      {title:"识别超跌",detail:"默认研究10个交易日观察窗口，评估区间跌幅、波动和成交量变化；具体阈值待历史验证。"},
      {title:"反弹确认",detail:"拟结合价格企稳、量价回升等确认条件，避免直接抄底连续跌停或无流动性股票。"},
      {title:"风险与退出",detail:"按5–20交易日持仓场景评估止损、持仓超时与交易摩擦，并加入涨跌停不可成交约束。"},
      {title:"策略检验",detail:"开展按市场环境分层的回测，检查回撤、胜率、盈亏比及换手率。"},
    ],
    limits:["该策略尚未正式实现每日扫描；上述均为拟研究规则。","不使用其它策略的股票、选股数量或观察信号作为结果。"],
  },
  quality_momentum_v0:{
    tag:"QUALITY MOMENTUM / PLANNED",
    title:"盈利质量 × 价格动量 × 估值",
    subtitle:"将基本面质量与趋势因子结合，严格避免提前读取尚未披露的财务数据。",
    steps:[
      {title:"历史时点股票池",detail:"建立包含已退市公司的历史证券列表，并排除当时不满足交易规则的样本。"},
      {title:"质量因子",detail:"拟分析盈利、现金流、资产负债结构和财报一致性；不可用的财务指标应保留缺失状态。"},
      {title:"估值因子",detail:"使用当时有效的市值、PB等估值指标，避免使用未来更正后的版本回填旧信号。"},
      {title:"动量因子",detail:"使用经过适当复权的价格序列验证趋势和波动，不与质量指标的时间戳混淆。"},
      {title:"组合与调仓",detail:"默认20个交易日观察调仓周期，需验证组合收益、风险暴露、换手率和成交成本。"},
    ],
    limits:["当前为计划开发，不能将指标列表当作已经投入生产的量化模型。","需要历史公告时点、报告版本、公司行为和ST状态的完整性验证。"],
  },
};

export default function StrategyMethodology({strategy}:{strategy:StrategyDefinition}){
  const guide=guides[strategy.id];
  if(!guide) return <section className="panel"><h2>筛选方法待登记</h2><p>{strategy.notes}</p></section>;
  return <div className="strategy-view-enter" aria-live="polite">
    <div className="hero-strip method-hero"><div>
      <span className="hero-label">{guide.tag}</span>
      <h2>{guide.title}</h2><p>{guide.subtitle}</p>
    </div><div className="hero-formula"><Scale size={24}/>
      <strong>{strategy.status==="live"?"已运行每日筛选":"研究规则，尚未上线"}</strong>
      <small>策略版本：{strategy.version}</small>
    </div></div>
    <section className="panel">
      <div className="section-kicker">STRATEGY METHODOLOGY / {strategy.id}</div>
      <h2>{strategy.name} · 筛选步骤</h2>
      <div className="method-steps">{guide.steps.map((step,i)=>
        <div className="method-step" key={step.title}>
          <span>{String(i+1).padStart(2,"0")}</span>
          <div><strong>{step.title}</strong><p>{step.detail}</p></div>
        </div>)}</div>
    </section>
    <div className="bottom-grid">
      <section className="panel">
        <div className="section-kicker">DATA INTEGRITY</div><h2>数据与验证原则</h2>
        <div className="quality-item"><Database size={18}/><div>
          <strong>独立数据范围</strong><span>{strategy.universe}</span></div></div>
        <div className="quality-item"><Clock3 size={18}/><div>
          <strong>历史时点</strong><span>仅使用决策时点已经可得的数据；披露时间和版本不能提前。</span></div></div>
        <div className="quality-item"><ShieldCheck size={18}/><div>
          <strong>避免结果混用</strong><span>不同策略的条件、候选股票及信号必须独立。</span></div></div>
      </section>
      <section className="panel">
        <div className="section-kicker">LIMITATIONS</div><h2>风险与限制</h2>
        {guide.limits.map(text=><div className="quality-item" key={text}>
          <AlertTriangle size={18}/><div><span>{text}</span></div></div>)}
        <div className="quality-item"><Activity size={18}/><div>
          <strong>研究信号不构成交易建议</strong><span>正式上线前须完成数据完整性和执行约束核验。</span>
        </div></div>
      </section>
    </div>
  </div>;
}
