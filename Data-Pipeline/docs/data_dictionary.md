# Aquarius — Data Dictionary

2026-11-19 · Chayuthpong

> Exported from the team's Claude Docs page "Aquarius — Data Dictionary" on 2026-11-19, updated 2026-11-20 (migrations `0001`–`0010`). Source of truth = `docker/db/migrations/`; update this file with every new migration. ER diagram: [`er_diagram.md`](er_diagram.md).

## ภาพรวมและข้อตกลง

ฐานข้อมูล `aquarius` (image `aquarius-timescaledb` = PostgreSQL 17 + TimescaleDB 2.22.1 + PostGIS 3.5, container `timescaledb` พอร์ต 5432 หรือ `TSDB_PORT`) มี 12 ตารางข้อมูล, 1 view, 2 ตารางระบบ และ 12 ฟังก์ชัน สร้างจาก migration `0001`–`0010` ใน `docker/db/migrations/` (0007 เพิ่มเฉพาะฟังก์ชันของ dashboard, 0008 เพิ่มคอลัมน์ `telemetry.sent_at` สำหรับวัด latency, 0009–0010 เพิ่มคอลัมน์ landed / GPS / health / CPU / mission ใน `telemetry` และตาราง `events` เพื่อให้ทาง Kafka มีข้อมูลเท่าทาง MQTT) เอกสารนี้ตรงกับ migration ชุดนั้น ถ้าเพิ่ม migration ใหม่ต้องแก้เอกสารนี้ด้วย

ในฐานข้อมูลเดียวกันยังมีตารางของ Telegraf (ทาง MAVLink → MQTT ของ zflukz ที่ dashboard Fleet ใช้) อีก 9 ตาราง: `online`, `status`, `"position"`, `attitude`, `battery`, `gps`, `health`, `mission`, `event` ทุกตารางมี `time`, `fleet`, `drone` (เลข 0, 1, … เป็น text) + field ตาม message สร้างเองตอนมีข้อมูลเข้า ไม่ได้อยู่ใน migration และลบหลัง `TSDB_RETENTION` (30 วัน) ตารางของ pipeline ในเอกสารนี้ไม่โดน retention นี้ เทียบกัน: pipeline ใช้ `drone_id = 'uav0'` ส่วน Telegraf ใช้ `drone = '0'`

| เรื่อง | ข้อตกลง |
| --- | --- |
| เวลา | `TIMESTAMPTZ` เก็บเป็น UTC ความละเอียด µs · `ts` = เวลาที่ข้อมูลเกิด (จาก `ts_us` ใน message, A4) · `received_at` = เวลาที่เข้าฐานข้อมูล |
| พิกัด | `lat`/`lon` เป็นองศา WGS84 · `geom` เป็น PostGIS EPSG:4326 สร้างจาก lat/lon อัตโนมัติ (generated) มี GiST index · ระยะและพื้นที่คำนวณใน UTM ด้วย `ST_Transform(geom, aq_utm_srid(geom))` (ในไทย = 32647) |
| หน่วย | ความสูง/ระยะ m · ความเร็ว m/s · แบตเตอรี่ 0–1 (ไม่ใช่ %) · แรงดัน V · ความมั่นใจ 0–1 · พิกเซลนับจากมุมซ้ายบนของภาพ |
| ระบบแกน | `q` = quaternion w,x,y,z จาก body FRD → NED · `vel_ned` = North, East, Down |
| id | `drone_id` = `uav<เลข>` เริ่มที่ `uav0` (เช่น `uav1` = PX4 instance 1) · `mission_id`, `detection_id`, `event_id`, `person_id` = UUID |
| กันข้อมูลซ้ำ | ทุกตารางที่มาจาก Kafka มี primary key ตามธรรมชาติ + insert แบบ `ON CONFLICT DO NOTHING` |
| ภาพ | ไม่เก็บในฐานข้อมูล อยู่ใน Kafka topic `image-frames` 1 วัน · ภาพหลักฐานเก็บเป็น path ใน `survivors.snapshot_path` |

**ใครเขียนตารางไหน**

| ตาราง | เขียนโดย | ต้นทาง | ปริมาณ |
| --- | --- | --- | --- |
| `telemetry` | ingestion | Kafka `telemetry` ← bridge | 10 แถว/วินาทีต่อลำ (36,000/ชม.) |
| `detections` | ingestion | Kafka `detections` ← AI Detection | ≤ 2 แถว/วินาทีต่อลำ (เฉพาะเฟรมที่เจอคน) |
| `survivors` | ingestion | Kafka `survivors` ← Geo-Localization | 1 แถวต่อการตรวจพบ |
| `replanning_events` | ingestion | Kafka `replanning-events` ← Multi-UAV Control | ไม่กี่แถวต่อภารกิจ |
| `events` | ingestion | Kafka `events` ← bridge (PX4 `mavlink_log`) | ไม่กี่แถว/นาทีต่อลำ |
| `missions`, `drones` | ingestion (อัตโนมัติ) + ผู้ควบคุม | id ที่เจอใน message | 1 แถวต่อภารกิจ / ต่อโดรน |
| `persons` | `aq_merge_survivors()` (ingestion เรียก) | `survivors` | 1 แถวต่อผู้ประสบภัย |
| `search_area`, `grid_cells` | planner ผ่าน `aq_create_search_area()` | mCPP | ครั้งเดียวต่อภารกิจ |
| `flight_paths` | planner / `aq_save_flown_paths()` | mCPP / telemetry | 1 แถวต่อลำต่อ version |
| `ground_truth_persons` | สคริปต์โหลด world (Phase F) | Gazebo `pose/info` | ตามจำนวนคนใน world |

