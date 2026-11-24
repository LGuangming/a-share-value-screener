export function moneyCny(value: number | null, decimals = 2): string {
  if (value === null || !Number.isFinite(value)) return "—";
  if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(decimals)}亿`;
  if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(decimals)}万`;
  return value.toFixed(decimals);
}
export function pct(value: number | null, digits = 0): string {
  return value === null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(digits)}%`;
}
export function fixed(value: number | null, digits = 2): string {
  return value === null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}
export function stateLabel(status: string): string {
  const map: Record<string, string> = {
    NET_NET: "符合Net-Net", NEAR_NET_NET: "接近Net-Net", BELOW_NET_NET: "未达门槛",
    DATA_GAP: "财报待补齐", PRICE_EXCLUDED: "价格排除", RISK_EXCLUDED: "风险排除",
    PB_EXCLUDED: "PB排除", STALE_FINANCIALS: "财报过旧", FINANCIAL_IGNORED: "金融股忽略",
  };
  return map[status] ?? status;
}
export function signalLabel(signal: string): string {
  const map: Record<string, string> = {
    BUY_WATCH: "入场观察", WAIT: "继续等待", FUNDAMENTAL_REVIEW: "基本面复核",
    RISK_WATCH: "退出观察", INSUFFICIENT_HISTORY: "历史行情不足",
    INVALID_HISTORY: "行情异常", NOT_ELIGIBLE: "不适用", NOT_EVALUATED: "未评估",
  };
  return map[signal] ?? signal;
}
export function flagLabel(flag: string): string {
  const map: Record<string, string> = {
    STRESS_COVERAGE_BELOW_100: "压力价值不足", FINANCIAL_PERIOD_MISMATCH: "财报期间不一致", ST_RISK: "ST风险", INVALID_MARKET_DATA: "行情异常", MISSING_MATERIAL_BALANCE_ITEM: "资产负债表缺项",
    BALANCE_INCONSISTENCY: "报表勾稽异常", HIGH_LEVERAGE: "杠杆偏高",
    STRESS_INPUT_INCOMPLETE: "压力测试数据缺失", NEGATIVE_STRESS_INPUT: "压力测试数据异常",
    RECEIVABLES_CONCENTRATED: "应收占比较高", INVENTORY_CONCENTRATED: "存货占比较高",
    CASHFLOW_NOT_VERIFIED: "现金流待核验", NEGATIVE_OPERATING_CASHFLOW: "经营现金流为负",
    LOSS_MAKING: "当前亏损", LEVERAGE_NOT_VERIFIED: "杠杆待核验", STALE_FINANCIALS: "旧财报",
  };
  return map[flag] ?? flag;
}
