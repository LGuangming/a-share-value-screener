import "server-only";
import postgres from "postgres";
import { unstable_cache } from "next/cache";

let client: ReturnType<typeof postgres> | null = null;

function connection() {
  const url = process.env.DATABASE_READ_URL;
  if (!url) return null;
  if (!client) client = postgres(url, { max: 1, idle_timeout: 10, connect_timeout: 15, prepare: false });
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
  cashflow_detail: "VERIFIED" | "REPORT_MISSING" | "FIELD_MISSING" | "NOT_YET_DISCLOSED";
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
    cashflow_detail: raw.cashflow_detail === "FIELD_MISSING" ||
      raw.cashflow_detail === "NOT_YET_DISCLOSED" || raw.cashflow_detail === "VERIFIED"
      ? raw.cashflow_detail : "REPORT_MISSING",
    is_candidate: raw.is_candidate === true, status: String(raw.status ?? "DATA_GAP"),
    signal: String(raw.signal ?? "NOT_EVALUATED"),
    flags: Array.isArray(raw.flags) ? raw.flags.map(String) : [],
    assessment: String(raw.assessment ?? "数据尚待核验"),
  };
}

async function readLatestSnapshot(): Promise<{ snapshot: Snapshot | null; error: string | null }> {
  const db = connection();
  if (!db) return { snapshot: null, error: "尚未配置数据库连接" };
  try {
    const rows = await db`
      SELECT trade_date, finished_at, metrics
      FROM job_runs WHERE job_type='daily_scan' AND status='success'
      ORDER BY finished_at DESC LIMIT 1
    `;
    if (!rows.length) return { snapshot: null, error: "数据库已连接，但尚未发布成功的筛选结果" };
    const r = rows[0];
    return { snapshot: { trade_date: dateString(r.trade_date) ?? "",
      finished_at: (r.finished_at instanceof Date ? r.finished_at.toISOString() : String(r.finished_at)),
      metrics: r.metrics ?? {} }, error: null };
  } catch (error) {
    console.error("Read snapshot failed", error);
    // Do not return a cacheable failure: transient Neon cold-starts should retry.
    throw error;
  }
}

async function readScreeningRows(day: string, strategy: string): Promise<ScreenerRow[]> {
  const db = connection();
  if (!db || !/^\d{4}-\d{2}-\d{2}$/.test(day) || !/^[a-z0-9_]+$/.test(strategy)) return [];
  try {
    const rows = await db`
      SELECT r.*,s.name,s.industry,s.market,
        CASE
          WHEN r.operating_cf_yuan IS NOT NULL THEN 'VERIFIED'
          WHEN EXISTS (
            SELECT 1 FROM financial_reports f
            WHERE f.ts_code=r.ts_code AND f.kind='cashflow'
              AND f.disclosed_at < r.trade_date AND f.end_date <= r.trade_date
          ) THEN 'FIELD_MISSING'
          WHEN EXISTS (
            SELECT 1 FROM financial_reports f
            WHERE f.ts_code=r.ts_code AND f.kind='cashflow'
              AND f.disclosed_at >= r.trade_date AND f.end_date <= r.trade_date
          ) THEN 'NOT_YET_DISCLOSED'
          ELSE 'REPORT_MISSING'
        END AS cashflow_detail
      FROM screen_results r JOIN stocks s USING(ts_code)
      JOIN job_runs j ON j.run_id=r.run_id AND j.status='success'
      WHERE r.trade_date=${day} AND r.strategy=${strategy}
      ORDER BY r.is_candidate DESC, r.ncav_coverage DESC NULLS LAST
      LIMIT 3000
    `;
    return rows.map((row) => normalizeRow(row as Record<string, unknown>));
  } catch (error) {
    console.error("Read screening rows failed", error);
    // Never cache a transient query failure as a legitimate empty selection.
    throw error;
  }
}

// Share one short-lived, read-only result across navigation and filter switches.
// Daily publication uses a date-specific row key; no cross-strategy result fallback.
const cachedSnapshot = unstable_cache(readLatestSnapshot, ["latest-daily-snapshot-v2"], { revalidate: 30 });
const cachedScreening = unstable_cache(readScreeningRows, ["strategy-screen-rows-v2"], { revalidate: 90 });

