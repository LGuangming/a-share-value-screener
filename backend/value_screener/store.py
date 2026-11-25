"""PostgreSQL persistence. Imported only by live CLI, never by pure unit tests."""
from __future__ import annotations
from datetime import datetime, timedelta, date
from uuid import UUID
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .rules import NETNET_IGNORED_INDUSTRIES
from .archive_storage import archive_driver


class Store:
    def __init__(self, url: str):
        self.url = url

    def connect(self):
        return psycopg.connect(self.url, row_factory=dict_row)

    @staticmethod
    def _parse_date(yyyymmdd: str | None) -> date | None:
        if not yyyymmdd or len(yyyymmdd) != 8:
            return None
        return date.fromisoformat(f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}")

    def initialize(self, schema_text: str):
        if archive_driver() == "local":
            raise RuntimeError("Local database schema is provisioned by the administrator; init-db disabled")
        with self.connect() as conn:
            conn.execute(schema_text)

    def start_run(self, run_id: UUID, kind: str, trade_day: date):
        with self.connect() as conn:
            conn.execute("INSERT INTO job_runs (run_id,job_type,trade_date,status) VALUES (%s,%s,%s,'running')",
                         (run_id, kind, trade_day))

    def finish_run(self, run_id: UUID, status: str, metrics: dict | None = None, error: str | None = None):
        with self.connect() as conn:
            conn.execute("UPDATE job_runs SET status=%s,finished_at=now(),metrics=%s,error_text=%s WHERE run_id=%s",
                         (status, Jsonb(metrics or {}), (error or "")[:1500] or None, run_id))

    def upsert_stocks(self, rows: list[dict]):
        values = []
        for row in rows:
            code = str(row.get("ts_code") or "")
            if not code.endswith((".SH", ".SZ", ".BJ")):
                continue
            name = str(row.get("name") or code)
            values.append((code, name, row.get("industry"), row.get("market"),
                           self._parse_date(row.get("list_date")), "ST" in name.upper()))
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO stocks (ts_code,name,industry,market,list_date,is_st)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ts_code) DO UPDATE SET
                      name=excluded.name,industry=excluded.industry,market=excluded.market,
                      list_date=excluded.list_date,is_st=excluded.is_st,updated_at=now()
                """, values)
        return len(values)

    def upsert_quotes(self, rows: list[dict], day: date):
        values = []
        for row in rows:
            try:
                market_cap_yuan = float(row["total_mv"]) * 10000
                price = float(row["close"])
            except (ValueError, TypeError, KeyError):
                continue
            if market_cap_yuan <= 0 or price <= 0:
                continue
            values.append((row["ts_code"], day, price, row.get("pb"),
                           row.get("turnover_rate"), market_cap_yuan, Jsonb(row)))
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO daily_prices
                      (ts_code,trade_date,close,pb,turnover_rate,market_cap_yuan,raw)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ts_code,trade_date) DO UPDATE SET
                      close=excluded.close,pb=excluded.pb,turnover_rate=excluded.turnover_rate,
                      market_cap_yuan=excluded.market_cap_yuan,raw=excluded.raw
                """, values)
        return len(values)

    def ensure_price_history_tables(self):
        """Validate migrated local tables; retain legacy cloud bootstrap behavior."""
        if archive_driver() == "local":
            # M12 restored these tables with a restricted non-owner writer.
            # Never issue CREATE or GRANT against the migrated schema.
            with self.connect() as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SELECT ts_code,trade_date,close FROM public.price_history LIMIT 0")
                conn.execute("SELECT trade_date,rows_saved,fetched_at FROM public.price_history_fetches LIMIT 0")
            return
        with self.connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    ts_code text NOT NULL REFERENCES stocks(ts_code),
                    trade_date date NOT NULL,
                    close numeric(16,4) NOT NULL CHECK (close > 0),
                    PRIMARY KEY (ts_code, trade_date)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS price_history_fetches (
                    trade_date date PRIMARY KEY,
                    rows_saved integer NOT NULL CHECK (rows_saved > 0),
                    fetched_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            # The website can use a dedicated read-only Neon login created
            # before price_history existed. PostgreSQL does not automatically
            # grant that older login SELECT on newly created tables.
            # Never grant price history to PUBLIC or an unknown role.
            reader_exists = conn.execute("""
                SELECT 1 FROM pg_roles WHERE rolname='screener_reader'
            """).fetchone()
            if reader_exists:
                conn.execute("GRANT SELECT ON TABLE price_history TO screener_reader")

    def completed_history_days(self, start: date, end: date,
                               min_full_quotes: int) -> set[date]:
        """Resume via committed history-day markers or complete daily snapshots."""
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT trade_date FROM price_history_fetches
                WHERE trade_date BETWEEN %s AND %s
                UNION
                SELECT trade_date FROM daily_prices
                WHERE trade_date BETWEEN %s AND %s
                GROUP BY trade_date
                HAVING COUNT(*) >= %s
            """, (start, end, start, end, min_full_quotes)).fetchall()
        return {r["trade_date"] for r in rows}

    def save_compact_history(self, day: date, rows: list[dict],
                             current_codes: set[str], min_rows: int) -> int:
        """Insert valid close prices and completion marker atomically per day.

        Restrict to currently listed symbols to preserve the stocks foreign key.
        No multi-year OHLC/JSON data is duplicated into the expensive daily table.
        """
        prices = []
        for row in rows:
            code = str(row.get("ts_code") or "")
            if code not in current_codes:
                continue
            try:
                close = float(row["close"])
            except (TypeError, ValueError, KeyError):
                continue
            if not (0 < close < float("inf")):
                continue
            prices.append((code, day, close))
        if len(prices) < min_rows:
            raise RuntimeError(
                f"{day}: only {len(prices)} eligible valid close prices "
                f"(minimum {min_rows}); no completion marker was saved"
            )
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO price_history (ts_code,trade_date,close)
                    VALUES (%s,%s,%s)
                    ON CONFLICT (ts_code,trade_date) DO UPDATE
                    SET close=excluded.close
                """, prices)
            conn.execute("""
                INSERT INTO price_history_fetches (trade_date,rows_saved,fetched_at)
                VALUES (%s,%s,now())
                ON CONFLICT (trade_date) DO UPDATE
                SET rows_saved=excluded.rows_saved, fetched_at=excluded.fetched_at
            """, (day, len(prices)))
        return len(prices)

    def latest_quote_date(self, asof: date) -> date | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) AS quote_day FROM daily_prices WHERE trade_date <= %s",
                (asof,),
            ).fetchone()
        return row["quote_day"] if row else None

    def has_successful_data_trigger(self, github_run_id: int) -> bool:
        """Only valid successful data changes should trigger NCAV publication.

        A successful read-only API probe, unchanged market re-fetch, or a
        financial report disclosed after the last market session cannot trigger.
        """
        with self.connect() as conn:
            row = conn.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM job_runs
                    WHERE job_type IN ('market_refresh', 'financial_refresh')
                      AND status='success'
                      AND metrics->>'github_run_id' = %s
                      AND metrics->>'recalc_needed' = 'true'
                ) AS changed
            """, (str(github_run_id),)).fetchone()
        return bool(row["changed"])

    def has_successful_local_trigger(self, local_run_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM job_runs
                    WHERE job_type IN ('market_refresh','financial_refresh')
                      AND status='success'
                      AND metrics->>'local_run_id'=%s
                      AND metrics->>'recalc_needed'='true'
                ) AS changed
            """,(str(local_run_id),)).fetchone()
        return bool(row["changed"])

    def has_published_snapshot(self, quote_day: date) -> bool:
        """A previously successful same-day NCAV publication exists."""
        with self.connect() as conn:
            row = conn.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM job_runs
                    WHERE job_type='daily_scan' AND status='success'
                      AND trade_date=%s
                ) AS exists
            """, (quote_day,)).fetchone()
        return bool(row["exists"])

    def market_data_signature(self, quote_day: date) -> str:
        """Hash the actual quote and stock metadata used by the screen.

        Excludes volatile ingested/updated timestamps; identical source data
        therefore does not cause a pointless recalculation.
        """
        with self.connect() as conn:
            row = conn.execute("""
                SELECT md5(coalesce(string_agg(
                    p.ts_code || ':' || p.raw::text || ':' || s.name || ':' ||
                    coalesce(s.market, '') || ':' || coalesce(s.industry, '') || ':' || s.is_st::text,
                    '|' ORDER BY p.ts_code
                ), '')) AS fingerprint
                FROM daily_prices p
                JOIN stocks s USING(ts_code)
                WHERE p.trade_date=%s
            """, (quote_day,)).fetchone()
        return row["fingerprint"]

    def financial_inputs_signature(self, quote_day: date) -> str:
        """Hash only statements that can influence this quote day's NCAV.

        A Saturday report disclosed after Friday cannot affect Friday's
        historical snapshot. Only the most recent eligible consolidated report
        of each kind is considered, matching screen_inputs exactly.
        """
        with self.connect() as conn:
            row = conn.execute("""
                SELECT md5(coalesce(string_agg(
                    p.ts_code || ':' || k.kind || ':' ||
                    coalesce(f.end_date::text, '') || ':' ||
                    coalesce(f.disclosed_at::text, '') || ':' ||
                    coalesce(f.metrics::text, ''),
                    '|' ORDER BY p.ts_code, k.kind
                ), '')) AS fingerprint
                FROM daily_prices p
                JOIN stocks s USING(ts_code)
                CROSS JOIN (VALUES ('balancesheet'), ('income'), ('cashflow')) AS k(kind)
                LEFT JOIN LATERAL (
                    SELECT end_date, disclosed_at, metrics
                    FROM financial_reports f
                    WHERE f.ts_code=p.ts_code AND f.kind=k.kind
                      AND f.disclosed_at<p.trade_date
                      AND f.end_date<=p.trade_date
                    ORDER BY f.end_date DESC, f.disclosed_at DESC
                    LIMIT 1
                ) f ON true
                WHERE p.trade_date=%s
                  
                  AND s.market='主板' AND right(p.ts_code,3) IN ('.SH','.SZ')
            """, (quote_day,)).fetchone()
        return row["fingerprint"]

    def quote_coverage(self, quote_day: date) -> dict:
        """Verify cached market completeness without any provider API calls."""
        with self.connect() as conn:
            row = conn.execute("""
                SELECT (SELECT COUNT(*) FROM stocks) AS listed_stocks,
                       (SELECT COUNT(*) FROM daily_prices WHERE trade_date=%s) AS quoted_stocks
            """, (quote_day,)).fetchone()
        return {"listed_stocks": int(row["listed_stocks"]),
                "quoted_stocks": int(row["quoted_stocks"])}

    def archive_financial_targets(self, quote_day: date, asof: date, cutoff: date) -> dict:
        """All quoted non-ST main-board companies; include missing or older periods."""
        with self.connect() as conn:
            rows=conn.execute("""
                SELECT p.ts_code,
                       MAX(r.end_date) FILTER (WHERE r.kind='balancesheet') AS balancesheet,
                       MAX(r.end_date) FILTER (WHERE r.kind='income') AS income,
                       MAX(r.end_date) FILTER (WHERE r.kind='cashflow') AS cashflow
                FROM daily_prices p JOIN stocks s USING(ts_code)
                LEFT JOIN financial_reports r ON r.ts_code=p.ts_code
                  AND r.disclosed_at<=%s AND r.end_date<=%s AND r.report_type='1'
                WHERE p.trade_date=%s 
                  AND NOT s.is_st AND s.market='主板' AND right(p.ts_code,3) IN ('.SH','.SZ')
                  AND NOT (btrim(coalesce(s.industry,'')) = ANY(%s::text[]))
                GROUP BY p.ts_code
                HAVING COALESCE(MAX(r.end_date) FILTER (WHERE r.kind='balancesheet'),DATE '1900-01-01')<%s
                    OR COALESCE(MAX(r.end_date) FILTER (WHERE r.kind='income'),DATE '1900-01-01')<%s
                    OR COALESCE(MAX(r.end_date) FILTER (WHERE r.kind='cashflow'),DATE '1900-01-01')<%s
            """,(asof,asof,quote_day,list(NETNET_IGNORED_INDUSTRIES),cutoff,cutoff,cutoff)).fetchall()
        return {r["ts_code"]:{k:r[k] for k in ("balancesheet","income","cashflow")} for r in rows}

    def insert_archive_financials(self, items, asof: date) -> tuple[int,int]:
        """Insert verified new periods in bounded batches, never overwrite live data."""
        payload=[]
        for code,kind,end,disclosed,row,provenance in items:
            if row.get("ts_code")!=code or str(row.get("report_type"))!="1" or kind not in ("balancesheet","income","cashflow") or not end<=disclosed<=asof:
                raise RuntimeError("Invalid archived financial import row")
            metrics={k:v for k,v in row.items() if k not in {"ts_code","ann_date","f_ann_date","end_date","report_type","comp_type","update_flag"}}
            payload.append({"ts_code":code,"kind":kind,"end_date":end.isoformat(),
                            "disclosed_at":disclosed.isoformat(),"metrics":metrics,
                            "raw":{**row,"_archive_import":provenance}})
        inserted=0;companies=set()
        if not payload:
            return inserted,0
        with self.connect() as conn:
            conn.execute("SET LOCAL lock_timeout='15s'")
            conn.execute("LOCK TABLE financial_reports IN SHARE ROW EXCLUSIVE MODE")
            for offset in range(0,len(payload),250):
                result=conn.execute("""
                    INSERT INTO financial_reports
                      (ts_code,kind,end_date,disclosed_at,report_type,metrics,raw)
                    SELECT x.ts_code,x.kind,x.end_date,x.disclosed_at,'1',x.metrics,x.raw
                    FROM jsonb_to_recordset(%s::jsonb) AS x(ts_code text,kind text,end_date date,disclosed_at date,metrics jsonb,raw jsonb)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM financial_reports f WHERE f.ts_code=x.ts_code AND f.kind=x.kind
                          AND f.report_type='1' AND f.end_date >= x.end_date AND f.disclosed_at<=%s
                    )
                    ON CONFLICT (ts_code,kind,end_date,disclosed_at,report_type) DO NOTHING
                    RETURNING ts_code
                """,(Jsonb(payload[offset:offset+250]),asof)).fetchall()
                inserted+=len(result);companies.update(row["ts_code"] for row in result)
        return inserted,len(companies)

    def financial_backlog(self, quote_day: date, asof: date, budget: int) -> dict:
        """Find missing / mismatched financial statements for current candidates.

        Do not query complete companies every 30 days. Count cooling companies
        as incomplete, but exclude them from today's limited attempt budget.
        """
        cutoff = asof - timedelta(days=280)
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT p.ts_code,
                       (log.attempted_at IS NULL OR
                        log.attempted_at < now() - interval '6 hours') AS ready
                FROM daily_prices p
                JOIN stocks s USING(ts_code)
                LEFT JOIN financial_fetch_log log USING(ts_code)
                LEFT JOIN financial_reports r
                  ON r.ts_code=p.ts_code AND r.disclosed_at<=%s AND r.end_date<=%s
                WHERE p.trade_date=%s
                  
                  AND NOT s.is_st
                  AND s.market='主板' AND right(p.ts_code,3) IN ('.SH','.SZ')
                  AND NOT (btrim(coalesce(s.industry,'')) = ANY(%s::text[]))
                GROUP BY p.ts_code, p.pb, p.market_cap_yuan, log.attempted_at
                HAVING MAX(r.end_date) FILTER (WHERE r.kind='balancesheet') IS NULL
                    OR MAX(r.end_date) FILTER (WHERE r.kind='balancesheet') < %s
                    OR MAX(r.end_date) FILTER (WHERE r.kind='income') IS DISTINCT FROM
                       MAX(r.end_date) FILTER (WHERE r.kind='balancesheet')
                    OR MAX(r.end_date) FILTER (WHERE r.kind='cashflow') IS DISTINCT FROM
                       MAX(r.end_date) FILTER (WHERE r.kind='balancesheet')
                ORDER BY log.attempted_at ASC NULLS FIRST,
                         p.pb ASC NULLS LAST, p.market_cap_yuan ASC
            """, (asof, asof, quote_day, list(NETNET_IGNORED_INDUSTRIES), cutoff)).fetchall()
        return {"remaining": len(rows),
                "queue": [row["ts_code"] for row in rows if row["ready"]][:budget]}

    def financial_disclosure_queue(self, quote_day: date, asof: date,
                                    events: list[dict], budget: int) -> list[dict]:
        """Only poll confirmed new quarter releases not yet stored completely."""
        if not events or budget <= 0:
            return []
        codes = [e["ts_code"] for e in events]
        periods = [e["period"] for e in events]
        actuals = [e["actual_date"] for e in events]
        with self.connect() as conn:
            rows = conn.execute("""
                WITH event_input AS (
                    SELECT DISTINCT ON (ts_code) ts_code, period, actual_date
                    FROM unnest(%s::text[], %s::date[], %s::date[])
                         AS e(ts_code, period, actual_date)
                    ORDER BY ts_code, period DESC, actual_date DESC
                )
                SELECT e.ts_code, e.period, e.actual_date
                FROM event_input e
                JOIN daily_prices p ON p.ts_code=e.ts_code AND p.trade_date=%s
                JOIN stocks s ON s.ts_code=e.ts_code
                LEFT JOIN financial_fetch_log log ON log.ts_code=e.ts_code
                LEFT JOIN financial_reports r
                  ON r.ts_code=e.ts_code AND r.end_date=e.period
                 AND r.disclosed_at<=%s AND r.report_type='1'
                WHERE NOT s.is_st
                  AND s.market='主板' AND right(p.ts_code,3) IN ('.SH','.SZ')
                  AND NOT (btrim(coalesce(s.industry,'')) = ANY(%s::text[]))
                  AND (log.attempted_at IS NULL OR
                       log.attempted_at < now()-interval '6 hours')
                GROUP BY e.ts_code, e.period, e.actual_date, p.pb, p.market_cap_yuan
                HAVING COUNT(DISTINCT r.kind) FILTER
                     (WHERE r.disclosed_at>=e.actual_date) < 3
                ORDER BY e.actual_date ASC, p.pb ASC NULLS LAST,
                         p.market_cap_yuan ASC
                LIMIT %s
            """, (codes, periods, actuals, quote_day, asof, list(NETNET_IGNORED_INDUSTRIES), budget)).fetchall()
        return [dict(row) for row in rows]

    def financial_missing_kinds(self, code: str, asof: date,
                                period: date | None = None,
                                actual_date: date | None = None) -> list[str]:
        """Fetch only missing statement kinds (or all if a newer revision exists)."""
        kinds = ("balancesheet", "income", "cashflow")
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT kind, MAX(end_date) AS report_period,
                       MAX(disclosed_at) AS announced
                FROM financial_reports
                WHERE ts_code=%s AND disclosed_at<=%s
                  AND (%s::date IS NULL OR end_date=%s::date)
                GROUP BY kind
            """, (code, asof, period, period)).fetchall()
        stored = {row["kind"]: row for row in rows}
        if period is not None:
            return [kind for kind in kinds
                    if kind not in stored or
                    (actual_date is not None and stored[kind]["announced"] < actual_date)]
        newest = max((row["report_period"] for row in rows), default=None)
        if newest is None or newest < asof - timedelta(days=280):
            return list(kinds)
        return [kind for kind in kinds
                if kind not in stored or stored[kind]["report_period"] != newest]

    def ingest_reports(self, code: str, kind: str, rows: list[dict]):
        payload = []
        for row in rows:
            if str(row.get("report_type")) != "1":
                continue  # Only latest consolidated reports, not parent/cohort variants.
            disclosed = self._parse_date(row.get("f_ann_date") or row.get("ann_date"))
            end_date = self._parse_date(row.get("end_date"))
            if disclosed is None or end_date is None or disclosed < end_date:
                continue
            metrics = {k: v for k, v in row.items() if k not in {
                "ts_code","ann_date","f_ann_date","end_date","report_type","comp_type","update_flag"}}
            payload.append((code, kind, end_date, disclosed, "1", Jsonb(metrics), Jsonb(row)))
        if not payload:
            return 0
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO financial_reports
                        (ts_code,kind,end_date,disclosed_at,report_type,metrics,raw)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ts_code,kind,end_date,disclosed_at,report_type)
                    DO UPDATE SET metrics=excluded.metrics,raw=excluded.raw,ingested_at=now()
                """, payload)
        return len(payload)

    def ingest_bulk_reports(self, kind: str, rows: list[dict], listed_codes: set[str]) -> tuple[int,set[str]]:
        """Batch ingest a provider-authorized VIP quarter; returns (rows, eligible codes)."""
        payload = []
        codes = set()
        for row in rows:
            code = str(row.get("ts_code") or "")
            if code not in listed_codes or str(row.get("report_type")) != "1":
                continue
            disclosed = self._parse_date(row.get("f_ann_date") or row.get("ann_date"))
            end_date = self._parse_date(row.get("end_date"))
            if disclosed is None or end_date is None or disclosed < end_date:
                continue
            metrics = {k: v for k, v in row.items() if k not in {
                "ts_code","ann_date","f_ann_date","end_date","report_type","comp_type","update_flag"}}
            payload.append((code,kind,end_date,disclosed,"1",Jsonb(metrics),Jsonb(row)))
            codes.add(code)
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO financial_reports
                        (ts_code,kind,end_date,disclosed_at,report_type,metrics,raw)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (ts_code,kind,end_date,disclosed_at,report_type)
                    DO UPDATE SET metrics=excluded.metrics,raw=excluded.raw,ingested_at=now()
                """, payload)
        return len(payload), codes

    def mark_bulk_financial_success(self, codes: set[str]):
        if not codes:
            return
        values = [(code,) for code in codes]
        with self.connect() as conn:
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO financial_fetch_log(ts_code,attempted_at,succeeded_at,last_error)
                    VALUES (%s,now(),now(),NULL)
                    ON CONFLICT(ts_code) DO UPDATE SET
                      attempted_at=now(),succeeded_at=now(),last_error=NULL
                """, values)

    def mark_financial_fetch(self, code: str, success: bool, error: str | None = None):
        with self.connect() as conn:
            conn.execute("""
                INSERT INTO financial_fetch_log (ts_code,attempted_at,succeeded_at,last_error)
                VALUES (%s,now(),CASE WHEN %s THEN now() ELSE NULL END,%s)
                ON CONFLICT(ts_code) DO UPDATE SET
                  attempted_at=now(),
                  succeeded_at=CASE WHEN %s THEN now() ELSE financial_fetch_log.succeeded_at END,
                  last_error=excluded.last_error
            """, (code, success, error, success))

    def screen_inputs(self, day: date) -> list[dict]:
        with self.connect() as conn:
            # Conservative point-in-time: financial disclosure must predate the
            # close-price session, because only calendar dates (not intraday times)
            # are supplied by the statement API. Prevent same-day after-close leaks.
            rows = conn.execute("""
                SELECT s.ts_code,s.name,s.is_st,s.market,s.industry,p.raw AS quote,
                       b.metrics AS balance,b.end_date AS report_date,b.disclosed_at AS ann_date,
                       i.metrics AS income,i.end_date AS income_end_date,
                       c.metrics AS cashflow,c.end_date AS cashflow_end_date
                FROM daily_prices p JOIN stocks s USING(ts_code)
                LEFT JOIN LATERAL (
                   SELECT metrics,end_date,disclosed_at FROM financial_reports f
                   WHERE f.ts_code=p.ts_code AND f.kind='balancesheet'
                     AND f.disclosed_at<p.trade_date AND f.end_date<=p.trade_date
                   ORDER BY f.end_date DESC,f.disclosed_at DESC LIMIT 1
                ) b ON true
                LEFT JOIN LATERAL (
                   SELECT metrics,end_date FROM financial_reports f
                   WHERE f.ts_code=p.ts_code AND f.kind='income'
                     AND f.disclosed_at<p.trade_date AND f.end_date<=p.trade_date
                   ORDER BY f.end_date DESC,f.disclosed_at DESC LIMIT 1
                ) i ON true
                LEFT JOIN LATERAL (
                   SELECT metrics,end_date FROM financial_reports f
                   WHERE f.ts_code=p.ts_code AND f.kind='cashflow'
                     AND f.disclosed_at<p.trade_date AND f.end_date<=p.trade_date
                   ORDER BY f.end_date DESC,f.disclosed_at DESC LIMIT 1
                ) c ON true
                WHERE p.trade_date=%s 
                  AND s.market='主板' AND right(p.ts_code,3) IN ('.SH','.SZ')
                ORDER BY p.market_cap_yuan ASC
            """, (day,)).fetchall()
        return rows

    def price_history(self, codes: list[str], day: date,
                      limit_days: int = 90) -> dict[str,list[float]]:
        """Merge compact historical closes with daily quotes without duplicates.

        Prefer daily_prices when both tables contain the same trade date, then
        take the latest unique 90 trading sessions, without forward prices.
        """
        if not codes:
            return {}
        with self.connect() as conn:
            rows = conn.execute("""
                WITH combined AS (
                    SELECT ts_code,trade_date,close,2 AS priority
                    FROM daily_prices WHERE ts_code = ANY(%s) AND trade_date<=%s
                    UNION ALL
                    SELECT ts_code,trade_date,close,1 AS priority
                    FROM price_history WHERE ts_code = ANY(%s) AND trade_date<=%s
                ), deduped AS (
                    SELECT DISTINCT ON (ts_code,trade_date)
                           ts_code,trade_date,close
                    FROM combined
                    ORDER BY ts_code,trade_date,priority DESC
                ), ranked AS (
                    SELECT ts_code,trade_date,close,
                           row_number() OVER (
                               PARTITION BY ts_code ORDER BY trade_date DESC
                           ) AS rn
                    FROM deduped
                )
                SELECT ts_code,trade_date,close FROM ranked
                WHERE rn<=%s ORDER BY ts_code,trade_date
            """, (codes, day, codes, day, limit_days)).fetchall()
        values: dict[str,list[float]] = {code: [] for code in codes}
        for item in rows:
            values[item["ts_code"]].append(float(item["close"]))
        return values


    def publish(self, day: date, run_id: UUID, candidates: list[dict], metrics: dict,
                strategy: str = "netnet_v1"):
        if strategy != "netnet_v1":
            raise ValueError("Unrecognized screener strategy; refusing to write")
        values = [(
            day,r["ts_code"],run_id,strategy,r["price"],r["pb"],r["market_cap"],
            r["ncav"],r["coverage"],r["stress_ncav"],r["stress_coverage"],
            r["liability_ratio"],r["report_date"],r["ann_date"],r["operating_cf"],
            r["net_profit"],r["is_candidate"],r["status"],r["signal"],
            Jsonb(r["flags"]),r["assessment"]
        ) for r in candidates]
        with self.connect() as conn:
            # Atomic replacement, even if GitHub Actions runs twice for one day.
            conn.execute("DELETE FROM screen_results WHERE trade_date=%s AND strategy=%s", (day, strategy))
            with conn.cursor() as cur:
                cur.executemany("""
                    INSERT INTO screen_results (trade_date,ts_code,run_id,strategy,
                      price,pb,market_cap_yuan,ncav_yuan,ncav_coverage,
                      stress_ncav_yuan,stress_coverage,liability_ratio,
                      report_date,disclosed_at,operating_cf_yuan,net_profit_yuan,
                      is_candidate,status,signal,flags,assessment)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, values)
            # A completed snapshot and its success marker share ONE transaction.
            # If publishing fails, the previous snapshot and success marker remain.
            conn.execute("""
                UPDATE job_runs SET status='success',finished_at=now(),metrics=%s,error_text=NULL
                WHERE run_id=%s AND status='running'
            """, (Jsonb(metrics), run_id))
        return len(values)
