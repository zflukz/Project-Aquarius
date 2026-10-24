#!/usr/bin/env bash
# Phase C done-checks (C1-C5) in one command. Needs `docker compose up -d` first.
#
#   tools/phase_c_check.sh
#
# 1. the real database: every migration applied?
# 2. a THROWAWAY database (aquarius_ctest): created empty, every migration
#    applied from zero (C1 "runs on an empty database"), then db/tests/phase_c_test.sql
#    checks grid, UTM, coverage (19 % by hand), index use (EXPLAIN) and the
#    survivor merge. The throwaway database is dropped at the end; real data is not touched.
set -uo pipefail
cd "$(dirname "$0")/.."
[[ -f .env ]] && set -a && . ./.env && set +a
U="${POSTGRES_USER:-aquarius}"; P="${POSTGRES_PASSWORD:-aquarius}"; DB="${POSTGRES_DB:-aquarius}"
TEST_DB=aquarius_ctest
psql_db() { local d="$1"; shift; docker exec -i aquarius-db psql -U "$U" -d "$d" -v ON_ERROR_STOP=1 -X "$@"; }
fail() { echo "FAIL  $*"; psql_db "$DB" -qc "DROP DATABASE IF EXISTS $TEST_DB" >/dev/null 2>&1; exit 1; }

docker compose ps --format '{{.Service}} {{.State}}' | grep -q '^db running' || fail "db is not running (docker compose up -d)"

echo "== 1. Migrations on the real database ($DB)"
docker compose build -q db-migrate || fail "build db-migrate image"
docker compose run --rm -T db-migrate python -m aquarius_ingest.migrate --status || fail "migrate --status"
if docker compose run --rm -T db-migrate python -m aquarius_ingest.migrate --status | grep -qE ' (pending|changed) '; then
  echo "WARN  some migrations are not applied yet: docker compose up -d --build (or: docker compose run --rm db-migrate)"
fi

echo
echo "== 2. Fresh database: create, migrate from zero, run the checks"
psql_db "$DB" -qc "DROP DATABASE IF EXISTS $TEST_DB" >/dev/null 2>&1
psql_db "$DB" -qc "CREATE DATABASE $TEST_DB" || fail "create $TEST_DB"
psql_db "$TEST_DB" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb; CREATE EXTENSION IF NOT EXISTS postgis;" \
  >/dev/null 2>&1 || fail "extensions in $TEST_DB"
docker compose run --rm -T -e PG_DSN="postgresql://$U:$P@db:5432/$TEST_DB" db-migrate || fail "migrations on an empty database"
echo "  (second run must change nothing:)"
docker compose run --rm -T -e PG_DSN="postgresql://$U:$P@db:5432/$TEST_DB" db-migrate | sed 's/^/  /'

echo
out=$(psql_db "$TEST_DB" -f - < db/tests/phase_c_test.sql 2>&1)
echo "$out" | grep -E "PASS|FAIL|ERROR|ALL PHASE C" | sed -E 's/^(psql:[^ ]+ )?(NOTICE|ERROR): +/  /'
psql_db "$DB" -qc "DROP DATABASE IF EXISTS $TEST_DB" >/dev/null 2>&1
echo
if echo "$out" | grep -q "ALL PHASE C CHECKS PASSED"; then
  echo "RESULT: Phase C checks passed ($(echo "$out" | grep -cE 'PASS C[0-9]') checks)"
else
  echo "RESULT: FAILED - full output:"; echo "$out" | tail -40; exit 1
fi
