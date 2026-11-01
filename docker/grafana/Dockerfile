# aquarius-grafana — Grafana with the TimescaleDB datasource + Aquarius dashboard baked in
# (pull-only VM needs no files besides compose.server.yml + .env). See docker/communication_plan.md.
#   MAVLink -> mavlink_mqtt.py -> Mosquitto -> Telegraf -> TimescaleDB -> this Grafana :3000
# Edit the dashboard in the UI, then Share -> Export -> JSON into dashboards/aquarius.json and rebuild.
ARG GRAFANA_VERSION=12.2.0
FROM grafana/grafana:${GRAFANA_VERSION}

COPY provisioning /etc/grafana/provisioning
COPY dashboards /etc/grafana/dashboards

# TSDB_* are read by provisioning/datasources/timescaledb.yml; set them in docker/compose.*.yml
ENV TSDB_HOST=127.0.0.1:5432 \
    TSDB_DB=aquarius \
    TSDB_USER=aquarius \
    TSDB_PASSWORD= \
    GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH=/etc/grafana/dashboards/aquarius.json \
    GF_DASHBOARDS_MIN_REFRESH_INTERVAL=1s \
    GF_ANALYTICS_REPORTING_ENABLED=false \
    GF_ANALYTICS_CHECK_FOR_UPDATES=false \
    GF_NEWS_NEWS_FEED_ENABLED=false
