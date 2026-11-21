-- 0011 received_at on every Telegraf (MQTT stack) table, for Grafana "Path compare" (latency = received_at - time).
-- New Telegraf tables get it from the Telegraf create template (compose telegraf config); tables created by an older
-- Telegraf config did not, and only `aquarius path` added it -> Path compare failed with
-- 'column "received_at" does not exist'. Idempotent: Telegraf tables = public tables with columns fleet + drone.
-- On an empty database (before Telegraf wrote anything) there is nothing to do.
DO $$
DECLARE t record;
BEGIN
  FOR t IN SELECT tb.table_name FROM information_schema.tables tb
            WHERE tb.table_schema = 'public' AND tb.table_type = 'BASE TABLE'
              AND EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema = 'public'
                          AND c.table_name = tb.table_name AND c.column_name = 'fleet')
              AND EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema = 'public'
                          AND c.table_name = tb.table_name AND c.column_name = 'drone')
              AND NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema = 'public'
                          AND c.table_name = tb.table_name AND c.column_name = 'received_at')
  LOOP
    EXECUTE format('ALTER TABLE public.%I ADD COLUMN received_at timestamptz', t.table_name);
    EXECUTE format('ALTER TABLE public.%I ALTER COLUMN received_at SET DEFAULT clock_timestamp()', t.table_name);
    RAISE NOTICE 'received_at added to %', t.table_name;
  END LOOP;
END $$;
