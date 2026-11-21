#!/usr/bin/env bash
# bench_paths.sh — compare the two telemetry stacks on the same running sim, one after the other
#
#   MQTT  : PX4 MAVLink -> mavlink_mqtt.py -> Mosquitto -> Telegraf -> TimescaleDB (Fleet dashboard)
#   Kafka : PX4 uXRCE-DDS -> XRCE agent -> ROS 2 -> aquarius_bridge -> Kafka -> ingestion -> TimescaleDB
#
#   ./bench_paths.sh                     mqtt, kafka, both — each: aquarius path <p>, warm-up, measure
#   ./bench_paths.sh kafka mqtt          only these, in this order
#   NO_SWITCH=1 ./bench_paths.sh         measure what runs now (no restart), label = telemetry's TELEMETRY_PATH
#   WARMUP=60 DURATION=120 ./bench_paths.sh
#
# Measured per run over DURATION s (same numbers as Grafana "Path compare", plus CPU / RAM):
#   container  cpu_pct / mem_mb   docker stats, averaged (100 % = one core)
#   process    cpu_pct / rss_mb   inside telemetry from /proc: xrce agent, mqtt bridge, kafka bridge,
#                                  gz bridge, ground truth, web video
#   db:<stack> rows_per_s (per drone), lat_p50/p95/p99/max_ms (bridge sent -> row written), max_gap_ms
#   sim        rtf                Gazebo real-time factor (Kafka's 10 Hz is sim time -> fewer rows if < 1)
# Results are appended to $RESULTS (CSV, long format). The sim keeps running between runs (aquarius path
# restarts px4-sim + telemetry). For numbers under load fly during the window, e.g. in another terminal:
#   aquarius start coverage --size 30x20 --detach
# Fair comparison: same drones / model / HEADLESS for every run, nothing else running, RTF close to 1.
#
# Env (defaults): WARMUP=60 (s after the switch)  DURATION=60 (s)  SAMPLE=5 (s between docker stats)
#   RESULTS=./bench_paths.csv  AQUARIUS (the aquarius script, found next to this file / in ../scripts / PATH)
#   NO_SWITCH (1 = don't switch)  PX4_GZ_WORLD (from docker/.env, default kmutt)
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash: keep /world/..., /proc/... arguments

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
WARMUP=${WARMUP:-60}
DURATION=${DURATION:-60}
SAMPLE=${SAMPLE:-5}
RESULTS=${RESULTS:-./bench_paths.csv}
CONTAINERS="px4-sim telemetry mosquitto telegraf kafka ingestion imagestream kafka-ui timescaledb grafana"
STACK_MQTT="mosquitto telegraf"
STACK_KAFKA="kafka ingestion imagestream kafka-ui"

log() { echo "[bench] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }
calc() { awk "BEGIN { printf \"%.2f\", $* }"; }
running() { docker ps --format '{{.Names}}' | grep -qx "$1"; }
env_val() { grep -E "^$1=" "$SCRIPT_DIR/.env" 2>/dev/null | tail -n 1 | cut -d= -f2-; }

if [ -z "${AQUARIUS:-}" ]; then
    for a in "$SCRIPT_DIR/aquarius" "$SCRIPT_DIR/../scripts/aquarius" "$(command -v aquarius || true)"; do
        [ -n "$a" ] && [ -f "$a" ] && { AQUARIUS=$a; break; }
    done
fi
WORLD=${PX4_GZ_WORLD:-$(env_val PX4_GZ_WORLD)}; WORLD=${WORLD:-kmutt}
PATHS=("$@"); [ ${#PATHS[@]} -gt 0 ] || PATHS=(mqtt kafka both)
for p in "${PATHS[@]}"; do case "$p" in mqtt|kafka|both) ;; *) die "path must be mqtt, kafka or both: $p" ;; esac; done
[ -n "${NO_SWITCH:-}" ] || [ -n "${AQUARIUS:-}" ] || die "aquarius script not found (AQUARIUS=...)"
running px4-sim || die "px4-sim is not running (aquarius init)"
[ -f "$RESULTS" ] || echo "date,path,drones,model,item,metric,value" > "$RESULTS"

# --- telemetry processes: utime+stime ticks and RSS per group, read inside the container -----------
PROC_SNAPSHOT='
hz=$(getconf CLK_TCK)
for d in /proc/[0-9]*; do
    [ "${d#/proc/}" = "$$" ] && continue   # this shell: its command line contains every pattern
    c=$(tr "\0" " " < "$d/cmdline" 2>/dev/null) || continue
    case "$c" in
        *MicroXRCEAgent*)              g=xrce_agent ;;
        *mavlink_mqtt.py*)             g=mqtt_bridge ;;
        *lib/aquarius_bridge/bridge*)  g=kafka_bridge ;;
        *parameter_bridge*)            g=gz_bridge ;;
        *ground_truth.py*)             g=ground_truth ;;
        *web_video_server*)            g=web_video ;;
        *) continue ;;
    esac
    t=$(awk "{ sub(/^.*\\) /, \"\"); print \$12 + \$13 }" "$d/stat" 2>/dev/null) || continue
    r=$(awk "/^VmRSS:/ { print \$2 }" "$d/status" 2>/dev/null)
    echo "$g $t ${r:-0} $hz"
