import StrategySwitch from "@/components/strategy-switch";

export const metadata = {title:"筛选方法"};

type Query = {strategy?: string};
export default async function MethodologyPage({searchParams}:{searchParams:Promise<Query>}){
  const query = await searchParams;
  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">METHODOLOGY / MULTI-STRATEGY RESEARCH</div>
      <h1>筛选方法</h1>
      <p className="page-lead">查看策略条件、指标口径与筛选步骤。</p>
    </div></div>
    <StrategySwitch mode="methodology" requestedId={query.strategy}/>
  </main>;
}
