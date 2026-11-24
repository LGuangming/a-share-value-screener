import Link from "next/link";
import type { Metadata } from "next";
import { ArrowLeft, CalendarClock, FileCheck2, TrendingUp, AlertTriangle } from "lucide-react";
import { stockDetail, companyOverview } from "@/lib/data";
import { fixed, pct, moneyCny, stateLabel, signalLabel, flagLabel } from "@/lib/format";
import { evaluateFundamentalReview, severityLabel, priorityLabel } from "@/lib/review";
import MarketChart from "@/components/market-chart";
import { Empty } from "@/components/empty";
import ResearchTabs from "@/components/research-tabs";
import FinancialHistory from "@/components/financial-history";
import { strategies, strategyStatusLabel } from "@/lib/strategies";
import { WatchButton } from "@/components/workspace-preferences";
export const dynamic = "force-dynamic";
type Props = { params: Promise<{code: string}> };
export async function generateMetadata({params}: Props): Promise<Metadata> {
  const {code}=await params;return {title: `${code} 个股研究`};
}
function Facts({rows}:{rows:[string,string][]}){return <dl className="detail-list">{rows.map(([label,value])=><div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;}
export default async function StockPage({params}: Props) {
  const {code}=await params;
  if(!/^\d{6}\.(SH|SZ|BJ)$/.test(code))return <main className="page-container"><Empty title="无效股票代码" detail="请输入有效的证券代码。"/></main>;
  const [company,{stock,prices,recent}]=await Promise.all([companyOverview(code),stockDetail(code)]);
  if(!company)return <main className="page-container"><Link className="back-link" href="/research"><ArrowLeft size={16}/> 返回行情研究</Link><div className="panel"><Empty title="未找到该证券" detail="股票档案暂无该证券。"/></div></main>;
  const {identity,overview:o}=company;
  const m=o.market,b=o.balance,i=o.income,c=o.cashflow;
  const review=stock?evaluateFundamentalReview(stock):null;
  const latest=prices.at(-1);
  const price=latest&&(!m||latest.day>=m.day)?latest.close:m?.close??null;
  const priceDay=latest&&(!m||latest.day>=m.day)?latest.day:m?.day??"—";
  const date=(r:typeof b)=>r?`${r.period} · 披露 ${r.disclosed}`:"—";
  const netnet=stock?<><div className="stats-grid">
    <div className="stat-card"><span>筛选状态</span><strong className="status-stat">{stateLabel(stock.status)}</strong><small>{stock.trade_date}</small></div>
    <div className="stat-card"><span>NCAV覆盖率</span><strong>{pct(stock.ncav_coverage,1)}</strong></div>
    <div className="stat-card"><span>压力价值覆盖率</span><strong>{pct(stock.stress_coverage,1)}</strong></div>
    <div className="stat-card"><span>观察信号</span><strong className="status-stat">{signalLabel(stock.signal)}</strong></div>
    </div><section className="panel"><h2>资产折价与压力测试</h2>
        <div className="table-scroll"><table className="results-table financial-table"><thead><tr><th>估值项目</th><th>金额 / 比率</th><th>计算口径</th></tr></thead><tbody>
          <tr><td>总市值</td><td>{moneyCny(stock.market_cap_yuan)}</td><td>收盘日总市值</td></tr>
          <tr><td>账面净流动资产</td><td>{moneyCny(stock.ncav_yuan)}</td><td>NCAV</td></tr>
          <tr><td>NCAV覆盖率</td><td>{pct(stock.ncav_coverage,1)}</td><td>NCAV / 市值</td></tr>
          <tr><td>统一压力测试价值</td><td>{moneyCny(stock.stress_ncav_yuan)}</td><td>调整后净流动资产</td></tr>
          <tr><td>压力价值覆盖率</td><td>{pct(stock.stress_coverage,1)}</td><td>压力价值 / 市值</td></tr>
          <tr><td>账面资产折价率</td><td>{stock.ncav_yuan!==null && stock.ncav_yuan>0 && stock.market_cap_yuan!==null ? pct(1-stock.market_cap_yuan/stock.ncav_yuan,1):"—"}</td><td>1 − 市值 / NCAV</td></tr>
          <tr><td>市净率</td><td>{fixed(stock.pb)}</td><td>PB</td></tr>
        </tbody></table></div>
        <Link className="inline-link" href="/methodology?strategy=netnet_v1">查看资产回收率及筛选口径</Link>
      </section><section className="panel"><div className="section-kicker">OBSERVATION HISTORY</div><h2>最近观察记录</h2>
        <div className="table-scroll"><table className="results-table compact"><thead><tr><th>交易日</th><th>价格</th><th>NCAV覆盖</th><th>压力测试</th><th>交易信号</th></tr></thead><tbody>
          {recent.map(r=><tr key={r.trade_date}><td>{r.trade_date}</td><td>¥{fixed(r.price)}</td><td>{pct(r.ncav_coverage,1)}</td><td>{pct(r.stress_coverage,1)}</td><td>{signalLabel(r.signal)}</td></tr>)}
        </tbody></table></div>
      </section><>{review && <section className="panel review-detail-panel">
        <div className="panel-heading"><div>
          <div className="section-kicker">FUNDAMENTAL RISK REVIEW</div>
          <h2>基本面复核摘要</h2>
          <p>财务指标与风险事项汇总。</p>
        </div></div>
        <div className="review-summary-grid">
          <div><span>风险等级</span><strong className={`review-level review-level-${review.severity.toLowerCase()}`}>{severityLabel[review.severity]}</strong></div>
          <div><span>复核优先级</span><strong>{priorityLabel[review.priority]}</strong></div>
          <div><span>主要复核类别</span><strong>{review.focus}</strong></div>
          <div><span>系统初评结论</span><strong className={`review-conclusion review-conclusion-${review.conclusion === "通过" ? "pass" : review.conclusion === "待补充资料" ? "pending" : "block"}`}>{review.conclusion}</strong></div>
        </div>
        <h3 className="review-subhead">复核原因与建议核查事项</h3>
        {review.reasons.length ? <div className="review-findings">
          {review.reasons.map(reason => <div className="review-finding" key={reason.code}>
            <div className="review-finding-heading"><strong>{reason.label}</strong>
              <span>{reason.focus} · 风险{severityLabel[reason.severity]}</span></div>
            <p>{reason.explanation}</p>
          </div>)}
        </div> : <p className="review-muted">暂无重点复核事项。</p>}
      </section>}<div className="detail-grid">
        <section className="panel"><div className="section-kicker">FUNDAMENTAL REVIEW</div><h2>基本面初步评价</h2>
          <p className="assessment">{stock.assessment}</p>
        </section>
        <section className="panel"><div className="section-kicker">RISK CHECKS</div><h2>财务风险和未核验项目</h2>
          {stock.flags.length ? <div className="flag-list">{stock.flags.map((flag,i)=><span key={`${flag}-${i}`} className="risk-flag"><AlertTriangle size={14}/>{flag === "CASHFLOW_NOT_VERIFIED" ? review?.reasons.find(x => x.code.startsWith("CASHFLOW_"))?.label ?? flagLabel(flag) : flagLabel(flag)}</span>)}</div>
          : <div className="no-flags"><FileCheck2 size={20}/> 暂无已标记风险事项。</div>}
          <div className="dates"><div><CalendarClock size={15}/> 财务报告期：{stock.report_date || "未取得"}</div>
            <div><CalendarClock size={15}/> 财报披露日：{stock.disclosed_at || "未取得"}</div></div>
        </section>
      </div></></>:<section className="panel"><Empty title="暂无 Net-Net 策略记录" detail="该证券暂无已发布的策略记录。"/></section>;
  return <main className="page-container">
    <Link className="back-link" href={"/research?code="+encodeURIComponent(code)}><ArrowLeft size={16}/> 返回行情研究</Link>
    <div className="page-top stock-page-top"><div><h1>{identity.name} <span className="heading-period">{code}</span></h1><p className="page-lead">{identity.industry||"—"} · {o.marketType||"—"} · 上市 {o.listDate||"—"}</p></div><WatchButton stock={identity}/></div>
    <div className="stats-grid">
      <div className="stat-card"><span>最近收盘价</span><strong>{fixed(price)}</strong><small>{priceDay}</small></div>
      <div className="stat-card"><span>总市值</span><strong>{moneyCny(m?.marketCap??null)}</strong><small>{m?.day??"—"}</small></div>
      <div className="stat-card"><span>市净率 PB</span><strong>{fixed(m?.pb??null)}</strong><small>{m?.day??"—"}</small></div>
      <div className="stat-card"><span>换手率</span><strong>{pct(m?.turnover??null,2)}</strong><small>{m?.day??"—"}</small></div>
    </div>
    <ResearchTabs key={code} label="个股研究" tabs={[
      {id:"overview",label:"公司概览",content:<><section className="panel market-chart-panel"><div className="panel-heading"><h2>行情分析</h2><TrendingUp size={18} className="muted"/></div><MarketChart key={code} code={code} name={identity.name} prices={prices}/></section>
        <div className="detail-grid"><section className="panel"><h2>公司资料</h2><Facts rows={[["股票代码",code],["所属行业",identity.industry??"—"],["市场",o.marketType??"—"],["上市日期",o.listDate??"—"],["ST标识",o.isST?"是":"否"]]}/></section>
        <section className="panel"><h2>核心财务</h2><Facts rows={[["营业收入",moneyCny(i?.revenue??null)],["归母净利润",moneyCny(i?.parentProfit??null)],["经营现金流",moneyCny(c?.operatingCash??null)],["资产负债率",pct(b?.leverage??null,1)],["利润表报告期",i?.period??"—"],["现金流报告期",c?.period??"—"],["资产负债表报告期",b?.period??"—"]]}/></section></div></>},
      {id:"finance",label:"财务分析",content:<><div className="detail-grid">
        <section className="panel"><h2>资产负债表</h2><p className="note">{date(b)}</p><Facts rows={[["总资产",moneyCny(b?.assets??null)],["总负债",moneyCny(b?.liabilities??null)],["所有者权益（含少数股东）",moneyCny(b?.equity??null)],["货币资金",moneyCny(b?.cash??null)],["资产负债率",pct(b?.leverage??null,1)]]}/></section>
        <section className="panel"><h2>利润表（累计）</h2><p className="note">{date(i)}</p><Facts rows={[["营业收入",moneyCny(i?.revenue??null)],["合并净利润",moneyCny(i?.profit??null)],["归母净利润",moneyCny(i?.parentProfit??null)]]}/></section>
        <section className="panel"><h2>现金流量表（累计）</h2><p className="note">{date(c)}</p><Facts rows={[["经营活动现金流量净额",moneyCny(c?.operatingCash??null)]]}/></section></div>
        <FinancialHistory key={code} code={code}/></>},
      {id:"valuation",label:"估值分析",content:<section className="panel"><h2>市场估值</h2><p className="note">行情日期：{m?.day??"—"}</p><Facts rows={[["总市值",moneyCny(m?.marketCap??null)],["市净率 PB",fixed(m?.pb??null)],["市盈率 PE（TTM）",fixed(m?.peTtm??null)],["市销率 PS（TTM）",fixed(m?.psTtm??null)],["股息率（TTM）",pct(m?.dividendYield??null,2)]]}/></section>},
      {id:"strategy",label:"策略分析",content:<ResearchTabs label="策略选择" tabs={strategies.map(s=>({id:s.id,label:s.name,content:s.id==="netnet_v1"?netnet:<section className="panel"><h2>{s.name}</h2><p className="muted">{strategyStatusLabel[s.status]} · 暂无个股策略结果</p><Link className="inline-link" href={"/strategies/"+s.id}>策略详情</Link></section>}))}/>},
      {id:"risk",label:"风险事项",content:<section className="panel"><h2>证券状态</h2><Facts rows={[["ST标识",o.isST?"是":"否"],["所属市场",o.marketType??"—"]]}/></section>},
    ]}/>
  </main>;
}
