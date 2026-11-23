import type { ScreenerRow } from "./data";

/**
 * Deterministic screening flags, NOT an analyst sign-off.
 * The data sources contain reporting-period values, not audited recoverability.
 */
export type RiskSeverity = "HIGH" | "MEDIUM" | "LOW";
export type ReviewPriority = "P0" | "P1" | "P2";
export type ReviewFocus = "经营风险" | "资产质量" | "资料缺失";
export type ReviewConclusion = "通过" | "待补充资料" | "不建议进入买入观察" | "不适用";
export type CashflowDetail =
  | "VERIFIED" | "REPORT_MISSING" | "FIELD_MISSING" | "NOT_YET_DISCLOSED";

export type ReviewReason = {
  code: string;
  label: string;
  explanation: string;
  severity: RiskSeverity;
  focus: ReviewFocus;
};

export type FundamentalReview = {
  severity: RiskSeverity;
  priority: ReviewPriority;
  focus: ReviewFocus | "无重点异常";
  conclusion: ReviewConclusion;
  reasons: ReviewReason[];
};

const rule = (
  label: string, explanation: string,
  severity: RiskSeverity, focus: ReviewFocus
): Omit<ReviewReason, "code"> => ({label, explanation, severity, focus});

const RULES: Record<string, Omit<ReviewReason, "code">> = {
  ST_RISK: rule("ST风险标记", "股票存在ST类风险，不应仅凭账面折价进入交易观察。", "HIGH", "经营风险"),
  BALANCE_INCONSISTENCY: rule("资产负债表勾稽异常", "资产与流动资产数据存在不合理关系，应先核对报表口径和原始资料。", "HIGH", "资料缺失"),
  MISSING_MATERIAL_BALANCE_ITEM: rule("关键资产负债表项目缺失", "流动资产或总负债无法有效确定，需先补齐数据。", "HIGH", "资料缺失"),
  STRESS_COVERAGE_BELOW_100: rule("压力NCAV覆盖不足100%", "统一折价假设下，净流动资产不足以覆盖市值，安全边际较弱。", "HIGH", "资产质量"),
  HIGH_LEVERAGE: rule("负债率超过筛选阈值", "总负债相对资产规模偏高，应进一步核实偿债压力、担保及到期结构。", "HIGH", "资产质量"),
  NEGATIVE_STRESS_INPUT: rule("压力测试基础数据异常", "用于压力NCAV测算的部分项目为异常负值，需核实报表项目口径。", "HIGH", "资料缺失"),
  NEGATIVE_OPERATING_CASHFLOW: rule("经营现金流为负", "最近可用报告期经营活动现金净流出；需比较利润、营运资金及历史期间。", "MEDIUM", "经营风险"),
  LOSS_MAKING: rule("报告期净利润为负", "最近可用报告期为亏损；需评估持续经营和资产消耗风险。", "MEDIUM", "经营风险"),
  RECEIVABLES_CONCENTRATED: rule("应收账款占比较高", "应收账款占流动资产比例触及系统阈值，需核对账龄和回款质量。", "MEDIUM", "资产质量"),
  INVENTORY_CONCENTRATED: rule("存货占比较高", "存货占流动资产比例触及系统阈值，需核对周转、跌价和可变现净值。", "MEDIUM", "资产质量"),
  STRESS_INPUT_INCOMPLETE: rule("压力测试数据缺失", "应收、存货、预付或少数股东权益数据不足，压力NCAV尚不能可靠计算。", "MEDIUM", "资料缺失"),
  FINANCIAL_PERIOD_MISMATCH: rule("三张财报报告期不一致", "利润表或现金流量表与资产负债表期间不一致，应核实对应披露和取数口径。", "MEDIUM", "资料缺失"),
  LEVERAGE_NOT_VERIFIED: rule("负债率待核验", "缺少计算资产负债率所需的有效资产总额，应核对报告。", "MEDIUM", "资料缺失"),
  STALE_FINANCIALS: rule("财报期间过旧", "资产负债表期间距筛选交易日过久，不能直接作为最新净流动资产判断。", "MEDIUM", "资料缺失"),
  INVALID_MARKET_DATA: rule("行情数据异常", "收盘价或市值无效，需核验数据源及交易日。", "MEDIUM", "资料缺失"),
  PROFIT_NOT_VERIFIED: rule("净利润数据待核验", "当前筛选记录未取得有效的净利润数值，不能判定盈利能力。", "MEDIUM", "资料缺失"),
  CASHFLOW_REPORT_MISSING: rule(
    "现金流量表缺失",
    "Neon数据库没有该交易日之前可用的现金流量表；无法据此判断企业是否已经披露，更不能把缺失当作零。",
    "MEDIUM", "资料缺失"
  ),
  CASHFLOW_FIELD_MISSING: rule(
    "经营现金流关键字段缺失",
    "已取得该交易日可用的现金流量表，但经营活动现金流量净额 n_cashflow_act 为空或无效。",
    "MEDIUM", "资料缺失"
  ),
  CASHFLOW_NOT_YET_DISCLOSED: rule(
    "报表尚未披露（截至筛选日）",
    "数据库已有后续披露记录，但披露日晚于或等于本次使用的收盘交易日；禁止将该报表提前用于历史筛选。",
    "MEDIUM", "资料缺失"
  ),
};

