#!/usr/bin/env bash
# bench_sim.sh — compare simulator performance on the VM: native PX4 + Gazebo vs Docker compose
#
#   ./bench_sim.sh both          # native, then docker, then a comparison table  (default)
#   ./bench_sim.sh native        # PX4 built in $PX4_DIR, gz-harmonic installed on the VM
#   ./bench_sim.sh docker        # px4-sim from $COMPOSE_FILE (pulled image)
#   DRONES=3 DURATION=120 ./bench_sim.sh both
#
# Same workload in both modes: N PX4 SITL instances in one Gazebo world, idle on the ground,
# no GUI / QGC by default. Measured per run:
#   boot_s   start -> every instance finished rcS ("Startup script returned successfully")
#   ready_s  start -> every instance has a valid home position (GPS + EKF ok, ready to arm)
#   rtf      Gazebo real-time factor over DURATION s = d(sim time) / d(wall time)  (1.0 = real time)
#   cpu_%    CPU of all px4 + gz sim processes over the same window (100 % = one core)
#   rss_mb   their memory at the end
# Results are appended to $RESULTS (CSV). Run on an otherwise idle VM, both modes on the same VM.
#
# Env (defaults):
#   DRONES=1  WORLD=baylands  MODEL=gz_x500  AUTOSTART=4001  SPACING=3
#   WARMUP=20 (s after ready, before measuring)  DURATION=60 (s measured)  HEADLESS=1 (empty = GUI)
#   PX4_DIR=~/PX4-Autopilot   COMPOSE_FILE=<this dir>/compose.server.yml   RESULTS=./bench_results.csv
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: don't rewrite /px4/..., /world/... arguments

MODE=${1:-both}
DRONES=${DRONES:-1}
WORLD=${WORLD:-baylands}
MODEL=${MODEL:-gz_x500}
AUTOSTART=${AUTOSTART:-4001}
SPACING=${SPACING:-3}
WARMUP=${WARMUP:-20}
DURATION=${DURATION:-60}
HEADLESS=${HEADLESS-1}
PX4_DIR=${PX4_DIR:-$HOME/PX4-Autopilot}
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
COMPOSE_FILE=${COMPOSE_FILE:-$SCRIPT_DIR/compose.server.yml}
RESULTS=${RESULTS:-./bench_results.csv}
CONTAINER=px4-sim
NATIVE_LOG=/tmp/bench_px4
BOOT_TIMEOUT=300
SIM_PATTERN='bin/px4 -i|gz sim'   # px4 daemons + gz server (+ gui when HEADLESS is empty)

log() { echo "[bench] $*" >&2; }
die() { log "ERROR: $*"; exit 1; }
now() { date +%s.%N; }
calc() { awk "BEGIN { printf \"%.2f\", $* }"; }

# --- process accounting via /proc (works for native and for Docker on a Linux host) ---------------
sim_pids() { pgrep -f -- "$SIM_PATTERN" 2>/dev/null | tr '\n' ' ' || true; }
cpu_ticks() {   # sum of utime+stime of the given pids (fields after the ')' of /proc/pid/stat)
    local t=0 p
    for p in $1; do
        [ -r "/proc/$p/stat" ] || continue
        t=$((t + $(awk '{ sub(/^.*\) /, ""); print $12 + $13 }' "/proc/$p/stat")))
    done
    echo "$t"
}
rss_mb() {
    local kb=0 p
    for p in $1; do
        [ -r "/proc/$p/status" ] || continue
        kb=$((kb + $(awk '/^VmRSS:/ { print $2 }' "/proc/$p/status")))
    done
    calc "$kb / 1024"
}

# --- mode-specific helpers ---------------------------------------------------------------------
gz_cmd() {   # run the gz CLI where the simulator runs
    if [ "$CUR" = docker ]; then docker exec "$CONTAINER" gz "$@"; else gz "$@"; fi
}
sim_time() {   # current Gazebo sim time in seconds
    gz_cmd topic -e -t "/world/$WORLD/stats" -n 1 2>/dev/null | awk '
        /^sim_time/ { s = 1; next }
        s && /sec:/ && !/nsec/ { sec = $2 }
        s && /nsec:/ { nsec = $2 }
        s && /}/ { printf "%.3f\n", sec + nsec / 1e9; exit }'
}
count_log() {   # how many instances printed $1
    if [ "$CUR" = docker ]; then
        docker logs --since "$START_ISO" "$CONTAINER" 2>&1 | grep -c -- "$1" || true
    else
        cat "$NATIVE_LOG"/px4-*.log 2>/dev/null | grep -c -- "$1" || true
    fi
}
home_ok() {   # true if PX4 instance $1 has a valid home position
    local l=build/px4_sitl_default/bin/px4-listener
    if [ "$CUR" = docker ]; then docker exec "$CONTAINER" "/px4/$l" --instance "$1" home_position -n 1
    else "$PX4_DIR/$l" --instance "$1" home_position -n 1; fi 2>/dev/null | grep -q "valid_hpos: True"
}
wait_home() {
    local deadline=$(( $(date +%s) + BOOT_TIMEOUT )) i
    for i in $(seq 0 $((DRONES - 1))); do
        until home_ok "$i"; do
            [ "$(date +%s)" -lt "$deadline" ] || return 1
            sleep 1
        done
    done
}
px4_version() {
    if [ "$CUR" = docker ]; then docker exec "$CONTAINER" git -C /px4 describe --always --tags 2>/dev/null
    else git -C "$PX4_DIR" describe --always --tags 2>/dev/null; fi || echo unknown
}

