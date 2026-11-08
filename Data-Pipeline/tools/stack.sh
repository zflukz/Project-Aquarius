# Sourced by the check scripts: where the Docker stack is and how to reach it.
# The pipeline services run from docker/compose.*.yml with the profile `pipeline` (docker/README.md).
#   COMPOSE=compose.server.yml tools/phase_c_check.sh     # on the VM (default: compose.local.yml)
#   AQUARIUS_DOCKER_DIR=~/aquarius tools/...              # compose files somewhere else
# pwd -W = C:/... in Git Bash (docker.exe can't read /c/... with MSYS_NO_PATHCONV), plain pwd elsewhere
DOCKER_DIR="${AQUARIUS_DOCKER_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../docker" && { pwd -W 2>/dev/null || pwd; })}"
COMPOSE="${COMPOSE:-compose.local.yml}"
export MSYS_NO_PATHCONV=1                                  # Git Bash: keep container paths as they are

dc() { docker compose -f "$DOCKER_DIR/$COMPOSE" --profile pipeline "$@"; }
env_get() { grep -E "^$1=" "$DOCKER_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r'; }

DB_USER=aquarius
DB_NAME=aquarius
DB_PASS="$(env_get TSDB_PASSWORD)"; DB_PASS="${DB_PASS:-aquarius-admin}"
if [[ "$COMPOSE" == compose.server.yml ]]; then           # host network
  DB_HOSTPORT="127.0.0.1:$(p=$(env_get TSDB_PORT); echo "${p:-5432}")"
else
  DB_HOSTPORT="timescaledb:5432"
fi
kafka_internal() { if [[ "$COMPOSE" == compose.server.yml ]]; then echo 127.0.0.1:19092; else echo localhost:19092; fi; }
# psql inside the database container (socket, no password)
psql_in() { docker exec -i timescaledb psql -U "$DB_USER" "$@"; }
