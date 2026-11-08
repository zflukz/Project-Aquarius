# aquarius-grafana — Grafana with the TimescaleDB datasource + both dashboards baked in
# (pull-only VM needs no files besides compose.server.yml + .env). See docker/README.md "Dashboards".
#   dashboards/aquarius.json  Fleet telemetry (home): MAVLink -> MQTT -> Telegraf -> TimescaleDB
#                             edit in the UI, then Share -> Export -> JSON into this file
#   dashboards/mission.json   Mission monitor (profile pipeline): Kafka -> ingestion -> TimescaleDB
#                             generated: python3 docker/grafana/build_dashboards.py
# Rebuild the image after changing either file.
ARG GRAFANA_VERSION=12.2.0
FROM grafana/grafana:${GRAFANA_VERSION}

COPY provisioning /etc/grafana/provisioning
COPY dashboards/*.json /etc/grafana/dashboards/

# TSDB_* are read by provisioning/datasources/timescaledb.yml; set them in docker/compose.*.yml
ENV TSDB_HOST=127.0.0.1:5432 \
    TSDB_DB=aquarius \
    TSDB_USER=aquarius \
    TSDB_PASSWORD= \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/etc/grafana/dashboards/aquarius.json \
    GF_DASHBOARDS_MIN_REFRESH_INTERVAL=1s \
    GF_PANELS_DISABLE_SANITIZE_HTML=true \
    GF_ANALYTICS_REPORTING_ENABLED=false \
    GF_ANALYTICS_CHECK_FOR_UPDATES=false \
    GF_NEWS_NEWS_FEED_ENABLED=false
