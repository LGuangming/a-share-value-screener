-- CI-only synthetic rows, using the actual main-branch business schema.
INSERT INTO stocks(ts_code,name,industry,market,list_date)
SELECT lpad(i::text,6,'0')||'.SZ','fixture-'||i,'synthetic','主板','2010-01-01' FROM generate_series(1,5000) i;
INSERT INTO daily_prices(ts_code,trade_date,close,pb,turnover_rate,market_cap_yuan,raw)
SELECT lpad(((i-1)%5000+1)::text,6,'0')||'.SZ',date '2025-01-01'+((i-1)/5000)::int,
       10+(i%1000)/100.0,0.8,1.2,1000000000+i,
       jsonb_build_object('fixture',i,'payload',md5(i::text)||md5((i+1)::text)||md5((i+2)::text)||md5((i+3)::text)||md5((i+4)::text)||md5((i+5)::text)||md5((i+6)::text)||md5((i+7)::text)||md5((i+8)::text)||md5((i+9)::text)||md5((i+10)::text)||md5((i+11)::text)||md5((i+12)::text)||md5((i+13)::text)||md5((i+14)::text)||md5((i+15)::text))
FROM generate_series(1,:fixture_rows) i;
INSERT INTO price_history(ts_code,trade_date,close) SELECT ts_code,trade_date,close FROM daily_prices;
INSERT INTO price_history_fetches(trade_date,rows_saved) SELECT trade_date,count(*) FROM price_history GROUP BY trade_date;
INSERT INTO financial_reports(ts_code,kind,end_date,disclosed_at,report_type,metrics,raw)
SELECT ts_code,k,date '2025-09-30',date '2025-10-30','1','{"total_cur_assets":1000000000,"total_liab":100000000}'::jsonb,'{}'::jsonb
FROM stocks CROSS JOIN (VALUES('balancesheet'),('income'),('cashflow')) t(k);
INSERT INTO financial_fetch_log(ts_code,succeeded_at) SELECT ts_code,now() FROM stocks;
INSERT INTO job_runs(run_id,job_type,trade_date,status) VALUES ('00000000-0000-0000-0000-000000000001','fixture','2025-01-01','success');
INSERT INTO screen_results(trade_date,ts_code,run_id,strategy,price,pb,is_candidate,status,signal,assessment)
SELECT '2025-01-01',ts_code,'00000000-0000-0000-0000-000000000001','netnet_v1',10,0.8,true,'fixture','fixture','fixture' FROM stocks;
ANALYZE;
