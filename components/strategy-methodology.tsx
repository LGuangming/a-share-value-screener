"use client";

import { Scale } from "lucide-react";
import type { StrategyDefinition } from "@/lib/strategies";

type Step={title:string;detail:string};
type Guide={tag:string;title:string;subtitle:string;steps:Step[]};

const guides:Record<string,Guide>={
  netnet_v1:{
    tag:"NET CURRENT ASSET VALUE",
    title:"NCAV = 流动资产 − 全部负债",
    subtitle:"基于合并资产负债表计算资产折价。",
    steps:[
      {title:"全量预计算及即时筛选",detail:"沪深主板全量估值；默认股价≤10元、PB≤1.2或PB缺失；股价及PB可自由调整或取消限制；剔除当前ST股票。"},
      {title:"净流动资产筛选",detail:"NCAV = 合并流动资产 − 合并全部负债；NCAV/总市值≥125%。银行、证券、保险及多元金融暂不纳入。"},
      {title:"保守资产压力测试",detail:"应收账款、存货及预付款分别折价20%、30%、50%，另考虑少数股东权益。"},
      {title:"经营及资产质量风险",detail:"独立标识经营现金流为负、持续亏损、高负债、应收或存货占比过高、陈旧财报及风险警示。"},
      {title:"技术面辅助观察",detail:"需有至少50个完整交易日收盘历史，再观察MA20、MA50；基本面风险优先于趋势信号。"},
    ],
  },
  ma_trend_research_v0:{
    tag:"PRICE TREND / ENGINEERING RESEARCH",
    title:"短期均线与长期均线趋势",
    subtitle:"MA20/MA50价格趋势研究。",
    steps:[
      {title:"样本与价格序列",detail:"研究沪深主板历史日线行情及复权价格。"},
      {title:"趋势识别",detail:"比较MA20与MA50的相对位置及趋势变化。"},
      {title:"信号确认",detail:"观察回踩、反弹和均线突破条件。"},
      {title:"可成交性约束",detail:"交易条件包括停牌、涨跌停、T+1及交易成本。"},
      {title:"验证和发布",detail:"样本内与样本外历史回测。"},
    ],
  },
  oversold_reversion_v0:{
    tag:"MEAN REVERSION / PLANNED",
    title:"短期超跌后的反转确认",
    subtitle:"研究5–20个交易日的超跌反转。",
    steps:[
      {title:"基础股票池",detail:"沪深主板历史可交易股票池，包含历史ST及停牌状态。"},
      {title:"识别超跌",detail:"10个交易日观察窗口；分析跌幅、波动率及成交量。"},
      {title:"反弹确认",detail:"结合价格企稳、量价回升及流动性条件。"},
      {title:"风险与退出",detail:"持仓5–20个交易日；纳入止损、持仓上限、涨跌停与成交成本。"},
      {title:"策略检验",detail:"按市场环境比较收益、回撤、胜率、盈亏比及换手率。"},
    ],
  },
  quality_momentum_v0:{
    tag:"QUALITY MOMENTUM / PLANNED",
    title:"盈利质量 × 价格动量 × 估值",
    subtitle:"组合盈利质量、价格动量和估值因子。",
    steps:[
      {title:"历史时点股票池",detail:"历史沪深主板股票池，包含退市及历史风险警示记录。"},
      {title:"质量因子",detail:"分析盈利、现金流、资产负债结构及财报一致性。"},
      {title:"估值因子",detail:"采用历史时点的市值与PB等估值指标。"},
      {title:"动量因子",detail:"采用复权价格分析趋势、收益和波动。"},
      {title:"组合与调仓",detail:"20个交易日调仓周期；观察收益、风险暴露、换手率及交易成本。"},
    ],
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
  </div>;
}
