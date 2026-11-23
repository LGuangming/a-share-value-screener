import catalogJson from "@/strategies/catalog.json";

export type StrategyStatus = "live" | "research" | "planned";
export type ParameterDefinition = {
  key: string;
  name: string;
  unit: string;
  type: "number" | "integer";
  default: number;
  min: number;
  max: number;
};
export type StrategyDefinition = {
  id: string;
  version: string;
  name: string;
  family: string;
  status: StrategyStatus;
  description: string;
  universe: string;
  requirements: string[];
  parameters: ParameterDefinition[];
  notes: string;
};

export const strategies: StrategyDefinition[] =
  catalogJson as unknown as StrategyDefinition[];

export const strategyStatusLabel: Record<StrategyStatus,string> = {
  live: "每日筛选运行中",
  research: "工程测试",
  planned: "计划开发",
};

export function findStrategy(id: string): StrategyDefinition | undefined {
  return strategies.find(x => x.id === id);
}

export const requirementLabels: Record<string,string> = {
  daily_basic: "最新估值及收盘价",
  balancesheet: "合并资产负债表",
  income: "合并利润表",
  cashflow: "现金流量表",
  price_history: "近期历史收盘价",
  daily_ohlcv: "历史开高低收、成交量",
  adj_factor: "历史复权因子",
  stk_limit: "历史涨跌停价格",
  suspend_d: "历史停复牌记录",
  historic_st: "历史ST状态（目前权限不足）",
  historic_financials: "历史时点三大财报（仍需补齐）",
  corporate_actions: "历史分红及其他公司行为",
};