## missions

1 แถวต่อภารกิจ ingestion สร้างให้เองครั้งแรกที่เจอ `mission_id` ใน message และอัปเดต `first_ts`/`last_ts` ทุก batch · migration 0002

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `mission_id` | UUID | ไม่ | PK | id ภารกิจ (ค่า `mission_id` ของ bridge) |
| `name` | TEXT | ได้ |  | ชื่อที่ผู้ควบคุมตั้ง |
| `status` | TEXT | ไม่ | `'active'` | `planned` · `active` · `completed` · `aborted` |
| `first_ts` | TIMESTAMPTZ | ได้ |  | เวลาข้อมูลแรกสุดที่เห็นของภารกิจ |
| `last_ts` | TIMESTAMPTZ | ได้ |  | เวลาข้อมูลล่าสุด |
| `created_at` | TIMESTAMPTZ | ไม่ | `now()` | เวลาที่สร้างแถว |
| `notes` | TEXT | ได้ |  | หมายเหตุ |

## drones

1 แถวต่อโดรน ingestion สร้างและอัปเดตให้เองจาก `drone_id` ทุก topic · migration 0002

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `drone_id` | TEXT | ไม่ | PK, ต้องตรง `^uav[0-9]+$` | เช่น `uav1` |
| `model` | TEXT | ได้ |  | รุ่นโดรน (ตั้งเอง) |
| `first_ts` | TIMESTAMPTZ | ได้ |  | เห็นครั้งแรก (เวลาข้อมูล) |
| `last_ts` | TIMESTAMPTZ | ได้ |  | เห็นล่าสุด ใช้บอกว่าโดรนหายไปนานแค่ไหน |
| `notes` | TEXT | ได้ |  | หมายเหตุ |

## telemetry

