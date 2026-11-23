import "server-only";
import postgres from "postgres";

let client: ReturnType<typeof postgres> | null = null;

function connection() {
  const url = process.env.DATABASE_READ_URL;
  if (!url) return null;
  if (!client) client = postgres(url, { max: 2, idle_timeout: 15, connect_timeout: 8, prepare: false });
  return client;
}

export type Snapshot = {
  trade_date: string;
  finished_at: string;
  metrics: {
    listed_stocks?: number;
    quoted_stocks?: number;
    pre_screened?: number;
    candidate_count?: number;
    financial_data_gap?: number;
    financial_successes?: number;
    spot_universe?: number;
    sample_available?: number;
    balancesheets_usable?: number;
    strict_book_candidates_unverified?: number;
    sample_codes?: string[];
  };
};

export type ScreenerRow = {
  ts_code: string;
  name: string;
  industry: string | null;
  market: string | null;
  trade_date: string;
  price: number | null;
  pb: number | null;
  market_cap_yuan: number | null;
  ncav_yuan: number | null;
  ncav_coverage: number | null;
  stress_ncav_yuan: number | null;
  stress_coverage: number | null;
  liability_ratio: number | null;
  report_date: string | null;
  disclosed_at: string | null;
  operating_cf_yuan: number | null;
  net_profit_yuan: number | null;
  is_candidate: boolean;
  status: string;
  signal: string;
  flags: string[];
  assessment: string;
};

function dateString(value: unknown): string | null {
  if (!value) return null;
  if (value instanceof Date) return value.toISOString().slice(0, 10);
  return String(value).slice(0, 10);
}
function numeric(value: unknown): number | null {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function normalizeRow(raw: Record<string, unknown>): ScreenerRow {
  return {
    ts_code: String(raw.ts_code), name: String(raw.name ?? raw.ts_code),
    industry: raw.industry ? String(raw.industry) : null,
    market: raw.market ? String(raw.market) : null,
    trade_date: dateString(raw.trade_date) ?? "",
    price: numeric(raw.price), pb: numeric(raw.pb),
    market_cap_yuan: numeric(raw.market_cap_yuan), ncav_yuan: numeric(raw.ncav_yuan),
    ncav_coverage: numeric(raw.ncav_coverage), stress_ncav_yuan: numeric(raw.stress_ncav_yuan),
    stress_coverage: numeric(raw.stress_coverage), liability_ratio: numeric(raw.liability_ratio),
    report_date: dateString(raw.report_date), disclosed_at: dateString(raw.disclosed_at),
    operating_cf_yuan: numeric(raw.operating_cf_yuan), net_profit_yuan: numeric(raw.net_profit_yuan),
    is_candidate: raw.is_candidate === true, status: String(raw.status ?? "DATA_GAP"),
    signal: String(raw.signal ?? "NOT_EVALUATED"),
    flags: Array.isArray(raw.flags) ? raw.flags.map(String) : [],
    assessment: String(raw.assessment ?? "数据尚待核验"),
  };
}

export async function latestSnapshot(jobType: "daily_scan" | "akshare_test" = "daily_scan"): Promise<{ snapshot: Snapshot | null; error: string | null }> {
  const db = connection();
  if (!db) return { snapshot: null, error: "尚未配置数据库连接" };
  try {
    const rows = await db`
      SELECT trade_date, finished_at, metrics
      FROM job_runs WHERE job_type=${jobType} AND status='success'
      ORDER BY finished_at DESC LIMIT 1
    `;
    if (!rows.length) return { snapshot: null, error: "数据库已连接，但尚未发布成功的筛选结果" };
    const r = rows[0];
    return { snapshot: { trade_date: dateString(r.trade_date) ?? "",
      finished_at: (r.finished_at instanceof Date ? r.finished_at.toISOString() : String(r.finished_at)),
      metrics: r.metrics ?? {} }, error: null };
  } catch (error) {
    console.error("Read snapshot failed", error);
    return { snapshot: null, error: "数据库查询失败，请检查只读账号及建表状态" };
  }
}

export async function screeningRows(day: string, strategy: "netnet_v1" | "akshare_test_v1" = "netnet_v1"): Promise<ScreenerRow[]> {
  const db = connection();
  if (!db || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return [];
  try {
    const rows = await db`
      SELECT r.*,s.name,s.industry,s.market
      FROM screen_results r JOIN stocks s USING(ts_code)
      JOIN job_runs j ON j.run_id=r.run_id AND j.status='success'
      WHERE r.trade_date=${day} AND r.strategy=${strategy}
      ORDER BY r.is_candidate DESC, r.ncav_coverage DESC NULLS LAST
      LIMIT 3000
    `;
    return rows.map((row) => normalizeRow(row as Record<string, unknown>));
  } catch (error) {
    console.error("Read screening rows failed", error);
    return [];
  }
}

export async function stockDetail(code: string): Promise<{ stock: ScreenerRow | null; prices: { day: string; close: number }[]; recent: ScreenerRow[] }> {
  const db = connection();
  if (!db || !/^\d{6}\.(SH|SZ|BJ)$/.test(code)) return { stock: null, prices: [], recent: [] };
  try {
    const rows = await db`
      SELECT r.*,s.name,s.industry,s.market FROM screen_results r
      JOIN stocks s USING(ts_code)
      JOIN job_runs j ON j.run_id=r.run_id AND j.status='success'
      WHERE r.ts_code=${code}
      ORDER BY r.trade_date DESC LIMIT 30
    `;
    const prices = await db`
      SELECT trade_date,close FROM (
        SELECT trade_date,close FROM daily_prices WHERE ts_code=${code}
        ORDER BY trade_date DESC LIMIT 100
      ) p ORDER BY trade_date ASC
    `;
    const recent = rows.map((row) => normalizeRow(row as Record<string, unknown>));
    return { stock: recent[0] ?? null,
      prices: prices.map((row) => ({ day: dateString(row.trade_date) ?? "", close: Number(row.close) })), recent };
  } catch (error) {
    console.error("Read stock failed", error);
    return { stock: null, prices: [], recent: [] };
  }
}