stop_native() {
    pkill -9 -f "$PX4_DIR/build/px4_sitl_default/bin/px4" 2>/dev/null || true
    pkill -9 -f "gz sim" 2>/dev/null || true
    rm -f /tmp/px4_lock-* /tmp/px4-sock-*
}
stop_docker() {
    docker compose -f "$COMPOSE_FILE" rm -sf "$CONTAINER" >/dev/null 2>&1 || true
}

start_native() {
    local bin="$PX4_DIR/build/px4_sitl_default/bin/px4" i
    [ -x "$bin" ] || die "native PX4 not built: $bin (cd $PX4_DIR && make px4_sitl_default)"
    command -v gz >/dev/null || die "gz not found (install gz-harmonic)"
    [ -n "$HEADLESS" ] || [ -n "${DISPLAY:-}" ] || die "HEADLESS is empty but no DISPLAY for the Gazebo GUI"
    stop_native
    rm -rf "$NATIVE_LOG"; mkdir -p "$NATIVE_LOG"
    # PX4's own scripts read these; the same values the px4-sim entrypoint uses
    export PX4_SIM_MODEL=$MODEL PX4_SYS_AUTOSTART=$AUTOSTART PX4_GZ_WORLD=$WORLD \
           HEADLESS PX4_GZ_NO_FOLLOW=1
    for i in $(seq 0 $((DRONES - 1))); do
        (cd "$PX4_DIR" && PX4_GZ_MODEL_POSE="0,$((i * SPACING))" exec "$bin" -i "$i" -d) \
            > "$NATIVE_LOG/px4-$i.log" 2>&1 &
        disown
        # instance i must find the world before i+1 starts, else a second gz server appears
        wait_count "Startup script returned successfully" $((i + 1)) || die "drone$i did not boot"
    done
}
start_docker() {
    command -v docker >/dev/null || die "docker not found"
    [ -f "$COMPOSE_FILE" ] || die "compose file not found: $COMPOSE_FILE"
    stop_docker
    # compose.local.yml points CONTROL_HOST at the drone-control container: start it (it only
    # idles) or px4-sim waits minutes for that name to resolve
    local svc="$CONTAINER"
    docker compose -f "$COMPOSE_FILE" config --services 2>/dev/null | grep -qx drone-control \
        && svc="$svc drone-control"
    # shell env overrides .env; CONTROL_HOST only has to resolve (MAVLink is not part of the test)
    # shellcheck disable=SC2086
    CONTROL_HOST=${CONTROL_HOST:-127.0.0.1} PX4_NUM_DRONES=$DRONES PX4_GZ_WORLD=$WORLD \
    PX4_SIM_MODEL=$MODEL PX4_SYS_AUTOSTART=$AUTOSTART DRONE_SPACING=$SPACING \
    HEADLESS=$HEADLESS QGC=0 GZ_FOLLOW= AUTO_RUN= \
        docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-deps $svc >/dev/null 2>&1
}

wait_count() {   # wait until $1 appears $2 times in the PX4 logs
    local deadline=$(( $(date +%s) + BOOT_TIMEOUT ))
    while [ "$(count_log "$1")" -lt "$2" ]; do
        [ "$(date +%s)" -lt "$deadline" ] || return 1
        sleep 0.5
    done
}