ตำแหน่งและสถานะของโดรน 10 ครั้ง/วินาทีต่อลำ เป็น TimescaleDB hypertable แบ่ง chunk วันละ 1 chunk บีบอัดหลัง 7 วัน (job `policy_compression` รันทุก 12 ชม., แยกตาม `drone_id`) ไม่ลบทิ้ง · ไม่มี foreign key (เพื่อให้เขียนเร็ว) · migration 0001, index ใน 0003, `sent_at` ใน 0008, คอลัมน์ landed – mission ใน 0009 / 0010

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย | มาจาก PX4 |
| --- | --- | --- | --- | --- | --- |
| `ts` | TIMESTAMPTZ | ไม่ | PK (drone_id, ts) | เวลาที่วัดตำแหน่ง (UTC, นาฬิกาของ sim — ไม่ใช้วัด latency) | `timestamp_sample` ของ local position |
| `drone_id` | TEXT | ไม่ | PK | เช่น `uav1` | namespace `/uav_N` |
| `mission_id` | UUID | ได้ | index (mission_id, ts) | ภารกิจ | parameter ของ bridge |
| `lat` | DOUBLE PRECISION | ไม่ |  | ละติจูด (°) | `vehicle_global_position.lat` |
| `lon` | DOUBLE PRECISION | ไม่ |  | ลองจิจูด (°) | `vehicle_global_position.lon` |
| `alt_amsl` | REAL | ได้ |  | ความสูงเหนือระดับน้ำทะเล (m) | `vehicle_global_position.alt` |
| `alt_rel` | REAL | ได้ |  | ความสูงจากจุดขึ้นบิน (m) | `−vehicle_local_position.z` |
| `q` | REAL[4] | ได้ |  | quaternion w,x,y,z (body FRD → NED) | `vehicle_attitude.q` |
| `vel_ned` | REAL[3] | ได้ |  | ความเร็ว N, E, D (m/s) | `vehicle_local_position.vx/vy/vz` |
| `battery_remaining` | REAL | ได้ |  | แบตที่เหลือ 0–1 | `battery_status.remaining` |
| `battery_voltage` | REAL | ได้ |  | แรงดัน (V) | `battery_status.voltage_v` |
| `nav_state` | TEXT | ได้ |  | โหมดบิน เช่น `OFFBOARD`, `AUTO_RTL` | `vehicle_status.nav_state` |
| `armed` | BOOLEAN | ได้ |  | มอเตอร์พร้อมหมุนไหม | `vehicle_status.arming_state` |
| `landed` | BOOLEAN | ได้ |  | อยู่บนพื้นไหม (0009) | `vehicle_land_detected.landed` |
| `gps_fix_type` | SMALLINT | ได้ |  | 1 ไม่มี fix, 2 2D, 3 3D, 4 DGPS, 5/6 RTK, 8 extrapolated (0009) | `vehicle_gnss.receiver.fix_type` |
| `gps_satellites` | SMALLINT | ได้ |  | จำนวนดาวเทียมที่ใช้ (0009) | `vehicle_gnss.receiver.satellites_used` |
| `gps_eph` | REAL | ได้ |  | ความคลาดเคลื่อนแนวราบ (m) (0009) | `vehicle_gnss.receiver.eph` |
| `gps_epv` | REAL | ได้ |  | ความคลาดเคลื่อนแนวดิ่ง (m) (0009) | `vehicle_gnss.receiver.epv` |
| `health_ok` | BOOLEAN | ได้ |  | `false` = มี failsafe flag ร้ายแรง (เซนเซอร์ / ตำแหน่ง / GPS หาย / แบตเสีย / มอเตอร์) ส่วน flag สถานการณ์ เช่น ไม่มี mission, RC หาย ไม่นับ (0009) | คำนวณจาก `failsafe_flags` |
| `failsafe_flags` | TEXT[] | ได้ |  | ชื่อ failsafe flag ที่เป็นจริงอยู่ เช่น `{gnss_lost}` (0009) | `failsafe_flags` (ทุก field boolean) |
| `cpu_load` | REAL | ได้ |  | CPU ของ PX4 (%) (0010) | `cpuload.load` × 100 · ต้องมี patch `dds_topics` ของ px4-sim |
| `mission_seq` | INTEGER | ได้ |  | waypoint ปัจจุบัน เริ่มที่ 0 (0010) | `mission_result.seq_current` · ต้องมี patch |
| `mission_total` | INTEGER | ได้ |  | จำนวน item ใน mission ที่อัปโหลด, 0 = ไม่มี (0010) | `mission_result.seq_total` · ต้องมี patch |
| `mission_finished` | BOOLEAN | ได้ |  | บินครบ mission แล้ว (0010) | `mission_result.finished` · ต้องมี patch |
| `sent_at` | TIMESTAMPTZ | ได้ |  | เวลาที่ bridge ส่ง message เข้า Kafka (wall clock, CreateTime ของ message) · NULL สำหรับแถวก่อน 0008 | — |
| `received_at` | TIMESTAMPTZ | ไม่ | `clock_timestamp()` (0008, เดิม `now()`), index received_at DESC | เวลาที่แถวเขียนลงฐานข้อมูล · latency = `received_at − sent_at` (D3, dashboard Path compare) | — |
| `geom` | geometry(Point, 4326) | — | generated, GiST | จุดตำแหน่งจาก lon/lat | — |

คอลัมน์ `geom` คำนวณเอง ห้าม insert ค่า · คอลัมน์ 0009 / 0010 เป็น NULL ได้เสมอ (แถวจาก bridge รุ่นเก่า หรือก่อนได้รับ topic นั้นครั้งแรก) · ชื่อ topic ของ PX4 มี suffix ตามเวอร์ชัน (เช่น `vehicle_status_v5`) bridge หาเองจาก `MESSAGE_VERSION` ของ `px4_msgs` ที่ build ไว้ ถ้าจะบังคับชื่อให้ตั้งใน `bridge.yaml` · `cpuload`, `mission_result`, `mavlink_log` PX4 ไม่ส่งออกมาเอง ต้องใช้ `docker/px4-sim/dds_topics_extra.yaml`

## detections

ผลจาก AI Detection 1 แถวต่อ 1 เฟรมที่เจอวัตถุอย่างน้อย 1 ชิ้น พิกัดยังเป็นพิกเซลในภาพ (ยังไม่ใช่ lat/lon) · migration 0001

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `ts` | TIMESTAMPTZ | ไม่ | PK (drone_id, frame_id, ts), index ts | เวลาที่ถ่ายภาพ (UTC) |
| `drone_id` | TEXT | ไม่ | PK | โดรนที่ถ่าย |
| `frame_id` | BIGINT | ไม่ | PK | เลขเฟรม (ตรงกับ header `frame_id` ใน `image-frames`) |
| `model_version` | TEXT | ได้ |  | รุ่นโมเดล เช่น `yolo-sar-v1` |
| `image_w`, `image_h` | INTEGER | ได้ |  | ขนาดภาพ (px) |
| `objects` | JSONB | ไม่ |  | รายการวัตถุ (ดูด้านล่าง) |
| `received_at` | TIMESTAMPTZ | ไม่ | `now()` | เวลาเข้าฐานข้อมูล |

แต่ละชิ้นใน `objects` (อย่างน้อย 1 ชิ้น):

| ฟิลด์ | ชนิด | คำอธิบาย |
| --- | --- | --- |
| `class` | string | ชนิดวัตถุ เช่น `person` |
| `conf` | number 0–1 | ความมั่นใจ |
| `bbox_xyxy` | [x1, y1, x2, y2] | กรอบวัตถุ (px) |
| `center_uv` | [u, v] | จุดกึ่งกลาง (px) ที่ Georef ใช้ |

