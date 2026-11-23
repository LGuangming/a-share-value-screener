import Link from "next/link";
import { AlertTriangle, Database, ExternalLink, FlaskConical, Info, ShieldAlert } from "lucide-react";
import { latestSnapshot, screeningRows } from "@/lib/data";
import { fixed, moneyCny, pct } from "@/lib/format";
import { Empty } from "@/components/empty";

export const dynamic = "force-dynamic";

const workflowUrl = "https://github.com/LGuangming/a-share-value-screener/actions/workflows/akshare-test.yml";

export default async function AKShareExperimentalPage() {
  const { snapshot, error } = await latestSnapshot("akshare_test");
  const rows = snapshot ? await screeningRows(snapshot.trade_date, "akshare_test_v1") : [];
  const covered = rows.filter(r => r.ncav_coverage !== null);
  return <main className="page-container">
    <div className="page-top"><div>
      <div className="eyebrow">EXPERIMENT / DATA PROVIDER SANDBOX</div>
      <h1>AKShare 数据源测试</h1>
      <p className="page-lead">与正式 Tushare Pro 扫描完全分离，核对东方财富行情及三张财务报表的字段和计算口径。</p>
    </div></div>

    <div className="panel notice-panel">
      <div className="quality-item"><ShieldAlert size={19}/><div>
        <strong>实验数据，不作为买卖依据</strong>
        <span>AKShare实时接口不保证报价所属交易日可独立核实；必须与原始上市公司公告复核合并口径和披露日期。实验模式不生成交易信号，不覆盖正式 Tushare 排名。</span>
      </div></div>
    </div>

    <div className="stats-grid">
      <div className="stat-card"><span><Database size={16}/>实验快照</span><strong>{snapshot?.trade_date ?? "—"}</strong><small>仅手动发布，非自动生产任务</small></div>
      <div className="stat-card"><span><FlaskConical size={16}/>测试股票</span><strong>{snapshot ? (snapshot.metrics.sample_available ?? rows.length) : "—"}</strong><small>单次限制1–8只股票</small></div>
      <div className="stat-card"><span><Info size={16}/>有效资产负债表</span><strong>{snapshot ? (snapshot.metrics.balancesheets_usable ?? "—") : "—"}</strong><small>有可核实的历史公告日期</small></div>
      <div className="stat-card"><span><AlertTriangle size={16}/>财报缺口</span><strong>{snapshot ? (snapshot.metrics.financial_data_gap ?? "—") : "—"}</strong><small>缺失不自动记为零</small></div>
    </div>
    {!snapshot && <div className="panel notice-panel">
      <Empty title="尚未发布AKShare实验结果" detail={error ?? "GitHub手动运行 AKShare Experimental Test，并选择 publish 模式。"} type="warning"/>
    </div>}

    <section className="panel market-panel">
      <div className="panel-heading"><div><div className="section-kicker">DATA PROVIDER CHECK</div><h2>测试样本数据</h2>
        <p>NCAV = 流动资产 − 全部负债；市场总市值必须使用人民币元计算。缺失的折价输入会标记为空。</p></div>
        <a className="inline-link" href={workflowUrl} target="_blank" rel="noreferrer">GitHub手动测试 <ExternalLink size={15}/></a>
      </div>
      {rows.length > 0 ? <div className="table-scroll"><table className="results-table"><thead><tr>
        <th>股票</th><th>测试价格</th><th>总市值</th><th>NCAV覆盖率</th><th>压力测试覆盖率</th>
        <th>报告期 / 披露日期</th><th>状态</th>
      </tr></thead><tbody>{rows.map(r => <tr key={r.ts_code}>
        <td><div className="stock-name">{r.name}</div><div className="stock-meta">{r.ts_code}</div></td>
        <td className="number">{fixed(r.price)}</td><td className="number">{moneyCny(r.market_cap_yuan)}</td>
        <td className="number">{pct(r.ncav_coverage,1)}</td><td className="number">{pct(r.stress_coverage,1)}</td>
        <td><div>{r.report_date || "未知"}</div><div className="stock-meta">公告 {r.disclosed_at || "未核实"}</div></td>
        <td><span className="pill pill-neutral">{r.is_candidate ? "账面达标（待核验）" : "不符合/数据不足"}</span>
          <div className="stock-meta">{r.flags.join("、") || "待复核"}</div></td>
      </tr>)}</tbody></table></div> : <Empty title="暂时没有可展示的测试股票" detail="请先检查AKShare实验工作流的日志和JSON Artifact。"/>}
      {covered.length > 0 && <div className="panel-foot">
        {covered.length}只股票产生了初步NCAV计算，但实验结果仅供验证数据接口和财务公式，不能推定完整市场排名。
      </div>}
    </section>
    <div className="panel info-panel">
      <div className="section-kicker">RUN MODES</div><h3>如何测试？</h3>
      <p>打开GitHub Actions中的“AKShare Experimental Test”。选择 <strong>smoke</strong>：仅生成JSON测试报告，不写数据库；选择 <strong>publish</strong>：使用GitHub里的 DATABASE_URL 写入隔离的AKShare实验快照，供本页查看。</p>
      <p>正式每日扫描仍由 <Link href="/">Tushare Pro 页面</Link> 展示，直到其接口权限问题解决。AKShare测试不自动合并进正式排名。</p>
    </div>
  </main>;