export async function latestSnapshot(): Promise<{ snapshot: Snapshot | null; error: string | null }> {
  try {
    return await cachedSnapshot();
  } catch {
    return { snapshot: null, error: "网站暂时无法读取数据库，已发布的筛选结果并未删除。请稍后重试。" };
  }
}
export async function screeningRowsStatus(day: string, strategy: string = "netnet_v1"):
  Promise<{ rows: ScreenerRow[]; error: string | null }> {
  try {
    return { rows: await cachedScreening(day, strategy), error: null };
  } catch {
    return { rows: [], error: "股票结果读取失败，不能将其视为筛选结果为零。" };
  }
}
export async function screeningRows(day: string, strategy: string = "netnet_v1"): Promise<ScreenerRow[]> {
  const result = await screeningRowsStatus(day, strategy);
  return result.rows;
}

export async function stockDetail(code: string): Promise<{ stock: ScreenerRow | null; prices: { day: string; close: number }[]; recent: ScreenerRow[] }> {
  const db = connection();
  if (!db || !/^\d{6}\.(SH|SZ|BJ)$/.test(code)) return { stock: null, prices: [], recent: [] };
  try {
    const rows = await db`
      SELECT r.*,s.name,s.industry,s.market,
        CASE
          WHEN r.operating_cf_yuan IS NOT NULL THEN 'VERIFIED'
          WHEN EXISTS (
            SELECT 1 FROM financial_reports f
            WHERE f.ts_code=r.ts_code AND f.kind='cashflow'
              AND f.disclosed_at < r.trade_date AND f.end_date <= r.trade_date
          ) THEN 'FIELD_MISSING'
          WHEN EXISTS (
            SELECT 1 FROM financial_reports f
            WHERE f.ts_code=r.ts_code AND f.kind='cashflow'
              AND f.disclosed_at >= r.trade_date AND f.end_date <= r.trade_date
          ) THEN 'NOT_YET_DISCLOSED'
          ELSE 'REPORT_MISSING'
        END AS cashflow_detail FROM screen_results r
      JOIN stocks s USING(ts_code)
      JOIN job_runs j ON j.run_id=r.run_id AND j.status='success'
      WHERE r.ts_code=${code} AND r.strategy='netnet_v1'
      ORDER BY r.trade_date DESC LIMIT 30
    `;
    const recent = rows.map((row) => normalizeRow(row as Record<string, unknown>));
    // Charts and technical signals must use the same compact historical close
    // source. Prefer the verified daily snapshot on duplicate trade dates.
    // Keep history-query failures separate from the stock's research record.
    let prices: { day: string; close: number }[] = [];
    try {
      const history = await db`
        WITH combined AS (
          SELECT trade_date, close, 2 AS source_priority
          FROM daily_prices WHERE ts_code=${code}
          UNION ALL
          SELECT trade_date, close, 1 AS source_priority
          FROM price_history WHERE ts_code=${code}
        ), unique_sessions AS (
          SELECT DISTINCT ON (trade_date) trade_date, close
          FROM combined
          ORDER BY trade_date DESC, source_priority DESC
        )
        SELECT trade_date, close FROM (
          SELECT trade_date, close FROM unique_sessions
          ORDER BY trade_date DESC LIMIT 120
        ) p ORDER BY trade_date ASC
      `;
      prices = history.map(row => ({
        day: dateString(row.trade_date) ?? "", close: Number(row.close),
      }));
    } catch (historyError) {
      // If a newly created price_history table has not been granted to
      // DATABASE_READ_URL yet, keep the stock page available and log the issue.
      console.error("Read compact price history failed; verify SELECT privileges", historyError);
      const fallback = await db`
        SELECT trade_date, close FROM (
          SELECT trade_date, close FROM daily_prices WHERE ts_code=${code}
          ORDER BY trade_date DESC LIMIT 120
        ) p ORDER BY trade_date ASC
      `;
      prices = fallback.map(row => ({
        day: dateString(row.trade_date) ?? "", close: Number(row.close),
      }));
    }
    return { stock: recent[0] ?? null, prices, recent };
  } catch (error) {
    console.error("Read stock failed", error);
    return { stock: null, prices: [], recent: [] };
  }
}