## survivors

ผลจาก Geo-Localization 1 แถวต่อการตรวจพบที่แปลงเป็นพิกัดแล้ว คนเดียวกันอาจมีหลายแถว (หลายเฟรม/หลายลำ) แถวเหล่านี้ถูกรวมเป็นหมุดใน `persons` · migration 0001, 0002, 0005

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `detection_id` | UUID | ไม่ | PK | id ที่ Georef สร้าง (ส่งซ้ำได้ ไม่เกิดแถวซ้ำ) |
| `mission_id` | UUID | ได้ | FK → missions | ภารกิจ |
| `drone_id` | TEXT | ไม่ |  | โดรนที่เจอ |
| `frame_id` | BIGINT | ได้ |  | เฟรมที่เจอ (อ้างถึง detections) |
| `ts` | TIMESTAMPTZ | ไม่ | index (mission_id, ts) | เวลาที่ถ่ายภาพ |
| `lat`, `lon` | DOUBLE PRECISION | ไม่ |  | ตำแหน่งผู้ประสบภัย (°) |
| `conf` | REAL | ได้ |  | ความมั่นใจของการตรวจพบ 0–1 |
| `pose_dt_ms` | REAL | ได้ |  | เวลาห่างระหว่างภาพกับ telemetry ที่ใช้คำนวณ (ms, A4) |
| `snapshot_path` | TEXT | ได้ |  | path ภาพหลักฐาน |
| `status` | TEXT | ไม่ | `'pending'` | `pending` · `confirmed` · `rejected` (ระดับการตรวจพบ สถานะหลักอยู่ที่ persons) |
| `person_id` | UUID | ได้ | FK → persons | คนที่การตรวจพบนี้ถูกรวมเข้า (NULL = ยังไม่รวม) |
| `received_at` | TIMESTAMPTZ | ไม่ | `now()` | เวลาเข้าฐานข้อมูล |
| `geom` | geometry(Point, 4326) | — | generated, GiST | จุดตำแหน่ง |

## persons

1 แถวต่อผู้ประสบภัย 1 คน (หมุดบน dashboard) `aq_merge_survivors()` รวม survivors ที่อยู่ห่างกันไม่เกิน `MERGE_RADIUS_M` (3 m) ในภารกิจเดียวกัน · migration 0005

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `person_id` | UUID | ไม่ | PK, `gen_random_uuid()` | id หมุด |
| `mission_id` | UUID | ไม่ | FK → missions (ลบภารกิจ = ลบด้วย) | ภารกิจ |
| `lat`, `lon` | DOUBLE PRECISION | ไม่ |  | ตำแหน่งหมุด = ค่าเฉลี่ยถ่วงด้วย conf ของทุกการตรวจพบ |
| `n_detections` | INTEGER | ไม่ | `0` | จำนวนการตรวจพบที่รวมเข้ามา |
| `max_conf` | REAL | ได้ |  | ความมั่นใจสูงสุด |
| `first_ts`, `last_ts` | TIMESTAMPTZ | ได้ |  | เจอครั้งแรก / ล่าสุด |
| `status` | TEXT | ไม่ | `'pending'` | `pending` · `confirmed` · `rejected` ตั้งโดยผู้ควบคุม |
| `updated_at` | TIMESTAMPTZ | ไม่ | `now()` | อัปเดตล่าสุด |
| `geom` | geometry(Point, 4326) | — | generated, GiST + GiST (geography) | จุดหมุด |

**view ****`survivor_pins`** — ทุกคอลัมน์ของ persons (ยกเว้น `updated_at`) บวก `best_snapshot` = `snapshot_path` ของการตรวจพบที่มั่นใจสูงสุดที่มีภาพ ใช้แสดงหมุดพร้อมภาพหลักฐาน (E2/E3)

## replanning_events

เหตุการณ์การวางแผนใหม่จาก Multi-UAV Control ใช้คำนวณ replanning response time (F2) · migration 0001, 0002

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `event_id` | UUID | ไม่ | PK | id เหตุการณ์ |
| `mission_id` | UUID | ได้ | FK → missions, index (mission_id, ts) | ภารกิจ |
| `ts` | TIMESTAMPTZ | ไม่ |  | เวลาที่เกิด |
| `event_type` | TEXT | ไม่ |  | `rtl_triggered` (โดรนกลับฐาน) · `reassigned` (แจกงานใหม่) · `mission_complete` |
| `drone_id` | TEXT | ได้ |  | โดรนที่เกี่ยวข้อง (NULL สำหรับเหตุการณ์ระดับภารกิจ) |
| `battery_remaining` | REAL | ได้ |  | แบตตอนเกิดเหตุ 0–1 |
| `waypoints_reassigned` | INTEGER | ได้ |  | จำนวน waypoint ที่แจกใหม่ |
| `assigned_to` | TEXT[] | ได้ |  | โดรนที่รับงานต่อ เช่น `{uav2,uav3}` |
| `received_at` | TIMESTAMPTZ | ไม่ | `now()` | เวลาเข้าฐานข้อมูล |

