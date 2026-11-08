#!/usr/bin/env bash
# Database done-checks (Phase C: C1-C5, Phase D: dashboard helpers) in one command. Needs the stack with the pipeline profile (aquarius pipeline up).
#
#   tools/phase_c_check.sh
#
# 1. the real database: every migration applied?
# 2. a THROWAWAY database (aquarius_ctest): created empty, every migration
#    applied from zero (C1 "runs on an empty database"), then every db/tests/phase_*_test.sql
#    checks grid, UTM, coverage (19 % by hand), index use (EXPLAIN) and the
#    survivor merge. The throwaway database is dropped at the end; real data is not touched.
set -uo pipefail
cd "$(dirname "$0")/.."                       # Data-Pipeline/
. tools/stack.sh
U="$DB_USER"; P="$DB_PASS"; DB="$DB_NAME"
DB_DIR="$DOCKER_DIR/db"
TEST_DB=aquarius_ctest
psql_db() { local d="$1"; shift; psql_in -d "$d" -v ON_ERROR_STOP=1 -X "$@"; }
fail() { echo "FAIL  $*"; psql_db "$DB" -qc "DROP DATABASE IF EXISTS $TEST_DB" >/dev/null 2>&1; exit 1; }

dc ps --format '{{.Service}} {{.State}}' | grep -q '^timescaledb running' || fail "timescaledb is not running (aquarius pipeline up)"

echo "== 1. Migrations on the real database ($DB)"
dc build -q db-migrate || fail "build db-migrate image"
dc run --rm -T db-migrate python -m aquarius_ingest.migrate --status || fail "migrate --status"
if dc run --rm -T db-migrate python -m aquarius_ingest.migrate --status | grep -qE ' (pending|changed) '; then
  echo "WARN  some migrations are not applied yet: aquarius pipeline up (or: docker compose -f $DOCKER_DIR/$COMPOSE run --rm db-migrate)"
fi

echo
echo "== 2. Fresh database: create, migrate from zero, run the checks"
psql_db "$DB" -qc "DROP DATABASE IF EXISTS $TEST_DB" >/dev/null 2>&1
psql_db "$DB" -qc "CREATE DATABASE $TEST_DB" || fail "create $TEST_DB"
psql_db "$TEST_DB" -qc "CREATE EXTENSION IF NOT EXISTS timescaledb; CREATE EXTENSION IF NOT EXISTS postgis;" \
  >/dev/null 2>&1 || fail "extensions in $TEST_DB"
dc run --rm -T -e PG_DSN="postgresql://$U:$P@$DB_HOSTPORT/$TEST_DB" db-migrate || fail "migrations on an empty database"
echo "  (second run must change nothing:)"
dc run --rm -T -e PG_DSN="postgresql://$U:$P@$DB_HOSTPORT/$TEST_DB" db-migrate | sed 's/^/  /'

echo
out=""; ok_files=0; n_files=0
for f in "$DB_DIR"/tests/phase_*_test.sql; do        # phase_c_test.sql, phase_d_test.sql, ...
  n_files=$((n_files+1))
  o=$(psql_db "$TEST_DB" -f - < "$f" 2>&1)
  echo "$o" | grep -E "PASS|FAIL|ERROR" | sed -E 's/^(psql:[^ ]+ )?(NOTICE|ERROR): +/  /'
  echo "$o" | grep -q "CHECKS PASSED" && ok_files=$((ok_files+1)) || { out="$out
--- $f
$o"; break; }
done
if [[ $ok_files -eq $n_files ]]; then
  # every Grafana panel query must run on the test data (phase_d_test.sql's mission)
  o=$(PYTHONIOENCODING=utf-8 python3 tools/dashboard_sql.py d0000000-0000-4000-8000-000000000001 | psql_db "$TEST_DB" -q -o /dev/null -f - 2>&1)
  if echo "$o" | grep -q "DASHBOARD QUERIES OK"; then
    echo "  PASS D $(echo "$o" | grep -o 'DASHBOARD QUERIES OK ([0-9]*)' | grep -o '[0-9]*') dashboard queries run without error"
  else
    ok_files=-1; out="--- dashboard queries
$(echo "$o" | tail -15)"
  fi
fi
psql_db "$DB" -qc "DROP DATABASE IF EXISTS $TEST_DB" >/dev/null 2>&1
echo
if [[ $ok_files -eq $n_files ]]; then
  echo "RESULT: database checks passed ($n_files test files)"
else
  echo "RESULT: FAILED - output of the failing file:"; echo "$out" | tail -40; exit 1
fi
