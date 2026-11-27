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
            # Real migrated-schema diagnosis: all connections default to read-only;
            # compare business table contents before/after, not just row counts.
            from contextlib import redirect_stdout
            from io import StringIO
            from value_screener import financial_gap_diagnosis
            from psycopg.conninfo import make_conninfo
            def snapshot():
                with store.connect() as conn:
                    return {table:conn.execute(
                        "SELECT md5(coalesce(string_agg(to_jsonb(t)::text,'|' ORDER BY to_jsonb(t)::text),'')) AS hash FROM "+table+" t"
                    ).fetchone()["hash"] for table in ("stocks","daily_prices","financial_reports",
                        "financial_fetch_log","job_runs","price_history","price_history_fetches","screen_results")}
            before=snapshot()
            archive_before=storage_state(s,b)
            with Store(make_conninfo(url,options="-c default_transaction_read_only=on")).connect() as conn:
                self.assertEqual(conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"],"on")
            output=StringIO()
            with redirect_stdout(output):
                financial_gap_diagnosis.run()
            summary=json.loads(next(line.split(" ",1)[1] for line in output.getvalue().splitlines()
                if line.startswith("FIN_DIAG_SUMMARY ")))
            self.assertEqual((summary["pending"],summary["data_gap"],summary["database_writes"],summary["tushare_calls"]),(0,0,0,0))
            self.assertEqual(snapshot(),before)
            self.assertEqual(storage_state(s,b),archive_before)
            with self.assertRaises(NotReady):retention_cutoffs(day,[day])
            with patch("sys.argv",["raw_maintenance","--apply"]):
                from value_screener.raw_maintenance import main
                with self.assertRaisesRegex(RuntimeError,"cleanup disabled"):main()


@unittest.skipUnless(os.getenv("TEST_PIPELINE_DATABASE_URL"),"isolated integration dependencies not configured")
class BT04ParquetIntegration(unittest.TestCase):
    def test_manifest_adapter_shards_resume_and_global_execution(self):
        import duckdb
        from contextlib import redirect_stdout
        from io import StringIO
        from value_screener import bt04_research as bt
        from value_screener.technical_strategies import TREND
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            market,security,scratch,out,strict=(root/x for x in ("market","security","scratch","out","strict"))
            for p in (market,security,scratch,out,strict):p.mkdir()
            sessions=[]
            d=date(2025,1,1)
            while len(sessions)<135:
                if d.weekday()<5:sessions.append(d)
                d+=timedelta(days=1)
            # Explicit synthetic calendar, not a real exchange calendar.
            calendar=root/"calendar.json"
            calendar.write_text(json.dumps({"from":sessions[0].isoformat(),"to":sessions[-1].isoformat(),
                                          "sessions":[d.isoformat() for d in sessions]}))
            db=duckdb.connect()
            def parquet(folder,name,table):
                p=folder/(name+".tmp.parquet")
                db.execute("COPY "+table+" TO ? (FORMAT PARQUET,COMPRESSION ZSTD)",[str(p)])
                h=bt.sha256(p);final=folder/(name+"-"+h+".parquet");p.rename(final)
                return {"file":final.name,"sha256":h,"bytes":final.stat().st_size}
            try:
                for rel,days in bt.expected(sessions).items():
                    m=market/rel;s=security/rel;m.mkdir(parents=True);s.mkdir(parents=True)
                    db.execute("""CREATE OR REPLACE TABLE m(ts_code VARCHAR,trade_date DATE,
                       open DOUBLE,high DOUBLE,low DOUBLE,close DOUBLE,vol DOUBLE,amount DOUBLE,adj_factor DOUBLE,
                       up_limit DOUBLE,down_limit DOUBLE,factor_valid BOOLEAN,limits_valid BOOLEAN,ohlcv_valid BOOLEAN,
                       suspend_event_count BIGINT,st_normal_evidence BOOLEAN,st_ann_date DATE)""")
                    db.execute("""CREATE OR REPLACE TABLE s(ts_code VARCHAR,trade_date DATE,listing_evidence VARCHAR,
                       historical_st_type VARCHAR,st_evidence VARCHAR,st_certified BOOLEAN)""")
                    for day in days:
                        i=sessions.index(day)
                        price=100+i*.2+(1.2 if i in (124,130) else 0)
                        for code in ("000001.SZ","000002.SZ"):
                            db.execute("INSERT INTO m VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                [code,day,price,price*1.005,price*.995,price,1000,60000,1,price*1.1,price*.9,
                                 True,True,True,0,True,date(2024,12,31)])
                            db.execute("INSERT INTO s VALUES (?,?,?,?,?,?)",[code,day,
                                "within_snapshot_listing_dates_not_certified","NORMAL","historical_merged_not_certified",False])
                    db.execute("CREATE OR REPLACE TABLE events(ts_code VARCHAR,trade_date DATE)")
                    mp=parquet(m,"market","m");ep=parquet(m,"suspension_events","events")
                    sp=parquet(s,"security","s")
                    n=len(days)*2
                    (m/"manifest.json").write_text(json.dumps(dict(version=1,year=days[0].year,month=days[0].month,
                        status="research_data_not_backtest_ready",outputs={"market":mp,"suspension_events":ep},
                        quality={"rows":n,"suspension_event_rows":0})))
                    (s/"manifest.json").write_text(json.dumps(dict(version=1,status="security_evidence_not_certified",
                        inputs={"market_sha256":mp["sha256"]},quality={"rows":n},**sp)))
            finally:db.close()
            a=SimpleNamespace(market=market,security=security,scratch=scratch,output=out,calendar=calendar,
                warmup=sessions[0],start=sessions[120],end=sessions[-1],shards=2,shard=None,config=None,
                provisional=True,execute_start=sessions[120],execute_end=sessions[-1],
                holding=5,stop=.05,max_rows=1000)
            with redirect_stdout(StringIO()):bt.run(a)
            summary=json.loads((out/"summary.json").read_text())
            self.assertEqual(summary["rows"],270)
            self.assertGreaterEqual(summary["signal_counts"][TREND],2)
            result=json.loads(next(out.glob("execution-*.json")).read_text())
            self.assertFalse(result["formal_backtest_ready"])
            self.assertEqual(result["rows"],30)
            self.assertTrue(result["results"][TREND]["fills"])
            self.assertEqual(result["results"][TREND]["fills"][0]["ts_code"],"000001.SZ")
            saved=(out/"shard-000.json").read_bytes()
            with redirect_stdout(StringIO()):bt.run(a)
            self.assertEqual(saved,(out/"shard-000.json").read_bytes())
            # BT05 consumes the same immutable receipts, not the BT04 capped bars.
            from value_screener import bt05_research as bt05
            out5=root/"bt05";out5.mkdir()
            config=root/"execution-config.json"
            config.write_text(json.dumps({"holding_sessions":5,"stop_loss_rate":.05}))
            b=SimpleNamespace(market=market,security=security,signals=out,scratch=scratch,
                output=out5,calendar=calendar,start=sessions[120],end=sessions[-1],
                config=config,fees=None)
            with redirect_stdout(StringIO()):report=bt05.run(b)
            self.assertEqual(report["sessions"],15)
            self.assertFalse(report["formal_backtest_ready"])
            self.assertGreater(report["results"][TREND]["metrics"]["closed_trades"],0)
            for k in result["results"]:
                old=result["results"][k]["metrics"]
                new=report["results"][k]["metrics"]
                for metric in ("total_return","max_drawdown"):
                    self.assertAlmostEqual(old[metric],new[metric])
                self.assertEqual(old["closed_trades"],new["closed_trades"])
            checkpoints=sorted(out5.glob("month-*.json"))
            self.assertGreater(len(checkpoints),1)
            saved5={p.name:p.read_bytes() for p in checkpoints}
            with redirect_stdout(StringIO()):self.assertEqual(bt05.run(b),report)
            self.assertEqual(saved5,{p.name:p.read_bytes() for p in checkpoints})
            # Replay only the last month from the prior committed portfolio state.
            checkpoints[-1].unlink();(out5/"summary.json").unlink()
            with redirect_stdout(StringIO()):self.assertEqual(bt05.run(b),report)
            self.assertEqual(saved5,{p.name:p.read_bytes() for p in checkpoints})
            checkpoint=json.loads(checkpoints[-1].read_text())
            checkpoint["body"]["rows_seen"]+=1
            checkpoints[-1].write_text(json.dumps(checkpoint))
            with self.assertRaisesRegex(ValueError,"Checkpoint hash"):
                with redirect_stdout(StringIO()):bt05.run(b)
            from value_screener import bt05_grid
            grid=root/"grid";grid.mkdir()
            action_file=root/"actions.json"
            action_file.write_text(json.dumps({"status":"OPERATOR_REVIEWED_RESEARCH_NOT_CERTIFIED",
                "from":sessions[0].isoformat(),"through":sessions[-1].isoformat(),"events":[]}))
            g=SimpleNamespace(market=market,security=security,signals=out,scratch=scratch,
                output=grid,calendar=calendar,train_start=sessions[120],train_end=sessions[129],
                test_start=sessions[130],test_end=sessions[-1],config=None,fees=None,actions=action_file,
                holdings=[1,2],stops=[None],min_trades=1,max_drawdown=.5)
            with redirect_stdout(StringIO()):gr=bt05_grid.run(g)
            self.assertFalse(gr["formal_backtest_ready"])
            self.assertIsNotNone(gr["selected_results"][TREND])
            self.assertEqual(gr["selected_results"][TREND]["held_out_metrics"]["completed_sessions"],5)
            selection=json.loads((grid/"selection.json").read_text())
            self.assertEqual(len(selection["training"]),2)
            self.assertEqual(gr["selected_results"][TREND]["id"],selection["selected"][TREND])
            self.assertIsNone(gr["selected_results"][bt05.REVERSAL])
            with redirect_stdout(StringIO()):self.assertEqual(bt05_grid.run(g),gr)
            g.test_start=g.train_end
            with self.assertRaisesRegex(ValueError,"disjoint"):bt05_grid.run(g)
            a.max_rows=1
            with self.assertRaisesRegex(ValueError,"cap is"):
                with redirect_stdout(StringIO()):bt.run(a)
            a.output=strict;a.provisional=False;a.execute_start=a.execute_end=None
            with redirect_stdout(StringIO()):bt.run(a)
            self.assertEqual(sum(json.loads((strict/"summary.json").read_text())["signal_counts"].values()),0)
            a.output=out;a.provisional=True
            (out/"shard-000.jsonl").write_text("corrupt")
            with self.assertRaisesRegex(ValueError,"corrupted"):
                with redirect_stdout(StringIO()):bt.run(a)
            a.output=strict;a.provisional=False
            sp=next(security.rglob("security-*.parquet"))
            sp.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError,"checksum"):
                with redirect_stdout(StringIO()):bt.run(a)

if __name__=="__main__":unittest.main()