## events

ข้อความจาก PX4 (`mavlink_log` = ข้อความเดียวกับ STATUSTEXT ของทาง MQTT) เช่น `Takeoff detected`, `Landing detected`, คำเตือน failsafe เป็น hypertable chunk ละ 7 วัน ไม่มี foreign key ไม่ลบทิ้ง · bridge ส่งเข้า Kafka topic `events` (เก็บ 30 วัน) · migration 0010, schema `events.schema.json`

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `ts` | TIMESTAMPTZ | ไม่ | PK (drone_id, ts, text) | เวลาที่ bridge **ได้รับ** ข้อความ (นาฬิกาเดียวกับ telemetry ไม่ใช่เวลาที่ PX4 สร้าง) |
| `drone_id` | TEXT | ไม่ | PK | เช่น `uav0` |
| `mission_id` | UUID | ได้ | index (mission_id, ts DESC) | ภารกิจ |
| `severity` | SMALLINT | ไม่ |  | ระดับแบบ syslog 0 = emergency … 7 = debug |
| `level` | TEXT | ไม่ |  | ชื่อของ severity: `emergency` · `alert` · `critical` · `error` · `warning` · `notice` · `info` · `debug` |
| `text` | TEXT | ไม่ | PK | ข้อความ ไม่เกิน 200 ตัวอักษร |
| `received_at` | TIMESTAMPTZ | ไม่ | `clock_timestamp()` | เวลาเขียนลงฐานข้อมูล |

PK รวม `text` ด้วย: ข้อความต่างกันที่เวลาเดียวกันเก็บได้ทั้งคู่ ส่วน Kafka message ที่ส่งซ้ำจะถูกข้าม

## search_area

พื้นที่ค้นหา 1 ภารกิจมีได้หลายพื้นที่ สร้างด้วย `aq_create_search_area()` ซึ่งสร้างกริดให้ด้วย · migration 0002

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `area_id` | UUID | ไม่ | PK, `gen_random_uuid()` | id พื้นที่ |
| `mission_id` | UUID | ไม่ | FK → missions (ลบตาม) | ภารกิจ |
| `name` | TEXT | ได้ |  | ชื่อพื้นที่ เช่น `โซน A` |
| `cell_size_m` | REAL | ไม่ | `20` | ขนาดช่องกริด (m) |
| `geom` | geometry(Polygon, 4326) | ไม่ | GiST, ต้อง valid | ขอบเขตพื้นที่ |
| `created_at` | TIMESTAMPTZ | ไม่ | `now()` |  |

## grid_cells

ช่องกริดสี่เหลี่ยมของพื้นที่ เริ่มจากมุมตะวันตกเฉียงใต้ใน UTM ช่องที่ขอบถูกตัดให้พอดี polygon พื้นที่ทุกช่องจึงรวมเท่าพื้นที่ สร้างใหม่ด้วย `aq_make_grid(area_id)` · migration 0002, ฟังก์ชัน 0004/0006

| คอลัมน์ | ชนิด | Null | คีย์ | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `area_id` | UUID | ไม่ | PK (area_id, cell_row, cell_col), FK → search_area (ลบตาม) | พื้นที่ |
| `cell_row` | INTEGER | ไม่ | PK | แถว (0 = ใต้สุด) |
| `cell_col` | INTEGER | ไม่ | PK | คอลัมน์ (0 = ตะวันตกสุด) |
| `geom` | geometry(MultiPolygon, 4326) | ไม่ | GiST | รูปช่อง (ตัดตามพื้นที่แล้ว) |

## flight_paths

แนวบินต่อลำ `planned` มาจาก mCPP (replan = เพิ่ม version ใหม่) ส่วน `flown` เก็บจาก telemetry หลังจบภารกิจด้วย `aq_save_flown_paths()` ระหว่างบินให้ใช้ `aq_flown_paths()` ที่คำนวณสด · migration 0002

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `path_id` | UUID | ไม่ | PK, `gen_random_uuid()` |  |
| `mission_id` | UUID | ไม่ | FK → missions (ลบตาม), UNIQUE (mission_id, drone_id, kind, version) | ภารกิจ |
| `drone_id` | TEXT | ไม่ | FK → drones | โดรน |
| `kind` | TEXT | ไม่ |  | `planned` · `flown` |
| `version` | INTEGER | ไม่ | `1` | รอบที่วางแผน (ใช้ค่าสูงสุดเป็นแผนปัจจุบัน) |
| `geom` | geometry(LineString, 4326) | ไม่ | GiST | เส้นทาง |
| `created_at` | TIMESTAMPTZ | ไม่ | `now()` |  |

## ground_truth_persons

