# Aquarius — ER Diagram

ตารางของ data pipeline ในฐานข้อมูล `aquarius` (PostgreSQL 17 + TimescaleDB + PostGIS) ตาม migration `0001`–`0008`
ใน `docker/db/migrations/` (19 พ.ย. 2026) รายละเอียดทุกคอลัมน์, ฟังก์ชัน และ index อยู่ใน
[`data_dictionary.md`](data_dictionary.md) ส่วนหน้าแบบ interactive อยู่ใน Claude artifact "Aquarius ER Diagram"

- **เส้นทึบ** = foreign key จริงในฐานข้อมูล
- **เส้นประ** = อ้างถึงกันจริง แต่ไม่มี constraint (`telemetry`, `detections` รับ 10 แถว/วินาทีต่อลำ จึงไม่มี FK
  ingestion สร้างแถวใน `missions` / `drones` ให้ก่อนเขียนแทน)
- ลบ mission แล้วตารางที่ผูกด้วย FK ถูกลบตาม (`ON DELETE CASCADE`) ยกเว้น `ground_truth_persons.mission_id` และ
  `survivors.person_id` ที่กลายเป็น NULL · `survivors.mission_id` และ `replanning_events.mission_id` เป็น FK
  แบบไม่ cascade (ลบ mission ไม่ได้ถ้ายังมีแถวอ้างอยู่)
- `geom` ทุกตัวเป็น EPSG:4326 สร้างจาก `lat` / `lon` อัตโนมัติ (generated) ระยะและพื้นที่คำนวณใน UTM (`aq_utm_srid`)

```mermaid
erDiagram
    missions {
        uuid mission_id PK
        text name
        text status "planned / active / completed / aborted"
        timestamptz first_ts
        timestamptz last_ts
        timestamptz created_at
        text notes
    }
    drones {
        text drone_id PK "uav0, uav1 ..."
        text model
        timestamptz first_ts
        timestamptz last_ts
        text notes
    }
    telemetry {
        timestamptz ts PK "sim time, hypertable"
        text drone_id PK
        uuid mission_id
        float8 lat
        float8 lon
        real alt_amsl
        real alt_rel
        real q "REAL[4] w x y z"
        real vel_ned "REAL[3] N E D"
        real battery_remaining "0-1"
        real battery_voltage
        text nav_state
        boolean armed
        timestamptz sent_at "0008"
        timestamptz received_at
        geometry geom "Point 4326"
    }
    detections {
        timestamptz ts PK
        text drone_id PK
        bigint frame_id PK
        text model_version
        int image_w
        int image_h
        jsonb objects "class conf bbox_xyxy center_uv"
        timestamptz received_at
    }
    survivors {
        uuid detection_id PK
        uuid mission_id FK
        uuid person_id FK "0005"
        text drone_id
        bigint frame_id
        timestamptz ts
        float8 lat
        float8 lon
        real conf
        real pose_dt_ms
        text snapshot_path
        text status "pending / confirmed / rejected"
        timestamptz received_at
        geometry geom "Point 4326"
    }
    persons {
        uuid person_id PK
        uuid mission_id FK
        float8 lat "conf-weighted mean"
        float8 lon
        int n_detections
        real max_conf
        timestamptz first_ts
        timestamptz last_ts
        text status "pending / confirmed / rejected"
        timestamptz updated_at
        geometry geom "Point 4326"
    }
    survivor_pins {
        uuid person_id "VIEW: persons + best_snapshot"
        text best_snapshot "snapshot of the most confident detection"
    }
    replanning_events {
        uuid event_id PK
        uuid mission_id FK
        timestamptz ts
        text event_type "rtl_triggered / reassigned / mission_complete"
        text drone_id
        real battery_remaining
        int waypoints_reassigned
        text assigned_to "TEXT[]"
        timestamptz received_at
    }
    search_area {
        uuid area_id PK
        uuid mission_id FK
        text name
        real cell_size_m "default 20"
        geometry geom "Polygon 4326"
        timestamptz created_at
    }
    grid_cells {
        uuid area_id PK, FK
        int cell_row PK "0 = south"
        int cell_col PK "0 = west"
        geometry geom "MultiPolygon 4326"
    }
    flight_paths {
        uuid path_id PK
        uuid mission_id FK, UK
        text drone_id FK, UK
        text kind UK "planned / flown"
        int version UK
        geometry geom "LineString 4326"
        timestamptz created_at
    }
    ground_truth_persons {
        bigint gt_id PK
        text world UK
        text label UK
        uuid mission_id FK
        float8 lat
        float8 lon
        real alt_amsl
        geometry geom "Point 4326"
    }
    ingest_rejects {
        bigint id PK
        text topic
        int kafka_part
        bigint kafka_offset
        text reason
        text payload
        timestamptz received_at
    }
    schema_migrations {
        text version PK
        text name
        text checksum "SHA-256"
        timestamptz applied_at
    }

    missions |o..o{ telemetry : "mission_id"
    drones ||..o{ telemetry : "drone_id"
    drones ||..o{ detections : "drone_id"
    detections |o..o{ survivors : "same drone_id, frame_id, ts"
    missions |o--o{ survivors : "mission_id"
    persons |o--o{ survivors : "person_id (merge 3 m)"
    missions ||--o{ persons : "mission_id"
    persons ||..|| survivor_pins : "view of"
    missions |o--o{ replanning_events : "mission_id"
    missions ||--o{ search_area : "mission_id"
    search_area ||--o{ grid_cells : "area_id"
    missions ||--o{ flight_paths : "mission_id"
    drones ||--o{ flight_paths : "drone_id"
    missions |o--o{ ground_truth_persons : "mission_id"
```

`ingest_rejects` และ `schema_migrations` เป็นตารางระบบ ไม่ผูกกับตารางอื่น

## ตารางที่ไม่ได้อยู่ในแผนภาพ

ตารางของ Telegraf (ทาง MAVLink → MQTT ของ zflukz ใช้กับ dashboard Fleet): `online`, `status`, `"position"`,
`attitude`, `battery`, `gps`, `health`, `mission`, `event` ไม่มีใน migration Telegraf สร้างเองตอนมีข้อมูลเข้า
ทุกตารางมี `time`, `fleet`, `drone` (`'0'`, `'1'` …), `received_at` + field ของ stream นั้น ลบหลัง `TSDB_RETENTION`
(30 วัน) และไม่ผูกกับตารางข้างบน คอลัมน์ครบอยู่ใน [`data_dictionary.md`](data_dictionary.md#ตาราง-telegraf-dashboard-fleet)