# --- one benchmark run -------------------------------------------------------------------------
run_mode() {
    CUR=$1
    log "=== $CUR: $DRONES x $MODEL in '$WORLD', HEADLESS=${HEADLESS:-<gui>}, measure ${DURATION}s ==="
    stop_native; stop_docker   # never measure both at once
    sleep 2
    [ -z "$(sim_pids)" ] || die "simulator processes still running: $(sim_pids)"

    START_ISO=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    local t0 t_boot t_ready
    t0=$(now)
    "start_$CUR"
    wait_count "Startup script returned successfully" "$DRONES" || die "$CUR: not all drones booted in ${BOOT_TIMEOUT}s"
    t_boot=$(now)
    wait_home || log "WARN: not all drones got a valid home position"
    t_ready=$(now)
    log "boot $(calc "$t_boot - $t0") s, ready $(calc "$t_ready - $t0") s, warm-up ${WARMUP}s ..."
    sleep "$WARMUP"

    local pids s0 w0 c0 s1 w1 c1 cpu rss rtf hz docker_cpu=""
    pids=$(sim_pids)
    hz=$(getconf CLK_TCK)
    if [ -z "$pids" ] && [ "$CUR" = docker ]; then
        # Docker Desktop (Windows / macOS): container processes are not visible -> docker stats
        log "no host /proc view of the container, using docker stats"
        docker_cpu=1
    fi
    s0=$(sim_time); w0=$(now); c0=$(cpu_ticks "$pids")
    if [ -n "$docker_cpu" ]; then
        local sum=0 n=0 v end=$(( $(date +%s) + DURATION ))
        while [ "$(date +%s)" -lt "$end" ]; do
            v=$(docker stats --no-stream --format '{{.CPUPerc}}' "$CONTAINER" | tr -d '%')
            sum=$(calc "$sum + $v"); n=$((n + 1))
        done
        cpu=$(calc "$sum / $n")
        # whole container (also Xvfb / x11vnc) -> slightly more than the /proc numbers
        rss=$(docker stats --no-stream --format '{{.MemUsage}}' "$CONTAINER" | awk '{
            v = $1 + 0; u = $1; gsub(/[0-9.]/, "", u)
            printf "%.2f", (u == "GiB") ? v * 1024 : (u == "KiB") ? v / 1024 : v }')
    else
        sleep "$DURATION"
        c1=$(cpu_ticks "$pids")
        cpu=$(calc "($c1 - $c0) / $hz / $DURATION * 100")
        rss=$(rss_mb "$pids")
    fi
    w1=$(now); s1=$(sim_time)
    [ -n "$s0" ] && [ -n "$s1" ] || die "$CUR: could not read /world/$WORLD/stats"
    rtf=$(awk "BEGIN { printf \"%.3f\", ($s1 - $s0) / ($w1 - $w0) }")

    local ver; ver=$(px4_version)
    "stop_$CUR"

    local row
    row="$(date -u +%FT%TZ),$CUR,$DRONES,$MODEL,$WORLD,${HEADLESS:-gui},$(calc "$t_boot - $t0"),$(calc "$t_ready - $t0"),$rtf,$cpu,$rss,$(nproc),$ver"
    [ -f "$RESULTS" ] || echo "time,mode,drones,model,world,headless,boot_s,ready_s,rtf,cpu_pct,rss_mb,vcpu,px4" > "$RESULTS"
    echo "$row" >> "$RESULTS"
    echo "$row"
}

# --- main ----------------------------------------------------------------------------------------
case "$MODE" in native|docker|both) ;; *) die "usage: $0 [native|docker|both]";; esac
trap 'stop_native; stop_docker' INT TERM
log "host: $(nproc) vCPU, $(free -m 2>/dev/null | awk '/Mem:/ {print $2 " MB RAM"}'), load $(cut -d' ' -f1-3 /proc/loadavg 2>/dev/null)"

ROWS=()
for m in native docker; do
    [ "$MODE" = both ] || [ "$MODE" = "$m" ] || continue
    row=$(run_mode "$m") || exit 1
    ROWS+=("$row")
done

echo
printf '%-7s %6s %7s %8s %7s %8s %9s  %s\n' mode drones boot_s ready_s rtf cpu_% rss_mb px4
for r in "${ROWS[@]}"; do
    IFS=, read -r _ mode drones _ _ _ boot ready rtf cpu rss _ ver <<< "$r"
    printf '%-7s %6s %7s %8s %7s %8s %9s  %s\n' "$mode" "$drones" "$boot" "$ready" "$rtf" "$cpu" "$rss" "$ver"
done
if [ "${#ROWS[@]}" -eq 2 ]; then
    IFS=, read -r _ _ _ _ _ _ _ _ rtf_n cpu_n _ <<< "${ROWS[0]}"
    IFS=, read -r _ _ _ _ _ _ _ _ rtf_d cpu_d _ <<< "${ROWS[1]}"
    echo
    awk -v rn="$rtf_n" -v rd="$rtf_d" -v cn="$cpu_n" -v cd="$cpu_d" 'BEGIN {
        printf "RTF  docker vs native: %+.1f %%  (higher = closer to real time)\n", (rd - rn) / rn * 100
        printf "CPU  docker vs native: %+.1f %%  (lower = cheaper)\n", (cd - cn) / cn * 100
        best = (rn - rd > 0.03) ? "native" : (rd - rn > 0.03) ? "docker" : "same (RTF within 0.03)"
        print "better RTF: " best
    }'
fi
log "results appended to $RESULTS"