const CASHFLOW_CODE: Record<Exclude<CashflowDetail,"VERIFIED">,string> = {
  REPORT_MISSING: "CASHFLOW_REPORT_MISSING",
  FIELD_MISSING: "CASHFLOW_FIELD_MISSING",
  NOT_YET_DISCLOSED: "CASHFLOW_NOT_YET_DISCLOSED",
};

function score(severity: RiskSeverity): number {
  return severity === "HIGH" ? 3 : severity === "MEDIUM" ? 2 : 1;
}

const focusOrder: Record<ReviewFocus, number> = {
  "经营风险": 0, "资产质量": 1, "资料缺失": 2,
};

export function evaluateFundamentalReview(
  row: Pick<ScreenerRow,
    "flags" | "cashflow_detail" | "operating_cf_yuan" |
    "net_profit_yuan" | "is_candidate" | "status">
): FundamentalReview {
  const flags = new Set(row.flags);
  // The old generic flag is preserved in past publications but is replaced
  // by a source-backed, as-of cashflow reason in the UI.
  flags.delete("CASHFLOW_NOT_VERIFIED");
  flags.delete("CASHFLOW_REPORT_MISSING");
  flags.delete("CASHFLOW_FIELD_MISSING");
  flags.delete("CASHFLOW_NOT_YET_DISCLOSED");

  if (row.operating_cf_yuan === null) {
    const detail = row.cashflow_detail === "VERIFIED"
      ? "FIELD_MISSING" : row.cashflow_detail;
    flags.add(CASHFLOW_CODE[detail] ?? CASHFLOW_CODE.REPORT_MISSING);
  }
  if (row.net_profit_yuan === null) flags.add("PROFIT_NOT_VERIFIED");

  // Never interpret an unfamiliar rule as a clean pass.
  const reasons: ReviewReason[] = [...flags].map(code => {
    const hit = RULES[code];
    return hit ? {code, ...hit} : {
      code, label: `其他待核验标记：${code}`,
      explanation: "当前复核规则没有该标记的专项解释，需核对底层数据与计算程序。",
      severity: "MEDIUM" as const, focus: "资料缺失" as const,
    };
  });
  const bothLossAndCashBurn = flags.has("NEGATIVE_OPERATING_CASHFLOW")
    && flags.has("LOSS_MAKING");
  if (bothLossAndCashBurn) {
    const reason = reasons.find(x => x.code === "NEGATIVE_OPERATING_CASHFLOW");
    if (reason) {
      reason.severity = "HIGH";
      reason.explanation += " 同期同时亏损，经营风险等级上调。";
    }
  }
  reasons.sort((a, b) =>
    score(b.severity) - score(a.severity) ||
    focusOrder[a.focus] - focusOrder[b.focus] ||
    a.label.localeCompare(b.label, "zh-CN")
  );

  const severity: RiskSeverity = reasons.some(r => r.severity === "HIGH")
    ? "HIGH" : reasons.length ? "MEDIUM" : "LOW";
  const priority: ReviewPriority = severity === "HIGH"
    ? "P0" : severity === "MEDIUM" ? "P1" : "P2";
  const focus = reasons[0]?.focus ?? "无重点异常";
  const conclusion: ReviewConclusion = !row.is_candidate
    ? "不适用"
    : severity === "HIGH" ? "不建议进入买入观察"
    : reasons.length ? "待补充资料" : "通过";

  return {severity, priority, focus, conclusion, reasons};
}

export const severityLabel: Record<RiskSeverity,string> = {
  HIGH: "高", MEDIUM: "中", LOW: "低",
};
export const priorityLabel: Record<ReviewPriority,string> = {
  P0: "P0 · 优先处理", P1: "P1 · 尽快核验", P2: "P2 · 常规跟踪",
};
