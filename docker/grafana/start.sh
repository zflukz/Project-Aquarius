#!/bin/sh
# aquarius-grafana start: copy the baked-in dashboards to the provisioning folder and replace the
# browser-side links http://localhost:<port> (camera 8083, imagestream 8090) with HOST_IP from .env,
# so a browser on another computer can open them. HOST_IP empty / localhost = files unchanged (local).
# Copied fresh on every start → changing HOST_IP + recreating the container is enough.
set -e
SRC=/usr/share/aquarius/dashboards
DST=/etc/grafana/dashboards
for f in "$SRC"/*.json; do
  if [ -n "$HOST_IP" ] && [ "$HOST_IP" != localhost ]; then
    sed "s#http://localhost:#http://${HOST_IP}:#g" "$f" > "$DST/$(basename "$f")"
  else
    cp "$f" "$DST/"
  fi
done
echo "aquarius-grafana: dashboard links -> http://${HOST_IP:-localhost}:<port>"
exec /run.sh "$@"
