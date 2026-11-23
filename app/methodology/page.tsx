import { Activity, Database, ShieldCheck, Scale, Clock3 } from "lucide-react";
export const metadata = { title: "筛选方法" };
const items = [
  { n:"01", title:"低价 / 市净率预筛", detail:"价格≤10元，PB≤1.2或PB数据缺失。PB≤1.2仅是宽松预筛，不是最终买入标准。暂排除ST及创业板、科创板、北交所。" },
  { n:"02", title:"严格 Net-Net", detail:"NCAV = 合并报表流动资产合计 − 合并报表全部负债合计。要求 NCAV ÷ 总市值 ≥ 1.25；注意不是仅扣除流动负债。" },
  { n:"03", title:"保守资产压力测试", detail:"假定应收账款折价20%、存货折价30%、预付款折价50%，另外扣除少数股东权益；报表缺项则不计算压力价值。" },
  { n:"04", title:"基本面排雷", detail:"对经营现金流为负、持续亏损、高杠杆、应收/存货占比过高、ST和陈旧报表打标。第一版未解析所有审计附注及诉讼公告。" },
  { n:"05", title:"辅助交易观察", detail:"仅当有至少50个完整交易日行情，才根据MA20、MA50趋势生成观察信号。现金流为负或压力数据缺失时优先转为基本面复核。" },
];
export default function MethodologyPage() {
  return <main className="page-container"><div className="page-top"><div><div className="eyebrow">METHODOLOGY / ACCOUNTING-FIRST INVESTING</div>
    <h1>筛选方法与数据原则</h1><p className="page-lead">明确区分财务事实、估值假设、未核实风险与交易观察。</p></div></div>
    <div className="hero-strip method-hero"><div><span className="hero-label">NET CURRENT ASSET VALUE</span><h2>NCAV = 流动资产 − 全部负债</h2><p>用于识别账面净流动资产高于股权市场价值的公司，而不是承诺清算可得金额。</p></div><div className="hero-formula"><Scale size={26}/><strong>覆盖率 ≥ 125%</strong><small>账面折价筛选阈值</small></div></div>
    <section className="panel"><div className="section-kicker">SCREENING PIPELINE</div><h2>第一版执行步骤</h2>
      <div className="method-steps">{items.map(i=><div className="method-step" key={i.n}><span>{i.n}</span><div><strong>{i.title}</strong><p>{i.detail}</p></div></div>)}</div>
    </section>
    <div className="bottom-grid"><section className="panel"><div className="section-kicker">DATA INTEGRITY</div><h2>日期与单位</h2>
      <div className="quality-item"><Database size={18}/><div><strong>统一人民币元</strong><span>Tushare总市值以万元披露，计算时乘以10,000；财务报表项目为元。</span></div></div>
      <div className="quality-item"><Clock3 size={18}/><div><strong>按真实披露日期取数</strong><span>仅使用该交易日前已披露的报表；由于缺少日内披露时间，保守地将公告当日数据延后至次日使用。</span></div></div>
      <div className="quality-item"><ShieldCheck size={18}/><div><strong>缺失数据不补零</strong><span>若缺少主要资产负债表项目，不给出假定合格的结论。</span></div></div>
    </section><section className="panel"><div className="section-kicker">LIMITATIONS</div><h2>重要限制</h2>
      <div className="quality-item"><Activity size={18}/><div><strong>当前不是全市场完备财报扫描</strong><span>财报按预算逐日补齐；站内展示扫描样本总量和缺口。</span></div></div>
      <div className="quality-item"><Scale size={18}/><div><strong>压力价值不是清算报价</strong><span>资金受限、抵质押、关联方占用、应收账龄等仍需专项核验。</span></div></div>
      <div className="quality-item"><ShieldCheck size={18}/><div><strong>研究信号不是买卖指令</strong><span>回测与实盘执行风险验证尚未完成。</span></div></div>
    </section></div>
  </main>;
}
