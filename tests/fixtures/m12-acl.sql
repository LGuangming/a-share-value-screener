-- Disposable M12-like CI roles only. Never apply to an existing VPS database.
DO $$
BEGIN
  IF current_database() NOT IN ('ashare_test','ashare_pipeline_test')
     OR current_user <> 'fixture_admin' THEN
    RAISE EXCEPTION 'Fixture ACL script requires isolated fixture database and owner';
  END IF;
END
$$;
CREATE ROLE screener_reader LOGIN PASSWORD 'fixture_only';
CREATE ROLE screener_writer LOGIN PASSWORD 'fixture_only';
DO $$
BEGIN
  EXECUTE format('REVOKE ALL ON DATABASE %I FROM PUBLIC',current_database());
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO screener_reader,screener_writer',current_database());
END
$$;
REVOKE ALL ON SCHEMA public FROM PUBLIC,screener_reader,screener_writer;
GRANT USAGE ON SCHEMA public TO screener_reader,screener_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO screener_reader,screener_writer;
GRANT INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO screener_writer;
GRANT DELETE ON public.daily_prices,public.price_history,public.price_history_fetches,public.screen_results TO screener_writer;