ตำแหน่งจริงของคนใน Gazebo world ใช้เทียบกับ `persons` เพื่อวัด georeferencing error เป็นเมตร (F3) ยังไม่มีสคริปต์โหลด (จะอ่านจาก `/world/<world>/pose/info`) · migration 0002

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `gt_id` | BIGSERIAL | ไม่ | PK |  |
| `world` | TEXT | ไม่ | UNIQUE (world, label) | ชื่อ world เช่น `flood`, `baylands` |
| `label` | TEXT | ไม่ | UNIQUE (world, label) | ชื่อ model คนใน world |
| `mission_id` | UUID | ได้ | FK → missions (ลบภารกิจ = NULL) | ภารกิจที่ใช้ world นี้ |
| `lat`, `lon` | DOUBLE PRECISION | ไม่ |  | ตำแหน่งจริง (°) |
| `alt_amsl` | REAL | ได้ |  | ความสูง (m) |
| `geom` | geometry(Point, 4326) | — | generated, GiST |  |

## ตารางระบบ

### ingest_rejects

message จาก Kafka ที่ไม่ผ่านการตรวจ (JSON ผิด, ไม่ตรง schema, id ไม่ใช่ UUID) เก็บไว้ดูสาเหตุแทนการทิ้งเงียบๆ ลบที่เก่ากว่า 30 วันด้วย `aq_prune_ingest_rejects()` · migration 0001

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `id` | BIGSERIAL | ไม่ | PK |  |
| `topic` | TEXT | ไม่ |  | Kafka topic ต้นทาง |
| `kafka_part`, `kafka_offset` | INTEGER, BIGINT | ได้ |  | ตำแหน่ง message ใน Kafka (ใช้หา message ต้นฉบับได้) |
| `reason` | TEXT | ไม่ |  | สาเหตุ เช่น `schema: lat: 123.0 is greater than the maximum of 90` |
| `payload` | TEXT | ได้ |  | เนื้อ message (4,000 ตัวอักษรแรก) |
| `received_at` | TIMESTAMPTZ | ไม่ | `now()`, index |  |

### schema_migrations

บันทึกว่ารันไฟล์ migration ไหนไปแล้ว เขียนโดย `db-migrate` เท่านั้น ห้ามแก้ด้วยมือ

| คอลัมน์ | ชนิด | Null | คีย์ / ค่าเริ่มต้น | คำอธิบาย |
| --- | --- | --- | --- | --- |
| `version` | TEXT | ไม่ | PK | เลขไฟล์ เช่น `0006` |
| `name` | TEXT | ไม่ |  | ชื่อไฟล์ เช่น `c4_grid_tolerance` |
| `checksum` | TEXT | ไม่ |  | SHA-256 ของไฟล์ตอนรัน (ใช้จับไฟล์ที่ถูกแก้ทีหลัง) |
| `applied_at` | TIMESTAMPTZ | ไม่ | `now()` | เวลาที่รัน |

## ตาราง Telegraf (dashboard Fleet)

ทาง MAVLink → MQTT ของ zflukz: `mavlink_mqtt.py` ส่ง MQTT `aquarius/<fleet>/drone/<i>/<stream>` แล้ว Telegraf เขียน 1 stream = 1 ตาราง ไม่มีใน migration — Telegraf สร้างตารางเองเมื่อมีข้อมูลเข้าครั้งแรก แล้วเรียก `aquarius_hypertable()` (จาก init script ใน compose) ให้เป็น hypertable chunk 1 วัน + index `(fleet, drone, time DESC)` + ลบหลัง `TSDB_RETENTION` (ค่าเริ่ม 30 วัน, `''`/`0` = เก็บตลอด) · ไม่มี key ไม่ผูกกับตาราง pipeline

คอลัมน์ที่ทุกตารางมี:

| คอลัมน์ | ชนิด | คำอธิบาย |
| --- | --- | --- |
| `time` | TIMESTAMPTZ | เวลาที่ bridge ส่ง (`t` ใน payload, wall clock ms) |
| `fleet` | TEXT | `sim` หรือ `real` (`FLEET`) |
| `drone` | TEXT | เลขโดรน `'0'`, `'1'` … (= pipeline `uav0`, `uav1`; sysid = เลข + 1) |
| `received_at` | TIMESTAMPTZ | `clock_timestamp()` ตอนเขียนแถว · latency = `received_at − time` (Path compare) |

field ของแต่ละตาราง (ตัวเลขเป็น DOUBLE PRECISION, ค่าจริง/เท็จเก็บเป็น 0/1, ข้อความเป็น TEXT; field ใหม่ = Telegraf `ADD COLUMN` เอง):