done'
proc_snapshot() { docker exec telemetry bash -c "$PROC_SNAPSHOT" 2>/dev/null | sort || true; }

sim_time() {   # Gazebo sim time, s
    docker exec px4-sim gz topic -e -t "/world/$WORLD/stats" -n 1 2>/dev/null | awk '
        /^sim_time/ { s = 1; next }
        s && /sec:/ && !/nsec/ { sec = $2 }
        s && /nsec:/ { nsec = $2 }
        s && /}/ { printf "%.3f\n", sec + nsec / 1e9; exit }'
}

mem_mb() {   # "123.4MiB / 7.6GiB" -> 123.4
    awk '{ v = $1 + 0; u = $1; gsub(/[0-9.]/, "", u)
           printf "%.1f", (u == "GiB") ? v * 1024 : (u == "KiB") ? v / 1024 : (u == "B") ? v / 1048576 : v }'
}

db_summary() {   # from to (UTC ISO) -> "stack rows_per_s p50 p95 p99 max gap" per stack
    docker exec -i timescaledb psql -q -At -F ' ' -U aquarius -d aquarius -v ON_ERROR_STOP=1 2>/dev/null <<SQL || true
WITH s AS (
  SELECT 'mqtt' AS path, drone AS drone, time AS sent, received_at FROM "position"
   WHERE received_at BETWEEN '$1' AND '$2' AND time > timestamptz '$1' - interval '1 minute'
  UNION ALL
  SELECT 'kafka', drone_id, sent_at, received_at FROM telemetry
   WHERE received_at BETWEEN '$1' AND '$2' AND sent_at IS NOT NULL
), g AS (
  SELECT *, EXTRACT(epoch FROM received_at - sent) * 1000 AS lat,
         EXTRACT(epoch FROM received_at - lag(received_at) OVER (PARTITION BY path, drone ORDER BY received_at)) * 1000 AS gap
    FROM s
)
SELECT path,
       round((count(*) / NULLIF(EXTRACT(epoch FROM max(received_at) - min(received_at)), 0) / count(DISTINCT drone))::numeric, 2),
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY lat)::numeric, 1),
       round(percentile_cont(0.95) WITHIN GROUP (ORDER BY lat)::numeric, 1),
       round(percentile_cont(0.99) WITHIN GROUP (ORDER BY lat)::numeric, 1),
       round(max(lat)::numeric, 1),
       round(max(gap)::numeric, 1)
  FROM g GROUP BY path ORDER BY path;
SQL
}

