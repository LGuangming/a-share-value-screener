import BacktestStrategyPanel from "@/components/backtest-strategy-panel";
import ResearchTabs from "@/components/research-tabs";
import { Beaker } from "lucide-react";
import { findStrategy, strategies } from "@/lib/strategies";

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
      <BacktestStrategyPanel initialId={active.id}/>
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
      </section>
    </div>
  </main>;
}
