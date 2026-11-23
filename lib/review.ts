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
  ST_RISK: rule("ST风险标记", "股票存在ST风险标记。", "HIGH", "经营风险"),
  BALANCE_INCONSISTENCY: rule("资产负债表勾稽异常", "资产总额与流动资产的勾稽关系异常，待核实。", "HIGH", "资料缺失"),
  MISSING_MATERIAL_BALANCE_ITEM: rule("关键资产负债表项目缺失", "流动资产或总负债项目缺失。", "HIGH", "资料缺失"),
  STRESS_COVERAGE_BELOW_100: rule("压力NCAV覆盖不足100%", "压力NCAV覆盖率低于100%。", "HIGH", "资产质量"),
  HIGH_LEVERAGE: rule("负债率超过筛选阈值", "资产负债率超过筛选阈值，需核查偿债结构。", "HIGH", "资产质量"),
  NEGATIVE_STRESS_INPUT: rule("压力测试基础数据异常", "压力价值测算所需项目存在异常负值。", "HIGH", "资料缺失"),
  NEGATIVE_OPERATING_CASHFLOW: rule("经营现金流为负", "最近报告期经营活动现金流量净额为负，需复核营运资金变动。", "MEDIUM", "经营风险"),
  LOSS_MAKING: rule("报告期净利润为负", "最近报告期净利润为负。", "MEDIUM", "经营风险"),
  RECEIVABLES_CONCENTRATED: rule("应收账款占比较高", "应收账款占流动资产比例偏高，需核对账龄及回款。", "MEDIUM", "资产质量"),
  INVENTORY_CONCENTRATED: rule("存货占比较高", "存货占流动资产比例偏高，需核对周转及跌价准备。", "MEDIUM", "资产质量"),
  STRESS_INPUT_INCOMPLETE: rule("压力测试数据缺失", "压力价值测算所需财务项目缺失。", "MEDIUM", "资料缺失"),
  FINANCIAL_PERIOD_MISMATCH: rule("三张财报报告期不一致", "利润表或现金流量表与资产负债表的报告期不一致。", "MEDIUM", "资料缺失"),
  LEVERAGE_NOT_VERIFIED: rule("负债率待核验", "资产总额缺失，负债率待核验。", "MEDIUM", "资料缺失"),
  STALE_FINANCIALS: rule("财报期间过旧", "最近可用资产负债表报告期距筛选日较久。", "MEDIUM", "资料缺失"),
  INVALID_MARKET_DATA: rule("行情数据异常", "收盘价或总市值数据异常。", "MEDIUM", "资料缺失"),
  PROFIT_NOT_VERIFIED: rule("净利润数据待核验", "净利润数据缺失。", "MEDIUM", "资料缺失"),
  CASHFLOW_REPORT_MISSING: rule(
    "现金流量表缺失",
    "截至筛选日未取得可用现金流量表。",
    "MEDIUM", "资料缺失"
  ),
  CASHFLOW_FIELD_MISSING: rule(
    "经营现金流关键字段缺失",
    "现金流量表中经营活动现金流量净额字段缺失。",
    "MEDIUM", "资料缺失"
  ),
  CASHFLOW_NOT_YET_DISCLOSED: rule(
    "报表尚未披露（截至筛选日）",
    "现金流量表披露日晚于本次筛选日。",
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
      explanation: "其他风险标记，待核验原始记录。",
      severity: "MEDIUM" as const, focus: "资料缺失" as const,
    };
  });
  const bothLossAndCashBurn = flags.has("NEGATIVE_OPERATING_CASHFLOW")
    && flags.has("LOSS_MAKING");
  if (bothLossAndCashBurn) {
    const reason = reasons.find(x => x.code === "NEGATIVE_OPERATING_CASHFLOW");
    if (reason) {
      reason.severity = "HIGH";
      reason.explanation += " 同期净利润为负。";
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
