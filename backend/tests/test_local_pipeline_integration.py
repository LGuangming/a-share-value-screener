"""Real isolated PostgreSQL and real DuckDB Parquet integration; no network providers."""
import gzip
import hashlib
import json
import os
from datetime import datetime,date,timedelta,timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from uuid import uuid4

@unittest.skipUnless(os.getenv("TEST_PIPELINE_DATABASE_URL"),"isolated integration database not configured")
class LocalPipelineIntegration(unittest.TestCase):
    def test_market_archive_finance_recalculate_and_audit(self):
        from value_screener import cli
        from value_screener.store import Store
        from value_screener.local_archive import LOCAL_BUCKET
        from value_screener.neon_archive import StorageConfig,s3_client
        from value_screener.phase2_market import put_manifest,get_optional_json
        from value_screener.raw_incremental import archive_session,market_run
        from value_screener.raw_month_reader import publish_month,month_root
        from value_screener.raw_maintenance import storage_state,retention_cutoffs,NotReady
        from value_screener.financial_archive_import import import_archive
        from value_screener.phase2_financial import FIELDS
        from value_screener.financial_schedule import recent_closed_quarters
        from value_screener.market_session import CN_TZ
        from value_screener.archive_paths import RAW_PREFIX
        url=os.environ["TEST_PIPELINE_DATABASE_URL"]
        parsed=urlsplit(url)
        self.assertEqual((parsed.hostname,parsed.path,parsed.username),("127.0.0.1","/ashare_pipeline_test","fixture_admin"))
        store=Store(url)
        schema=Path(__file__).resolve().parents[2]/"database/schema.sql"
        store.initialize(schema.read_text())
        fixture_acl = Path(__file__).resolve().parents[2]/"tests/fixtures/m12-acl.sql"
        with store.connect() as conn:
            conn.execute(fixture_acl.read_text())
        # Use the actual restricted writer for all subsequent business SQL.
        url = "postgresql://screener_writer:fixture_only@127.0.0.1:5432/ashare_pipeline_test"
        store = Store(url)
        today=datetime.now(CN_TZ).date()
        day=today-timedelta(days=3)
        self.assertGreaterEqual(day.year,2026)
        ds=day.strftime("%Y%m%d")
        codes=[f"{i:06d}.SZ" for i in range(1,1001)]
        code=codes[0]
        class Provider:
            source="official"
            def trade_open(self,_):return True
            def stocks(self):
                return [{"ts_code":c,"name":"Fixture "+c,"market":"主板" if c==code else "创业板","industry":"制造业","list_date":"20200101"} for c in codes]
            def daily(self,_):
                return [{"ts_code":c,"trade_date":ds,"close":10,"total_mv":100,"pb":0.5,"turnover_rate":1} for c in codes]
            def query(self,endpoint,params,fields):
                if endpoint=="trade_cal":
                    start=datetime.strptime(params["start_date"],"%Y%m%d").date()
                    end=datetime.strptime(params["end_date"],"%Y%m%d").date()
                    return [{"cal_date":(start+timedelta(days=i)).strftime("%Y%m%d"),"is_open":int(start+timedelta(days=i)==day)} for i in range((end-start).days+1)]
                if endpoint=="suspend_d":return []
                extra={"daily":{"open":10,"high":11,"low":9,"close":10,"vol":100,"amount":1000},
                    "adj_factor":{"adj_factor":1},"daily_basic":{"total_mv":100,"circ_mv":100,"total_share":10,"float_share":10,"free_share":10,"turnover_rate":1,"pb":0.5,"pe_ttm":5},
                    "stk_limit":{"up_limit":11,"down_limit":9}}[endpoint]
                return [{"ts_code":code,"trade_date":ds,**extra}]
        provider=Provider()
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{"DATABASE_URL":url,"ARCHIVE_STORAGE_DRIVER":"local","ARCHIVE_LOCAL_ROOT":tmp,
                     "PIPELINE_RUN_ID":str(uuid4()),"ARCHIVE_LOCAL_ALLOW_MAINTENANCE_APPLY":"false"}):
            run_id=os.environ["PIPELINE_RUN_ID"]
            with self.assertRaisesRegex(RuntimeError,"init-db disabled"):
                store.initialize(schema.read_text())
            store.ensure_price_history_tables()
            with store.connect() as conn:
                self.assertEqual(conn.execute("SELECT current_user AS role").fetchone()["role"],"screener_writer")
                self.assertFalse(conn.execute("SELECT has_schema_privilege(current_user,'public','CREATE') AS allowed").fetchone()["allowed"])
                for table in ("stocks","daily_prices","financial_reports","financial_fetch_log","job_runs","price_history","price_history_fetches","screen_results"):
                    for privilege in ("SELECT","INSERT","UPDATE","DELETE","TRUNCATE","REFERENCES","TRIGGER"):
                        expected = privilege in ("SELECT","INSERT","UPDATE") or (privilege=="DELETE" and table in ("daily_prices","price_history","price_history_fetches","screen_results"))
                        actual=conn.execute("SELECT has_table_privilege(current_user,%s,%s) AS allowed",("public."+table,privilege)).fetchone()["allowed"]
                        self.assertEqual(actual,expected,(table,privilege))
                self.assertFalse(conn.execute("SELECT has_database_privilege(current_user,current_database(),'TEMP') AS allowed").fetchone()["allowed"])
            with patch.object(cli.TushareClient,"from_environment",return_value=provider):
                self.assertTrue(cli.fetch_market(SimpleNamespace(date=day.isoformat())))
            self.assertEqual(store.quote_coverage(day)["quoted_stocks"],1000)
            self.assertEqual(market_run(None,run_id),(day,1000))
            self.assertTrue(store.has_successful_local_trigger(run_id))
            self.assertFalse(store.has_successful_local_trigger(str(uuid4())))
            cfg=StorageConfig.from_environment();s=s3_client(cfg);b=cfg.bucket
            self.assertEqual(b,LOCAL_BUCKET)
            archive_session(s,b,provider,day,1)
            publish_month(s,b,provider,day)
            pointer=month_root("daily_prices",day.year,day.month)+"/reader.manifest.json"
            doc=get_optional_json(s,b,pointer)
            self.assertEqual(doc["rows"],1)
            self.assertEqual(doc["dates"],[day.isoformat()])
            before=storage_state(s,b)
            archive_session(s,b,provider,day,1);publish_month(s,b,provider,day)
            self.assertEqual(storage_state(s,b),before)
            put_manifest(s,b,RAW_PREFIX+"/stock_basic/stock_basic_snapshot.json",{"status":"current_snapshot_not_historical","records":[{"ts_code":code}]})
            end=next(x for x in recent_closed_quarters(today) if x<day-timedelta(days=2))
            announced=end+timedelta(days=1)
            for kind in ("balancesheet","income","cashflow"):
                record=dict.fromkeys(FIELDS[kind].split(","),None)
                record.update(ts_code=code,report_type="1",comp_type="1",end_date=end.strftime("%Y%m%d"),ann_date=announced.strftime("%Y%m%d"),f_ann_date=announced.strftime("%Y%m%d"))
                record.update({"balancesheet":{"total_cur_assets":3000000,"total_liab":500000,"total_assets":3500000,"money_cap":2000000},"income":{"n_income":100000},"cashflow":{"n_cashflow_act":120000}}[kind])
                raw=gzip.compress((json.dumps(record)+"\n").encode(),mtime=0)
                sha=hashlib.sha256(raw).hexdigest()
                base=RAW_PREFIX+"/financial/"+kind+"/start=00000_count=050";key=base+".jsonl.gz"
                s.put_object(Bucket=b,Key=key,Body=raw,Metadata={"sha256":sha},ContentType="application/gzip")
                put_manifest(s,b,base+".manifest.json",{"format_version":1,"kind":kind,"start_index":0,"requested_codes":1,"codes_sha256":hashlib.sha256(json.dumps([code]).encode()).hexdigest(),"object_key":key,"status":"research_not_pit_certified","source":"Tushare official per-symbol","currency":"CNY","financial_amount_unit":"RMB yuan","compressed_bytes":len(raw),"sha256":sha,"rows":1,"captured_at_utc":datetime.now(timezone.utc).isoformat(),"missing_codes":[],"missing_codes_count":0})
            stats=import_archive(store,day,today)
            self.assertEqual(stats["archive_rows_inserted"],3)
            self.assertEqual(import_archive(store,day,today)["archive_rows_inserted"],0)
            cli.recalculate_ncav(SimpleNamespace(date=today.isoformat(),trigger_local_run_id=run_id))
            with store.connect() as conn:
                row=conn.execute("SELECT count(*) AS n FROM screen_results WHERE trade_date=%s",(day,)).fetchone()
                self.assertEqual(row["n"],1)
                self.assertEqual(conn.execute("SELECT ncav_yuan FROM screen_results WHERE ts_code=%s",(code,)).fetchone()["ncav_yuan"],2500000)
            with self.assertRaises(NotReady):retention_cutoffs(day,[day])
            with patch("sys.argv",["raw_maintenance","--apply"]):
                from value_screener.raw_maintenance import main
                with self.assertRaisesRegex(RuntimeError,"cleanup disabled"):main()

if __name__=="__main__":unittest.main()
