#!/usr/bin/env python3
"""Generate the provisioned Grafana dashboards (Phase D) from readable Python.

    python3 grafana/build_dashboards.py      # rewrites grafana/provisioning/dashboards/aquarius/*.json

Edit the panels here, not in the JSON: the JSON is overwritten. Grafana reloads
the files within 30 s (no restart). Changes made in the Grafana UI are kept
until the file changes; to keep one, copy it back here.

The SQL behind the panels is in db/migrations/0007_d_dashboard_helpers.sql.
"""
from __future__ import annotations

import json
import os

DS = {"type": "grafana-postgresql-datasource", "uid": "aquarius-db"}
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provisioning", "dashboards", "aquarius")
M = "'$mission'"


def target(sql, ref="A", fmt="table"):
    return {"refId": ref, "datasource": DS, "editorMode": "code", "format": fmt, "rawQuery": True, "rawSql": sql}


_id = [0]


def panel(kind, title, x, y, w, h, targets, description="", **extra):
    _id[0] += 1
    p = {"id": _id[0], "type": kind, "title": title, "description": description, "datasource": DS,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": targets}
    p.update(extra)
    return p


def row(title, y):
    _id[0] += 1
    return {"id": _id[0], "type": "row", "title": title, "collapsed": False,
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1}, "panels": []}


def thresholds(*steps):
    """steps: (value or None, color) ... first value must be None (base)."""
    return {"mode": "absolute", "steps": [{"value": v, "color": c} for v, c in steps]}


def stat(title, sql, x, y, w, unit="none", decimals=0, steps=None, description=""):
    return panel("stat", title, x, y, w, 4, [target(sql)], description,
                 fieldConfig={"defaults": {"unit": unit, "decimals": decimals,
                                           "color": {"mode": "thresholds"},
                                           "thresholds": steps or thresholds((None, "text"))},
                              "overrides": []},
                 options={"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                          "colorMode": "value", "graphMode": "none", "textMode": "value",
                          "justifyMode": "center", "orientation": "auto"})


