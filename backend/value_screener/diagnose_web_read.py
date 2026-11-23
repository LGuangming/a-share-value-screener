"""Read-only database diagnostics; prints only counts and dates, no individual securities."""
from __future__ import annotations

import json
import os
import psycopg

url=os.environ.get("DATABASE_URL")
if not url:
    raise RuntimeError("DATABASE_URL is not configured in GitHub Actions")
with psycopg.connect(url,connect_timeout=15) as db:
    cols=db.execute("""
        SELECT
          (SELECT count(*) FROM job_runs
           WHERE job_type='daily_scan' AND status='success') AS successful_scans,
          (SELECT max(trade_date) FROM job_runs
           WHERE job_type='daily_scan' AND status='success') AS latest_scan_date,
          (SELECT count(*) FROM screen_results WHERE strategy='netnet_v1') AS netnet_rows,
          (SELECT max(trade_date) FROM screen_results
           WHERE strategy='netnet_v1') AS latest_netnet_date,
          (SELECT count(*) FROM screen_results r JOIN job_runs j
           ON j.run_id=r.run_id AND j.status='success'
           WHERE r.strategy='netnet_v1') AS published_netnet_rows
    """).fetchone()
    report={"successful_scans":cols[0],
            "latest_scan_date":str(cols[1]) if cols[1] else None,
            "netnet_rows":cols[2],
            "latest_netnet_date":str(cols[3]) if cols[3] else None,
            "published_netnet_rows":cols[4]}
    reader=db.execute("SELECT 1 FROM pg_roles WHERE rolname='screener_reader'").fetchone()
    report["screener_reader_exists"]=bool(reader)
    if reader:
        for table in ("job_runs","screen_results","stocks","financial_reports"):
            report["screener_reader_select_"+table]=db.execute(
                "SELECT has_table_privilege(%s,%s,'SELECT')",
                ("screener_reader",table)
            ).fetchone()[0]
    print("WEB_READ_DIAGNOSTIC "+json.dumps(report,sort_keys=True),flush=True)
