import StrategySwitch from "@/components/strategy-switch";

export const metadata = {title:"筛选方法"};

type Query = {strategy?: string};
export default async function MethodologyPage({searchParams}:{searchParams:Promise<Query>}){
  const query = await searchParams;
  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">METHODOLOGY / MULTI-STRATEGY RESEARCH</div>
      <h1>筛选方法与数据原则</h1>
      <p className="page-lead">选择不同策略，查看独立的研究步骤、数据条件、适用范围与验证限制。</p>
    </div></div>
    <StrategySwitch mode="methodology" requestedId={query.strategy}/>
  </main>;
}
