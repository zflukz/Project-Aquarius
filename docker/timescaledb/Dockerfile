# aquarius-timescaledb — PostgreSQL 17 + PostGIS 3.5 + TimescaleDB, small (~200 MB download).
# Replaces timescale/timescaledb-ha (1.5 GB download / 6.8 GB on disk, mostly tools we don't use).
#
# Base = official PostGIS Alpine image. TimescaleDB is copied from the official TimescaleDB Alpine image:
# its .so files only need musl libc + symbols of the postgres binary (same major 17), so they load as-is.
# Only the files of one TimescaleDB version: .so files, control file, install script (no upgrade scripts,
# which are 240 MB) — a new TimescaleDB version = change TIMESCALEDB_VERSION + rebuild; an existing database
# then needs ALTER EXTENSION timescaledb UPDATE, which needs the upgrade scripts -> add them to COPY then.
ARG TIMESCALEDB_VERSION=2.22.1
ARG PG_MAJOR=17
ARG POSTGIS_VERSION=3.5

FROM timescale/timescaledb:${TIMESCALEDB_VERSION}-pg${PG_MAJOR} AS ts

FROM postgis/postgis:${PG_MAJOR}-${POSTGIS_VERSION}-alpine
ARG TIMESCALEDB_VERSION

COPY --from=ts /usr/local/lib/postgresql/timescaledb.so \
               /usr/local/lib/postgresql/timescaledb-${TIMESCALEDB_VERSION}.so \
               /usr/local/lib/postgresql/timescaledb-tsl-${TIMESCALEDB_VERSION}.so \
               /usr/local/lib/postgresql/timescaledb-invalidations-${TIMESCALEDB_VERSION}.so \
               /usr/local/lib/postgresql/
COPY --from=ts /usr/local/share/postgresql/extension/timescaledb.control \
               /usr/local/share/postgresql/extension/timescaledb--${TIMESCALEDB_VERSION}.sql \
               /usr/local/share/postgresql/extension/
# timescaledb-tune (TS_TUNE_MEMORY / TS_TUNE_NUM_CPUS) + the official init scripts:
#   000 = CREATE EXTENSION timescaledb (+ TIMESCALEDB_TELEMETRY=off), 001 = tune postgresql.conf
#   (run before PostGIS's 10_postgis.sh and our zz-aquarius.sql)
COPY --from=ts /usr/local/bin/timescaledb-tune /usr/local/bin/
COPY --from=ts /docker-entrypoint-initdb.d/000_install_timescaledb.sh \
               /docker-entrypoint-initdb.d/001_timescaledb_tune.sh \
               /docker-entrypoint-initdb.d/

# TimescaleDB must be preloaded; new clusters copy postgresql.conf from this sample
RUN sed -ri "s/^#?shared_preload_libraries = '[^']*'/shared_preload_libraries = 'timescaledb'/" \
        /usr/local/share/postgresql/postgresql.conf.sample && \
    grep -q "^shared_preload_libraries = 'timescaledb'" /usr/local/share/postgresql/postgresql.conf.sample