run_path() {
    local p=$1
    if [ -z "${NO_SWITCH:-}" ]; then
        log "=== $p: aquarius path $p ==="
        "$AQUARIUS" path "$p" >&2 || die "aquarius path $p failed"
        log "warm-up ${WARMUP}s (Kafka bridge / ingestion need ~20-40 s) ..."
        sleep "$WARMUP"
    else
        p=$(docker exec telemetry printenv TELEMETRY_PATH 2>/dev/null || echo both)
        log "=== $p (running stack, NO_SWITCH) ==="
    fi
    running telemetry || die "telemetry is not running"
    local drones model date
    drones=$(docker exec px4-sim printenv PX4_NUM_DRONES 2>/dev/null || echo 1)
    model=$(docker exec px4-sim printenv PX4_SIM_MODEL 2>/dev/null || echo ?)
    date=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    out() { echo "$date,$p,$drones,$model,$1,$2,$3" >> "$RESULTS"; }

    local present=() c
    for c in $CONTAINERS; do running "$c" && present+=("$c"); done
    log "measuring ${DURATION}s: ${present[*]}"

    local from to s0 s1 w0 w1 p0 p1
    from=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)
    s0=$(sim_time); w0=$(date +%s.%N); p0=$(proc_snapshot)

    declare -A cpu_sum=() mem_sum=(); local n=0 end=$(( $(date +%s) + DURATION )) name cpu mem
    while [ "$(date +%s)" -lt "$end" ]; do
        while IFS='|' read -r name cpu mem; do
            cpu_sum[$name]=$(calc "${cpu_sum[$name]:-0} + ${cpu%\%}")
            mem_sum[$name]=$(calc "${mem_sum[$name]:-0} + $(echo "$mem" | mem_mb)")
        done < <(docker stats --no-stream --format '{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}' "${present[@]}")
        n=$((n + 1))
        sleep "$SAMPLE"
    done

    s1=$(sim_time); w1=$(date +%s.%N); p1=$(proc_snapshot)
    to=$(date -u +%Y-%m-%dT%H:%M:%S.%NZ)

    # --- report
    printf '\n%-28s %9s %9s\n' "[$p] container" "cpu %" "mem MB" >&2
    local stack_cpu_mqtt=0 stack_cpu_kafka=0 stack_mem_mqtt=0 stack_mem_kafka=0
    for c in "${present[@]}"; do
        cpu=$(calc "${cpu_sum[$c]:-0} / $n"); mem=$(calc "${mem_sum[$c]:-0} / $n")
        printf '  %-26s %9s %9s\n' "$c" "$cpu" "$mem" >&2
        out "container:$c" cpu_pct "$cpu"; out "container:$c" mem_mb "$mem"
        case " $STACK_MQTT " in *" $c "*) stack_cpu_mqtt=$(calc "$stack_cpu_mqtt + $cpu"); stack_mem_mqtt=$(calc "$stack_mem_mqtt + $mem") ;; esac
        case " $STACK_KAFKA " in *" $c "*) stack_cpu_kafka=$(calc "$stack_cpu_kafka + $cpu"); stack_mem_kafka=$(calc "$stack_mem_kafka + $mem") ;; esac
    done
    printf '  %-26s %9s %9s\n' "= mosquitto+telegraf" "$stack_cpu_mqtt" "$stack_mem_mqtt" >&2
    printf '  %-26s %9s %9s\n' "= kafka+ingestion+ui+img" "$stack_cpu_kafka" "$stack_mem_kafka" >&2
    out stack:mqtt cpu_pct "$stack_cpu_mqtt"; out stack:mqtt mem_mb "$stack_mem_mqtt"
    out stack:kafka cpu_pct "$stack_cpu_kafka"; out stack:kafka mem_mb "$stack_mem_kafka"

    printf '%-28s %9s %9s\n' "[$p] telemetry process" "cpu %" "rss MB" >&2
    local wall; wall=$(calc "$w1 - $w0")
    # ticks per group at start / end (processes that restarted in between count from 0)
    join -a 2 -e 0 -o 0,1.2,2.2,2.3,2.4 \
        <(echo "$p0" | awk 'NF { t[$1] += $2 } END { for (g in t) print g, t[g] }' | sort) \
        <(echo "$p1" | awk 'NF { t[$1] += $2; r[$1] += $3; hz = $4 } END { for (g in t) print g, t[g], r[g], hz }' | sort) |
    while read -r g t0 t1 rss hz; do
        cpu=$(calc "($t1 - $t0) / $hz / $wall * 100"); mem=$(calc "$rss / 1024")
        printf '  %-26s %9s %9s\n' "$g" "$cpu" "$mem" >&2
        out "process:$g" cpu_pct "$cpu"; out "process:$g" rss_mb "$mem"
    done

    if [ -n "$s0" ] && [ -n "$s1" ]; then
        local rtf; rtf=$(calc "($s1 - $s0) / $wall")
        printf '  %-26s %9s\n' "Gazebo RTF" "$rtf" >&2
        out sim rtf "$rtf"
    fi

    printf '%-28s %9s %8s %8s %8s %8s %9s\n' "[$p] database" "rows/s" "p50 ms" "p95 ms" "p99 ms" "max ms" "max gap" >&2
    local st r50 r95 r99 rmax gap rate any=
    while read -r st rate r50 r95 r99 rmax gap; do
        [ -n "$st" ] || continue; any=1
        printf '  %-26s %9s %8s %8s %8s %8s %9s\n' "$st (per drone)" "$rate" "$r50" "$r95" "$r99" "$rmax" "$gap" >&2
        out "db:$st" rows_per_s "$rate"; out "db:$st" lat_p50_ms "$r50"; out "db:$st" lat_p95_ms "$r95"
        out "db:$st" lat_p99_ms "$r99"; out "db:$st" lat_max_ms "$rmax"; out "db:$st" max_gap_ms "$gap"
    done < <(db_summary "$from" "$to")
    [ -n "$any" ] || log "WARN: no rows with send / insert time in the window (aquarius path once, migration 0008?)"
    echo >&2
}

for p in "${PATHS[@]}"; do
    run_path "$p"
    [ -n "${NO_SWITCH:-}" ] && break
done
log "results appended to $RESULTS"