| ตาราง | ความถี่ | field | มาจาก MAVLink |
| --- | --- | --- | --- |
| `online` | 1 Hz | `online` 0/1 — heartbeat ล่าสุดไม่เกิน `OFFLINE_S`; Grafana ถือว่า offline ถ้าไม่มีแถวใหม่ 5 s | HEARTBEAT |
| `status` | 1 Hz + เมื่อเปลี่ยน | `armed` 0/1, `mode` (เช่น `MISSION`, `RTL`), `system_status` (`standby`, `active`, `critical` …), `landed_state` 0–4, `landed` (`on_ground`, `in_air`, `takeoff`, `landing`) | HEARTBEAT, EXTENDED_SYS_STATE |
| `"position"` (ต้องใส่ `"` เพราะเป็นคำสงวน) | 10 Hz | `lat`, `lon` (°), `alt_amsl`, `alt_rel` (m), `vn`, `ve`, `vd` (m/s), `speed` (m/s แนวราบ), `climb` (m/s, = −vd), `hdg` (° ถ้ามี) | GLOBAL_POSITION_INT |
| `attitude` | 10 Hz | `roll`, `pitch` (°), `yaw` (° 0–360) | ATTITUDE |
| `battery` | 1 Hz | `pct` (0–100 %, ต่างจาก pipeline ที่เป็น 0–1), `voltage` (V), `current` (A) | SYS_STATUS |
| `gps` | 1 Hz | `fix_type` (3 = 3D fix), `sats`, `eph`, `epv` (m) | GPS_RAW_INT |
| `health` | 1 Hz | `gyro_ok`, `accel_ok`, `mag_ok`, `baro_ok`, `gps_ok`, `all_ok` (0/1), `load` (% CPU) | SYS_STATUS |
| `mission` | เมื่อเปลี่ยน | `seq` (waypoint ปัจจุบัน), `total` (0 = ไม่มีภารกิจ) | MISSION_CURRENT |
| `event` | เมื่อเกิด | `severity` (0 = emergency … 7 = debug), `text` | STATUSTEXT (ต่อ chunk แล้ว) |

## ฟังก์ชัน

มี 12 ฟังก์ชัน `aq_*` จาก migration ทุกตัวคำนวณระยะและพื้นที่ใน UTM ของพื้นที่นั้น (ที่ Baylands = 32610, ในไทย = 32647) ส่วน `aquarius_hypertable` มาจาก init script ของ compose ไม่ได้อยู่ใน migration

| ฟังก์ชัน | พารามิเตอร์ (ค่าเริ่มต้น) | คืนค่า | ทำอะไร | ใครเรียก | migration |
| --- | --- | --- | --- | --- | --- |
| `aq_utm_srid` | `g geometry` | INTEGER | รหัส EPSG ของ UTM zone ที่จุดกึ่งกลางของ g อยู่ (326xx เหนือ / 327xx ใต้) | ทุกฟังก์ชันที่วัดเมตร | 0004 |
| `aq_make_grid` | `p_area uuid` | INTEGER (จำนวนช่อง) | ลบแล้วสร้าง `grid_cells` ใหม่ตาม `cell_size_m` เริ่มจากมุมตะวันตกเฉียงใต้ ตัดช่องขอบให้พอดี polygon ไม่นับส่วนเกิน < 1 cm · เกิน 250,000 ช่อง = error | `aq_create_search_area`, หรือเรียกเองหลังแก้พื้นที่ | 0004, แก้ 0006 |
| `aq_create_search_area` | `p_mission uuid`, `p_geojson text`, `p_name text` (NULL), `p_cell_size_m real` (20) | UUID (`area_id`) | รับ GeoJSON Polygon หรือ Feature (ลำดับ `[lon, lat]`) → สร้าง `missions` (สถานะ `planned`) ถ้ายังไม่มี + `search_area` + กริด | planner / mission API (ยังไม่มีคนเรียกจริง) | 0004 |
| `aq_flown_paths` | `p_mission uuid`, `p_min_alt_m real` (2), `p_from`, `p_to timestamptz` (NULL) | ตาราง `drone_id`, `points`, `first_ts`, `last_ts`, `geom` LineString | แนวบินจริงต่อลำจาก `telemetry` (ตัดจุดที่ต่ำกว่า `p_min_alt_m` ออก) | dashboard Mission, `aq_coverage` | 0004 |
| `aq_save_flown_paths` | `p_mission uuid`, `p_min_alt_m real` (2) | INTEGER (จำนวนแถว) | เก็บผล `aq_flown_paths` ลง `flight_paths` ชนิด `flown` version ถัดไป | เรียกเองหลังจบภารกิจ | 0004 |
| `aq_coverage` | `p_area uuid`, `p_source text` (`'flown'`), `p_swath_m` (0), `p_min_alt_m` (2) | `area_m2`, `covered_m2`, `coverage_pct`, `cells_total`, `cells_covered` | % พื้นที่ที่ครอบคลุม: ช่องกริดที่แนวบินผ่าน (หรือแนวกว้าง `p_swath_m`) · source `flown` = telemetry สด, `planned` / `saved` = version ล่าสุดใน `flight_paths` · หลายลำผ่านช่องเดียวกันนับครั้งเดียว | dashboard Mission, benchmark (F4) | 0004 |
| `aq_merge_survivors` | `p_mission uuid`, `p_radius_m` (3.0) | INTEGER (จำนวนที่รวม) | จับ `survivors` ที่ยังไม่มี `person_id` (เก่าสุดก่อน) เข้าคนที่ใกล้สุดในระยะ ไม่มี = สร้าง `persons` ใหม่ · ตำแหน่งหมุด = ค่าเฉลี่ยถ่วง conf · lock ต่อภารกิจ | ingestion หลังเขียน survivors (`MERGE_RADIUS_M`) | 0005 |
| `aq_remerge_survivors` | `p_mission uuid`, `p_radius_m` (3.0) | INTEGER | ล้าง `person_id` + ลบ `persons` ของภารกิจ แล้วรวมใหม่ (สถานะที่ผู้ควบคุมตั้งไว้หาย) | เรียกเอง เช่นลองรัศมีอื่น | 0005 |
| `aq_prune_ingest_rejects` | `p_keep interval` (30 วัน) | BIGINT (จำนวนที่ลบ) | ลบ `ingest_rejects` ที่เก่ากว่า `p_keep` | เรียกเอง (ยังไม่มี cron) | 0003 |
| `aq_current_area` | `p_mission uuid` | UUID | `search_area` ที่ใหม่ที่สุดของภารกิจ | dashboard Mission | 0007 |
| `aq_cell_coverage` | `p_area uuid`, `p_swath_m` (0), `p_min_alt_m` (2) | ตาราง `cell_row`, `cell_col`, `lat`, `lon`, `covered` | ทุกช่องกริดพร้อมจุดกลางและว่าบินผ่านแล้วหรือยัง (กฎเดียวกับ `aq_coverage` source flown) | dashboard Mission (แผนที่ coverage, D2) | 0007 |
| `aq_drone_status` | `p_mission uuid`, `p_window interval` (1 ชม.) | ตาราง `drone_id`, `ts`, `lat`, `lon`, `alt_rel`, `battery_pct`, `battery_voltage`, `nav_state`, `armed`, `speed_ms`, `age_s` + (0009) `landed`, `gps_fix_type`, `gps_satellites`, `health_ok`, `failsafe_flags` + (0010) `cpu_load`, `mission_seq`, `mission_total`, `mission_finished` | แถวล่าสุดต่อลำในภารกิจ (ใช้ PK จึงเร็ว) · `battery_pct` 0–100 · `age_s` = วินาทีตั้งแต่ `received_at` | dashboard Mission (D1) | 0007, เพิ่มคอลัมน์ 0009 / 0010 |
| `aquarius_hypertable` | `tbl regclass`, `retention text` | void | ทำตารางของ Telegraf ให้เป็น hypertable + index + retention | Telegraf ตอนสร้างตาราง | init script (compose) |

