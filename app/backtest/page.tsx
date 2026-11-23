import { Construction, CheckCircle2 } from "lucide-react";
export const metadata = { title: "策略回测" };
export default function BacktestPage() {
  return <main className="page-container"><div className="page-top"><div><div className="eyebrow">RESEARCH VALIDATION</div>
    <h1>策略回测 <span className="heading-period">/ 待开发</span></h1><p className="page-lead">先保证财务数据的披露日期正确，再开展有意义的回测。</p></div></div>
    <section className="panel placeholder-section"><div className="placeholder-icon"><Construction size={32}/></div><h2>回测模块尚未启用</h2>
      <p>第一版优先完成实时数据采集、Net-Net筛选和历史信号存储。目前没有宣称回测收益、最大回撤或胜率。</p>
      <div className="checklist"><div><CheckCircle2 size={17}/> 未来只能读取当时已经披露的财报</div>
      <div><CheckCircle2 size={17}/> 历史股票池必须包括退市股票和ST状态变化</div>
      <div><CheckCircle2 size={17}/> 计入交易成本、涨跌停、停牌、T+1及滑点</div>
      <div><CheckCircle2 size={17}/> 验证持有期与信号有效性后再提供建议</div></div>
    </section>
  </main>;
}
