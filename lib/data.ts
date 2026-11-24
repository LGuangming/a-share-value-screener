import "server-only";
import { neon } from "@neondatabase/serverless";
import { unstable_cache } from "next/cache";

// Vercel Function read-only queries use HTTPS to Neon's SQL endpoint.
// The previous postgres.js TCP path was repeatedly failing with CONNECT_TIMEOUT
// on port 5432. No persistent serverless TCP pool is needed with neon().
// Never fall back to DATABASE_URL: only the restricted reader credential is used.
function makeReader(url: string) {
  // Explicit response mode narrows Neon's generic type to row objects, not
  // the union of array rows and full-result metadata.
  return neon(url, { arrayMode: false, fullResults: false });
}
let client: ReturnType<typeof makeReader> | null = null;

function connection() {
  const url = process.env.DATABASE_READ_URL;
  if (!url) return null;
  if (!client) client = makeReader(url);
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
    financial_ignored?: number;
    netnet_applicable_pre_screened?: number;
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
  if (!db) return { snapshot: null, error: "筛选数据源暂不可用" };
  try {
    const rows = await db`
      SELECT trade_date, finished_at, metrics
      FROM job_runs WHERE job_type='daily_scan' AND status='success'
      ORDER BY finished_at DESC LIMIT 1
    `;
    if (!rows.length) return { snapshot: null, error: "暂无已发布的筛选结果" };
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
    return { snapshot: null, error: "筛选数据暂不可用，请稍后重试。" };
  }
}
export async function screeningRowsStatus(day: string, strategy: string = "netnet_v1"):
  Promise<{ rows: ScreenerRow[]; error: string | null }> {
  try {
    return { rows: await cachedScreening(day, strategy), error: null };
  } catch {
    return { rows: [], error: "股票筛选结果暂不可用，请稍后重试。" };
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

export async function searchStockIdentities(query:string):Promise<{ts_code:string;name:string;industry:string|null}[]>{
  const db=connection();
  if(!db)throw new Error("Stock directory unavailable");
  const q=query.trim().slice(0,40);
  if(!q)return [];
  // Use literal substring matching, not user-supplied SQL wildcard patterns.
  const rows=await db`
    SELECT ts_code,name,industry FROM stocks
    WHERE strpos(lower(ts_code),lower(${q}))>0 OR strpos(lower(name),lower(${q}))>0
    ORDER BY CASE WHEN upper(ts_code)=upper(${q}) THEN 0
      WHEN name=${q} THEN 1 WHEN strpos(lower(ts_code),lower(${q}))=1 THEN 2
      WHEN strpos(lower(name),lower(${q}))=1 THEN 3 ELSE 4 END,ts_code
    LIMIT 12
  `;
  return rows.map(r=>({ts_code:String(r.ts_code),name:String(r.name),industry:r.industry?String(r.industry):null}));
}

export async function recentMiniTrends(codes:string[]):Promise<Record<string,{day:string;close:number}[]>>{
  const db=connection();
  if(!db)throw new Error("Price source unavailable");
  const valid=[...new Set(codes)].filter(c=>/^\d{6}\.(SH|SZ|BJ)$/.test(c)).slice(0,25);
  if(!valid.length)return {};
  const rows=await db`
    SELECT u.ts_code,p.trade_date,p.close
    FROM unnest(string_to_array(${valid.join(",")}, ',')) AS u(ts_code)
    CROSS JOIN LATERAL (
      SELECT DISTINCT ON (trade_date) trade_date,close
      FROM (
        (SELECT trade_date,close,2 AS source_priority FROM daily_prices WHERE ts_code=u.ts_code ORDER BY trade_date DESC LIMIT 20)
        UNION ALL
        (SELECT trade_date,close,1 AS source_priority FROM price_history WHERE ts_code=u.ts_code ORDER BY trade_date DESC LIMIT 20)
      ) combined
      ORDER BY trade_date DESC,source_priority DESC LIMIT 20
    ) p
    ORDER BY u.ts_code,p.trade_date ASC
  `;
  const out:Record<string,{day:string;close:number}[]>={};
  for(const r of rows){const day=dateString(r.trade_date),close=numeric(r.close);if(day&&close!==null&&close>0)(out[String(r.ts_code)]??=[]).push({day,close});}
  return out;
}

export async function dataCenterSnapshot(){
  const db=connection();
  if(!db)throw Error("Database reader unavailable");
  const {snapshot,error}=await latestSnapshot();
  const jobs=await db`
    SELECT job_type,trade_date,status,started_at,finished_at
    FROM job_runs ORDER BY started_at DESC LIMIT 30
  `;
  const fresh=await db`
    SELECT max(trade_date) AS day FROM daily_prices
  `;
  const day=dateString(fresh[0]?.day);
  const counts=day?await db`
    SELECT count(*) AS rows,count(pb) AS pb,count(market_cap_yuan) AS market_cap
    FROM daily_prices WHERE trade_date=${day}
  `:[];
  return {snapshot,error,day,rows:numeric(counts[0]?.rows),pb:numeric(counts[0]?.pb),marketCap:numeric(counts[0]?.market_cap),jobs:jobs.map(r=>({type:String(r.job_type),day:dateString(r.trade_date),status:String(r.status),started:r.started_at instanceof Date?r.started_at.toISOString():String(r.started_at),finished:r.finished_at?(r.finished_at instanceof Date?r.finished_at.toISOString():String(r.finished_at)):null}))};
}

export async function publishedCandidateDates():Promise<string[]>{
  const db=connection();if(!db)throw Error("Database unavailable");
  const rows=await db`
    SELECT DISTINCT j.trade_date FROM job_runs j
    WHERE j.job_type='daily_scan' AND j.status='success'
    AND EXISTS (SELECT 1 FROM screen_results r WHERE r.run_id=j.run_id AND r.strategy='netnet_v1' AND r.trade_date=j.trade_date)
    ORDER BY j.trade_date DESC LIMIT 60
  `;
  return rows.map(r=>dateString(r.trade_date)).filter((d):d is string=>!!d);
}
export async function publishedCandidateChanges(day:string,previous:string){
  const db=connection();if(!db||!/^\d{4}-\d{2}-\d{2}$/.test(day)||!/^\d{4}-\d{2}-\d{2}$/.test(previous)||previous>=day)throw Error("Invalid publication dates");
  const rows=await db`
    WITH current_candidates AS (
      SELECT r.ts_code,r.ncav_coverage,r.price FROM screen_results r JOIN job_runs j ON j.run_id=r.run_id AND j.status='success'
      WHERE r.strategy='netnet_v1' AND r.trade_date=${day} AND r.is_candidate=true
    ), previous_candidates AS (
      SELECT r.ts_code,r.ncav_coverage,r.price FROM screen_results r JOIN job_runs j ON j.run_id=r.run_id AND j.status='success'
      WHERE r.strategy='netnet_v1' AND r.trade_date=${previous} AND r.is_candidate=true
    )
    SELECT COALESCE(c.ts_code,p.ts_code) AS ts_code,s.name,s.industry,
      CASE WHEN p.ts_code IS NULL THEN 'added' WHEN c.ts_code IS NULL THEN 'removed' ELSE 'retained' END AS change,
      p.ncav_coverage AS previous_coverage,c.ncav_coverage AS current_coverage,p.price AS previous_price,c.price AS current_price
    FROM current_candidates c FULL OUTER JOIN previous_candidates p USING(ts_code)
    JOIN stocks s ON s.ts_code=COALESCE(c.ts_code,p.ts_code)
    ORDER BY CASE WHEN p.ts_code IS NULL THEN 0 WHEN c.ts_code IS NULL THEN 1 ELSE 2 END,COALESCE(c.ts_code,p.ts_code)
  `;
  return rows.map(r=>({code:String(r.ts_code),name:String(r.name),industry:r.industry?String(r.industry):null,change:String(r.change) as "added"|"removed"|"retained",previousCoverage:numeric(r.previous_coverage),currentCoverage:numeric(r.current_coverage),previousPrice:numeric(r.previous_price),currentPrice:numeric(r.current_price)}));
}