## Index และอายุข้อมูล

ตารางของ pipeline ไม่มีอะไรถูกลบอัตโนมัติ เพราะสรุปผลหลังภารกิจ (E4) และ benchmark (F4) ต้องใช้ทั้งภารกิจ · index นอกจาก PK / UNIQUE อยู่ใน 0003 เว้นที่ระบุ

| ตาราง | index เพิ่ม | อายุข้อมูล |
| --- | --- | --- |
| `telemetry` | GiST `geom` · `(mission_id, ts DESC)` · `(received_at DESC)` (0008) · ของ hypertable บน `ts` | บีบอัด chunk ที่เก่ากว่า 7 วัน (ยัง query / insert ได้) ไม่ลบ |
| `detections` | `(ts DESC)` | เก็บตลอด |
| `survivors` | GiST `geom` · `(mission_id, ts DESC)` · `(person_id)` · `(mission_id, ts) WHERE person_id IS NULL` (0005, หาตัวที่ยังไม่รวม) | เก็บตลอด |
| `persons` | `(mission_id)` · GiST `geom` · GiST `geom::geography` (0005, ให้ `ST_DWithin` เป็นเมตรใช้ index) | ลบตามภารกิจ |
| `replanning_events` | `(mission_id, ts)` | เก็บตลอด |
| `events` | `(mission_id, ts DESC)` · ของ hypertable บน `ts` (chunk 7 วัน) | เก็บตลอด |
| `search_area` | GiST `geom` · `(mission_id)` | ลบตามภารกิจ |
| `grid_cells` | GiST `geom` | ลบตามพื้นที่ |
| `flight_paths` | GiST `geom` · `(mission_id, kind)` | ลบตามภารกิจ |
| `ground_truth_persons` | GiST `geom` | เก็บตลอด (ลบภารกิจ = `mission_id` เป็น NULL) |
| `ingest_rejects` | `(received_at DESC)` | ลบด้วยมือผ่าน `aq_prune_ingest_rejects()` (30 วัน) |
| ตาราง Telegraf 9 ตาราง | `(fleet, drone, time DESC)` · ของ hypertable บน `time` | ลบหลัง `TSDB_RETENTION` (30 วัน) |

ภาพกล้องไม่อยู่ในฐานข้อมูล: Kafka `image-frames` เก็บ 1 วัน ส่วนภาพหลักฐานเป็นไฟล์ใน `Data-Pipeline/results/evidence/` ที่ `survivors.snapshot_path` ชี้ไป
