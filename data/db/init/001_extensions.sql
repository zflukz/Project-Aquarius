-- Runs once, on the first start of an empty database (Docker entrypoint).
-- Only the extensions live here; every table, index and function is created by
-- the migrations in db/migrations/, applied by the `db-migrate` service.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS postgis;