def timeseries(title, sql, x, y, w, h, unit, description="", min_=None, max_=None):
    defaults = {"unit": unit, "color": {"mode": "palette-classic-by-name"},
                "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 0, "showPoints": "never",
                           "spanNulls": True}}
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    return panel("timeseries", title, x, y, w, h, [target(sql, fmt="time_series")], description,
                 fieldConfig={"defaults": defaults, "overrides": []},
                 options={"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                          "tooltip": {"mode": "multi", "sort": "none"}})


def markers(name, ref, size, color, symbol, opacity=0.9, text=None, color_field=None):
    style = {"size": {"fixed": size, "min": size, "max": size},
             "color": {"fixed": color, **({"field": color_field} if color_field else {})},
             "opacity": opacity,
             "symbol": {"mode": "fixed", "fixed": f"img/icons/marker/{symbol}.svg"},
             "symbolAlign": {"horizontal": "center", "vertical": "center"},
             "rotation": {"fixed": 0, "mode": "mod", "min": -360, "max": 360}}
    if text:
        style["text"] = {"field": text, "mode": "field", "fixed": ""}
        style["textConfig"] = {"fontSize": 12, "offsetX": 0, "offsetY": -14, "textAlign": "center",
                               "textBaseline": "middle"}
    return {"type": "markers", "name": name, "config": {"style": style, "showLegend": False},
            "location": {"mode": "coords", "latitude": "lat", "longitude": "lon"},
            "filterData": {"id": "byRefId", "options": ref}, "tooltip": True}


def override(field, props):
    return {"matcher": {"id": "byName", "options": field},
            "properties": [{"id": k, "value": v} for k, v in props.items()]}


# one colour per drone on the map (a string field is coloured through value mappings)
_PALETTE = ["#73bf69", "#f2cc0c", "#8ab8ff", "#ff780a", "#f2495c", "#5794f2", "#b877d9", "#ff9830",
            "#96d98d", "#ca95e5"]
DRONE_COLORS = [{"type": "value", "options": {f"uav{i}": {"color": c, "index": i}
                                              for i, c in enumerate(_PALETTE, start=1)}},
                {"type": "value", "options": {"uav0": {"color": "#fade2a", "index": 0}}}]


def mission_dashboard():
    _id[0] = 0
    P = []
    y = 0

    # -- overview -------------------------------------------------------------------
    P.append(row("ภาพรวมภารกิจ", y)); y += 1
    P.append(stat("Coverage", f"""
SELECT c.coverage_pct
  FROM search_area a, aq_coverage(a.area_id, 'flown', $swath) c
 WHERE a.area_id = aq_current_area({M})""", 0, y, 5, unit="percent", decimals=1,
        steps=thresholds((None, "orange"), (50, "yellow"), (90, "green")),
        description="พื้นที่ที่แนวบินจริงผ่าน / พื้นที่ค้นหา (C4, aq_coverage) · ความกว้างกล้องตั้งที่ตัวแปร swath"))
    P.append(stat("ผู้ประสบภัยที่พบ", f"""
SELECT count(*) FROM persons WHERE mission_id = {M} AND status <> 'rejected'""", 5, y, 5,
        steps=thresholds((None, "text"), (1, "red")),
        description="จำนวนหมุด (คน) หลังรวมการตรวจพบที่ห่างกันไม่เกิน MERGE_RADIUS_M (C5)"))
    P.append(stat("การตรวจพบ", f"""
SELECT count(*) FROM survivors WHERE mission_id = {M}""", 10, y, 4,
        description="survivors ทั้งหมดก่อนรวม (หลายเฟรม / หลายลำต่อคน)"))
    P.append(stat("โดรนที่ส่งข้อมูล", f"""
SELECT count(*) FROM aq_drone_status({M}, interval '10 seconds')""", 14, y, 5,
        steps=thresholds((None, "red"), (1, "green")),
        description="โดรนที่มี telemetry เข้าฐานข้อมูลภายใน 10 วินาทีล่าสุด"))
    P.append(stat("message ถูกปฏิเสธ (1 ชม.)", """
SELECT count(*) FROM ingest_rejects WHERE received_at > now() - interval '1 hour'""", 19, y, 5,
        steps=thresholds((None, "green"), (1, "red")),
        description="message ที่ไม่ผ่าน schema ดูสาเหตุในตาราง ingest_rejects"))
    y += 4

    # -- map + drones -------------------------------------------------------------------
    P.append(row("แผนที่และสถานะโดรน", y)); y += 1
    map_targets = [
        target(f"""
SELECT lat, lon, CASE WHEN covered THEN 1 ELSE 0 END AS covered, cell_row AS "แถว", cell_col AS "คอลัมน์"
  FROM aq_cell_coverage(aq_current_area({M}), $swath)""", "A"),
        target(f"""
SELECT ST_Y(p.geom) AS lat, ST_X(p.geom) AS lon, fp.drone_id
  FROM flight_paths fp,
       LATERAL ST_DumpPoints(ST_Segmentize(fp.geom::geography, 10)::geometry) p
 WHERE fp.mission_id = {M} AND fp.kind = 'planned'
   AND fp.version = (SELECT max(version) FROM flight_paths q
                      WHERE q.mission_id = fp.mission_id AND q.drone_id = fp.drone_id AND q.kind = 'planned')""", "B"),
        target(f"""
SELECT DISTINCT ON (drone_id, time_bucket('2 seconds', ts)) lat, lon, drone_id
  FROM telemetry
 WHERE mission_id = {M} AND $__timeFilter(ts) AND (alt_rel IS NULL OR alt_rel >= 2)
 ORDER BY drone_id, time_bucket('2 seconds', ts), ts""", "C"),
        target(f"""
SELECT drone_id, lat, lon, round(battery_pct) AS "แบต %", nav_state AS "โหมด"
  FROM aq_drone_status({M}, interval '1 hour')""", "D"),
        target(f"""
SELECT lat, lon, n_detections AS "ตรวจพบ", round(max_conf::numeric, 2) AS "มั่นใจ", status
  FROM survivor_pins WHERE mission_id = {M} AND status <> 'rejected'""", "E"),
        target(f"""
SELECT lat, lon, label FROM ground_truth_persons WHERE mission_id = {M}""", "F"),
    ]
    layers = [
        markers("กริด (เขียว = ค้นแล้ว)", "A", 7, "#8e8e8e", "square", 0.45, color_field="covered"),
        markers("แนวบินตามแผน", "B", 2, "#b0b0b0", "circle", 0.6),
        markers("แนวบินจริง", "C", 3, "blue", "circle", 0.9, color_field="drone_id"),
        markers("ตำแหน่งจริงของคน (ground truth)", "F", 9, "#5794f2", "circle", 0.5),
        markers("ผู้ประสบภัย", "E", 12, "red", "triangle", 1.0, text="ตรวจพบ"),
        markers("โดรน", "D", 14, "blue", "circle", 1.0, text="drone_id", color_field="drone_id"),
    ]
    P.append(panel("geomap", "แผนที่ภารกิจ", 0, y, 14, 18, map_targets,
                   "กริดเปลี่ยนเป็นสีเขียวเมื่อแนวบินจริงผ่าน (D2) · จุดเทา = แนวบินตามแผน mCPP · "
                   "สามเหลี่ยมแดง = หมุดผู้ประสบภัย ตัวเลข = จำนวนครั้งที่ตรวจพบ · วงกลมฟ้า = ตำแหน่งจริง (demo/F3)",
                   fieldConfig={"defaults": {"color": {"mode": "palette-classic-by-name"}},
                                "overrides": [override("drone_id", {"mappings": DRONE_COLORS}),
                                              override("covered", {
                                    "color": {"mode": "thresholds"},
                                    "thresholds": thresholds((None, "#8e8e8e"), (1, "green"))})]},
                   options={"view": {"id": "fit", "lat": 0, "lon": 0, "zoom": 16, "allLayers": True,
                                     "padding": 10, "maxZoom": 19},
                            "controls": {"showZoom": True, "mouseWheelZoom": True, "showAttribution": True,
                                         "showScale": True, "showMeasure": True},
                            "basemap": {"type": "default", "name": "Basemap"},
                            "layers": layers, "tooltip": {"mode": "details"}}))

    P.append(panel("table", "สถานะโดรน", 14, y, 10, 7, [target(f"""
SELECT drone_id AS "โดรน", round(battery_pct) AS "แบต %", round(alt_rel::numeric, 1) AS "สูง (m)",
       round(speed_ms::numeric, 1) AS "เร็ว (m/s)", nav_state AS "โหมด", armed,
       round(age_s::numeric, 1) AS "ล่าสุด (s)"
  FROM aq_drone_status({M}, interval '1 hour')""")],
        "ค่าล่าสุดของแต่ละลำ (D1) · ล่าสุด (s) = วินาทีตั้งแต่ข้อมูลเข้าฐานข้อมูล เกิน 5 = สัญญาณหาย",
        fieldConfig={"defaults": {"custom": {"align": "center"}},
                     "overrides": [
                         override("แบต %", {"custom.cellOptions": {"type": "color-background", "mode": "basic"},
                                            "color": {"mode": "thresholds"},
                                            "thresholds": thresholds((None, "red"), (20, "orange"), (40, "green"))}),
                         override("ล่าสุด (s)", {"custom.cellOptions": {"type": "color-text"},
                                                "color": {"mode": "thresholds"},
                                                "thresholds": thresholds((None, "green"), (5, "red"))})]},
        options={"showHeader": True, "cellHeight": "sm"}))
    P.append(timeseries("แบตเตอรี่", f"""
SELECT $__timeGroupAlias(ts, '2s'), drone_id AS metric, avg(battery_remaining) * 100 AS "แบต %"
  FROM telemetry WHERE mission_id = {M} AND $__timeFilter(ts)
 GROUP BY 1, drone_id ORDER BY 1""", 14, y + 7, 10, 5, "percent", "D1", 0, 100))
    P.append(timeseries("ความสูง", f"""
SELECT $__timeGroupAlias(ts, '2s'), drone_id AS metric, avg(alt_rel) AS "สูง (m)"
  FROM telemetry WHERE mission_id = {M} AND $__timeFilter(ts)
 GROUP BY 1, drone_id ORDER BY 1""", 14, y + 12, 10, 6, "lengthm", "D1"))
    y += 18

    P.append(panel("table", "หมุดผู้ประสบภัย", 0, y, 24, 6, [target(f"""
SELECT left(person_id::text, 8) AS "หมุด", status AS "สถานะ", n_detections AS "ตรวจพบ (ครั้ง)",
       round(max_conf::numeric, 2) AS "มั่นใจสูงสุด", round(lat::numeric, 6) AS lat, round(lon::numeric, 6) AS lon,
       first_ts AS "พบครั้งแรก", last_ts AS "เห็นล่าสุด", best_snapshot AS "ภาพหลักฐาน"
  FROM survivor_pins WHERE mission_id = {M} ORDER BY first_ts""")],
        "เปลี่ยนสถานะ: UPDATE persons SET status = 'confirmed' WHERE person_id = ... (หน้าจอยืนยันอยู่ใน Phase E)",
        fieldConfig={"defaults": {}, "overrides": []}, options={"showHeader": True, "cellHeight": "sm"}))
    y += 6

    # -- cameras --------------------------------------------------------------------------
    P.append(row("กล้อง (สด จาก Kafka image-frames)", y)); y += 1
    P.append(panel("text", "", 0, y, 24, 11, [],
                   "ภาพเดียวกับที่ AI Detection อ่าน · ตั้งที่อยู่ service ที่ตัวแปร stream_url",
                   options={"mode": "html", "content":
                            '<iframe src="$stream_url/?minw=280" '
                            'style="width:100%;height:100%;border:0;background:#111217"></iframe>'},
                   datasource=None))
    y += 11

    # -- pipeline health (D3) -------------------------------------------------------------
    P.append(row("สุขภาพ pipeline (D3)", y)); y += 1
    P.append(timeseries("ความหน่วงโดรน → ฐานข้อมูล", f"""
SELECT $__timeGroupAlias(received_at, '10s'),
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY extract(epoch FROM received_at - ts) * 1000) AS "p50",
       percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM received_at - ts) * 1000) AS "p95"
  FROM telemetry WHERE mission_id = {M} AND $__timeFilter(received_at)
 GROUP BY 1 ORDER BY 1""", 0, y, 12, 7, "ms",
        "received_at − ts ของ telemetry ทุก 10 วินาที · เชื่อได้เมื่อนาฬิกาตรงกัน (โดรนจริง + chrony หรือ Gazebo ที่ RTF ≈ 1)"))
    P.append(timeseries("telemetry ที่เข้าฐานข้อมูลต่อลำ", f"""
SELECT $__timeGroupAlias(received_at, '10s'), drone_id AS metric, count(*) / 10.0 AS "Hz"
  FROM telemetry WHERE mission_id = {M} AND $__timeFilter(received_at)
 GROUP BY 1, drone_id ORDER BY 1""", 12, y, 12, 7, "hertz",
        "ควรเป็น ~10 Hz ต่อลำ (A2) · ต่ำกว่านั้น = ข้อมูลหายระหว่างทาง (D3 loss rate)", 0))

    variables = [
        {"name": "mission", "label": "ภารกิจ", "type": "query", "datasource": DS,
         "query": "SELECT mission_id::text AS __value, coalesce(name, left(mission_id::text, 8)) || ' · ' || "
                  "coalesce(to_char(last_ts AT TIME ZONE 'Asia/Bangkok', 'DD Mon HH24:MI'), 'no data') AS __text "
                  "FROM missions ORDER BY last_ts DESC NULLS LAST LIMIT 50",
         "refresh": 2, "sort": 0, "includeAll": False, "multi": False, "current": {}, "options": [],
         "hide": 0},
        {"name": "swath", "label": "ความกว้างกล้อง (m)", "type": "custom", "query": "0,30,70",
         "current": {"text": "70", "value": "70"}, "options": [], "hide": 0, "includeAll": False, "multi": False,
         "description": "0 = นับช่องที่เส้นบินผ่าน (C4) · 70 = ภาพกว้างจริงที่ความสูง 30 m (HFOV 100°)"},
        {"name": "stream_url", "label": "ที่อยู่กล้อง", "type": "textbox", "query": "http://localhost:8090",
         "current": {"text": "http://localhost:8090", "value": "http://localhost:8090"}, "options": [],
         "hide": 0, "description": "เครื่องอื่นให้ใส่ IP ของ ground station เช่น http://192.168.1.10:8090"},
    ]
    return {
        "uid": "aquarius-mission", "title": "Aquarius — Mission monitor", "tags": ["aquarius", "phase-d"],
        "timezone": "browser", "editable": True, "graphTooltip": 1, "schemaVersion": 39, "version": 1,
        "refresh": "2s", "time": {"from": "now-30m", "to": "now"},
        "timepicker": {"refresh_intervals": ["1s", "2s", "5s", "10s", "30s", "1m"]},
        "templating": {"list": variables}, "annotations": {"list": []}, "links": [], "panels": P,
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "mission.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mission_dashboard(), f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
