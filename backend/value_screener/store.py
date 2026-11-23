"""PostgreSQL persistence. Imported only by live CLI, never by pure unit tests."""
from __future__ import annotations
from datetime import datetime, timedelta, date
from uuid import UUID
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


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

    def latest_quote_date(self, asof: date) -> date | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) AS quote_day FROM daily_prices WHERE trade_date <= %s",
                (asof,),
            ).fetchone()
        return row["quote_day"] if row else None

    def financial_queue(self, day: date, budget: int) -> list[str]:
        # Prioritize genuine low-price/low-PB candidates and unsynced companies.
        # Missing PB is allowed because rejecting missing data can hide NCAV ideas.
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT p.ts_code FROM daily_prices p
                JOIN stocks s USING(ts_code)
                LEFT JOIN financial_fetch_log f USING(ts_code)
                WHERE p.trade_date=%s AND p.close<=10 AND (p.pb<=1.2 OR p.pb IS NULL)
                  AND NOT s.is_st AND s.market NOT IN ('科创板','创业板','北交所')
                  AND (f.succeeded_at IS NULL OR f.succeeded_at < now()-interval '30 days')
                  AND (f.attempted_at IS NULL OR f.attempted_at < now()-interval '6 hours')
                ORDER BY (f.succeeded_at IS NOT NULL), f.succeeded_at ASC NULLS FIRST,
                         p.pb ASC NULLS LAST, p.market_cap_yuan ASC
                LIMIT %s
            """, (day, budget)).fetchall()
        return [r["ts_code"] for r in rows]

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
                SELECT s.ts_code,s.name,s.is_st,s.market,p.raw AS quote,
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
                WHERE p.trade_date=%s AND p.close<=10 AND (p.pb<=1.2 OR p.pb IS NULL)
                  AND s.market NOT IN ('科创板','创业板','北交所')
                ORDER BY p.market_cap_yuan ASC
            """, (day,)).fetchall()
        return rows

    def price_history(self, codes: list[str], day: date, limit_days: int = 90) -> dict[str,list[float]]:
        if not codes:
            return {}
        with self.connect() as conn:
            rows = conn.execute("""
                SELECT ts_code,trade_date,close FROM (
                    SELECT ts_code,trade_date,close,
                           row_number() OVER (PARTITION BY ts_code ORDER BY trade_date DESC) AS rn
                    FROM daily_prices WHERE ts_code = ANY(%s) AND trade_date<=%s
                ) v WHERE rn<=%s ORDER BY ts_code,trade_date
            """, (codes,day,limit_days)).fetchall()
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
