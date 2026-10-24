# Aquarius — Data Pipeline (Phase B + C)

โค้ดของ Phase B ใน Data Task Planning: ข้อมูลจากโดรน → ROS 2 → **ROS 2–Kafka Bridge** → Kafka → **DB Ingestion** → TimescaleDB + PostGIS → Grafana
ทุก message เป็นไปตาม Phase A (A2 schema, A3 topic, A4 timestamp)

| งาน | อยู่ที่ | สถานะ |
|---|---|---|
| B1 Docker Compose | `docker-compose.yml`, `kafka/`, `db/init/`, `grafana/` | พร้อมรัน |
| B2 ROS 2–Kafka Bridge | `ros2_ws/src/aquarius_bridge/` | พร้อม build ใน ROS 2 Jazzy |
| B3 DB Ingestion | `ingestion/` (รันเป็น container อัตโนมัติ) | พร้อมรัน |
| เครื่องมือทดสอบ | `tools/` | โดรนปลอม, วัด loss/latency, ตั้งค่า DDS Discovery Server |
| JSON Schema | `schemas/` | ใช้ร่วมกันทั้งทีม (AI, Georef, Control) |
| Phase C ฐานข้อมูล (C1–C5) | `db/migrations/`, `db/tests/`, `tools/phase_c_check.sh` | ดูข้อ 9 |

```
aquarius-data/
├── docker-compose.yml        Kafka (KRaft) · TimescaleDB+PostGIS · Grafana · Kafka UI · ingestion
├── .env.example              ค่าที่ต้องตั้ง (IP ของ Kafka, จำนวน partition)
├── kafka/create-topics.sh    สร้าง 5 topic ตาม A3
├── db/init/                  สร้าง extension (TimescaleDB, PostGIS) ตอนฐานข้อมูลยังว่าง
├── db/migrations/            ตาราง / index / ฟังก์ชันทั้งหมด เรียงตามเลข (service db-migrate รันให้)
├── db/tests/                 ชุดตรวจ Phase C
├── grafana/provisioning/     เชื่อม Grafana กับฐานข้อมูลให้อัตโนมัติ
├── schemas/*.schema.json     สัญญาข้อมูล A2 ในรูปที่โปรแกรมตรวจได้
├── ros2_ws/src/aquarius_bridge/
├── ingestion/
└── tools/
    ├── sim_producer.py       โดรนปลอม ส่งข้อมูลเข้า Kafka โดยไม่ต้องมี ROS/PX4
    ├── pipeline_stats.py     วัด loss, latency, ขนาดภาพ ต่อโดรน
    └── dds/                  ตั้งค่า DDS Discovery Server + จำลอง Wi-Fi แย่
```

---

## 1. เปิดระบบฝั่ง ground station (B1)

ต้องมี Docker + Docker Compose v2

```bash
cp .env.example .env          # KAFKA_PARTITIONS=10 (= โดรนสูงสุด 10 ลำ) ตั้งไว้แล้ว
docker compose up -d --build
docker compose ps             # kafka, db, grafana, ingestion = running · kafka-init = exited (0)
docker compose logs kafka-init   # ควรเห็น 5 topic
```

| หน้า | URL | |
|---|---|---|
| Kafka UI | http://localhost:8081 | ดู topic / message |
| Grafana | http://localhost:3000 | admin / ค่าใน `.env` (datasource "Aquarius DB" ตั้งไว้แล้ว) |
| PostgreSQL | `localhost:5433` | `psql postgresql://aquarius:aquarius@localhost:5433/aquarius` |

> ถ้า bridge หรือเครื่องมือรันบน **เครื่องอื่น** ให้ตั้ง `KAFKA_ADVERTISED_HOST=<IP ของ ground station>` ใน `.env` แล้ว `docker compose up -d` ใหม่

## 2. ทดสอบทั้ง pipeline ด้วยโดรนปลอม (ยังไม่ต้องมี PX4)

```bash
pip install confluent-kafka pillow
python3 tools/sim_producer.py --drones 10           # ปล่อยรันไว้ (ลองที่ 3 ลำก่อนก็ได้)
python3 tools/pipeline_stats.py --seconds 30        # อีก terminal
```

ตรวจในฐานข้อมูล:

```sql
SELECT drone_id, count(*), max(ts) AS last_sample,
       round(avg(extract(epoch FROM received_at - ts)) * 1000) AS avg_delay_ms
FROM telemetry WHERE ts > now() - interval '1 minute' GROUP BY drone_id ORDER BY drone_id;
```

## 3. ROS 2–Kafka Bridge กับ PX4 SITL (B2)

```bash
# ครั้งแรก
pip install confluent-kafka          # (opencv ใช้เฉพาะ image_input:=raw)
cd ros2_ws/src && git clone https://github.com/PX4/px4_msgs.git   # ใช้ branch ให้ตรงกับเวอร์ชัน PX4
cd .. && colcon build --packages-select px4_msgs aquarius_bridge && source install/setup.bash

# ทุกครั้ง: source tools/dds/env_discovery_server.sh <IP ground> ในทุก terminal (ข้อ 5)
# แล้วเปิด PX4 SITL หลายลำ + MicroXRCEAgent udp4 -p 8888 + กล้อง
ros2 topic list | grep -E "fmu/out|camera"          # ดูชื่อจริง แล้วแก้ config/bridge.yaml ให้ตรง
ros2 launch aquarius_bridge bridge.launch.py use_sim_time:=true
```

สิ่งที่ต้องตั้งใน `config/bridge.yaml`:

- `drones` — ตั้งไว้ครบ 10 ลำ หนึ่งบรรทัดต่อลำ `"uav1,/px4_1,/uav1/camera/image/compressed"` ลำที่ไม่ได้เปิดไม่มีผลอะไร (PX4 instance 0 อาจไม่มี namespace ให้ใส่ `/`)
- `image_input` — `compressed` ถ้ากล้องส่ง `CompressedImage` อยู่แล้ว · `raw` ถ้าเอา `sensor_msgs/Image` จาก `ros_gz_bridge` มาตรงๆ (bridge จะบีบเป็น JPEG ให้)
  หรือแปลงเองด้วย `ros2 run image_transport republish raw compressed --ros-args -r in:=/uav1/camera/image -r out/compressed:=/uav1/camera/image/compressed`
- ชื่อ topic ของ PX4 — บางเวอร์ชันมี suffix เช่น `vehicle_status_v1` ให้แก้ `topic_status`

Log ของ bridge ทุก 10 วินาทีจะบอกจำนวนที่ส่ง, ที่ทิ้ง, error ของ Kafka และ offset เวลาของ PX4 ต่อลำ

### เรื่องเวลา (A4) ที่ bridge จัดการให้

- **telemetry**: PX4 ใส่เวลาเป็น µs นับจากบูต bridge ประมาณ offset ระหว่างนาฬิกา PX4 กับ ROS โดยเก็บค่า `เวลารับ − เวลา PX4` ที่ **น้อยที่สุด** ในช่วงล่าสุด (ความหน่วงทำให้ค่านี้มากขึ้นเท่านั้น ค่าน้อยสุดจึงใกล้ offset จริงที่สุด) แล้วแปลง `timestamp_sample` ของตำแหน่งเป็นเวลา ROS → `ts_us` คือเวลาที่วัดตำแหน่ง ไม่ใช่เวลาที่ได้รับ
  ถ้าทดลองแล้วยืนยันได้ว่า PX4 ส่งเวลาเป็นนาฬิกา ROS อยู่แล้ว ตั้ง `px4_time_mode: already_ros_time`
- **ภาพ**: ใช้ `header.stamp` ของภาพ (เวลาถ่าย)
- **Simulation**: sim time เริ่มจาก ~0 bridge บวกค่าคงที่ (เวลาจริง − sim time ณ ตอนเริ่ม) ให้ทุก message เท่ากัน ภาพกับ telemetry จึงอยู่บนนาฬิกาเดียวกันเสมอ
  ⚠ ถ้า Gazebo วิ่งช้ากว่าเวลาจริง (real-time factor < 1) `ts_us` จะค่อยๆ ห่างจากนาฬิกาจริง — latency ที่วัดใน sim จึงเชื่อได้เฉพาะตอน RTF ≈ 1

## 4. DB Ingestion (B3)

รันเป็น service `ingestion` ใน compose แล้ว ไม่ต้องทำอะไรเพิ่ม

- อ่าน `telemetry`, `detections`, `survivors`, `replanning-events` (consumer group `db-ingestion`)
- ตรวจทุก message กับ `schemas/` — message ที่ผิดไม่ทิ้งเงียบๆ แต่ลงตาราง `ingest_rejects` พร้อมเหตุผล
- insert ทีละ batch (สูงสุด 500 message หรือรอ 1 วินาที) ในหนึ่ง transaction แล้วค่อย commit offset ของ Kafka
- **กันข้อมูลซ้ำ**: ทุกตารางมี primary key ตามธรรมชาติ (`telemetry` = drone_id + ts) และ insert แบบ `ON CONFLICT DO NOTHING` ถ้า service ตายกลางทาง batch เดิมจะถูกอ่านซ้ำแต่ไม่เกิดแถวซ้ำ
- ถ้าฐานข้อมูลมีปัญหา service จะออกแล้ว Docker restart ให้ อ่านต่อจาก offset ที่ commit ล่าสุด

```bash
docker compose logs -f ingestion                       # [stats 10s] telemetry=... rejected=...
psql ... -c "SELECT topic, reason, count(*) FROM ingest_rejects GROUP BY 1,2;"
```

## 5. ROS 2 ข้าม Wi-Fi ด้วย DDS + Discovery Server

ทีมเลือกใช้ **DDS (Fast DDS) แบบ Discovery Server** สำหรับช่วงโดรน ↔ ground station
DDS ปกติหากันด้วย multicast ซึ่งบน Wi-Fi หายบ่อย และปริมาณข้อความ discovery โตประมาณ N² เมื่อมีโดรน 10 ลำ
Discovery Server ให้ทุก node ลงทะเบียนกับ server ตัวเดียวบน ground station ด้วย unicast ส่วนข้อมูลยังวิ่งเป็น DDS ปกติ

```bash
# ground station (ครั้งเดียว ปล่อยรันตลอดภารกิจ)
tools/dds/start_discovery_server.sh
# ทุก terminal บนทุกเครื่องที่รัน node ของ ROS 2 หรือ MicroXRCEAgent
source tools/dds/env_discovery_server.sh <IP ของ ground station>
```

ถ้าลืม source ใน terminal ใด node นั้นจะกลับไปใช้ multicast และมองไม่เห็นตัวอื่น (อาการ: `ros2 topic list` ไม่เห็น topic ของ PX4)

### ทดสอบว่ารับโดรน 10 ลำบน Wi-Fi ไหว

ให้เหมือนจริง: **เครื่อง A** รัน PX4 SITL + Gazebo + MicroXRCEAgent (แทนโดรนทุกลำ) **เครื่อง B** เป็น ground station (Discovery Server + docker compose + bridge) เชื่อมกันผ่าน **Wi-Fi**

1. เปิดโดรน 3 ลำก่อน แล้วค่อยเพิ่มเป็น 6 และ 10 ลำ บินภารกิจเดิมทุกรอบ
2. วัดทุกขั้น: `python3 tools/pipeline_stats.py --seconds 120 --csv results/dds_<n>uav_clean.csv`
3. จำลอง Wi-Fi แย่บนเครื่อง A: `sudo tools/dds/netem.sh on wlan0 5 50 20` แล้ววัดซ้ำเป็น `results/dds_<n>uav_loss5.csv` เสร็จแล้ว `sudo tools/dds/netem.sh off wlan0`
4. จดเวลาที่โดรนลำใหม่ใช้จนข้อมูลขึ้นใน Kafka UI (discovery time) และลองปิด–เปิด Discovery Server กลางภารกิจ

เกณฑ์: telemetry loss ต่ำกว่า 5% ที่ 10 ลำในสภาพ Wi-Fi แย่ ถ้าไม่ผ่าน ให้ลดภาพ (fps หรือความละเอียด) ก่อน เพราะภาพคือแบนด์วิดท์เกือบทั้งหมด

### แบนด์วิดท์ที่ 10 ลำ

| ข้อมูล | ต่อลำ | 10 ลำ |
|---|---|---|
| telemetry 10 Hz × ~300 B | ~3 KB/s | ~30 KB/s |
| ภาพ 2 fps × ~165 KB (1280×720, q80) | ~330 KB/s | ~3.3 MB/s (~26 Mbit/s) |

ภาพ 10 ลำต้องใช้ Wi-Fi ราว 26 Mbit/s ต่อเนื่อง ซึ่งเกินกำลังของลิงก์ระยะไกลส่วนใหญ่ ถ้าวัดแล้วไม่พอ ให้ลด `max_image_fps` หรือความละเอียดของกล้อง
Gazebo ที่มีกล้อง 10 ตัวก็ใช้ CPU/GPU สูง ถ้า RTF ต่ำกว่า 1 ให้ลดความละเอียดกล้องตอนทดสอบ

## 6. รัน unit test

```bash
python3 -m pytest ros2_ws/src/aquarius_bridge/test ingestion/tests tools   # หรือ python3 -m unittest
```

ทดสอบ logic ที่ไม่ต้องใช้ Kafka/ROS: การสร้าง telemetry ตาม schema, การแปลงเวลา, ขนาด JPEG, rate limit ภาพ, การตรวจ/แปลง message เป็นแถว, การคำนวณ loss

## 7. Phase B เสร็จเมื่อ

- [ ] `docker compose up` แล้วทุก service ทำงาน และมี 5 topic (B1)
- [ ] ข้อมูลจากโดรนจำลอง 3 ลำเข้า Kafka ครบทุก topic — `pipeline_stats.py` เห็น 3 ลำ, telemetry ≈ 10 Hz, ภาพ ≈ 2 fps (B2)
- [ ] เพิ่มเป็น 10 ลำแล้ว telemetry loss < 5% ผ่าน Wi-Fi ที่ใช้ Discovery Server (ข้อ 5)
- [ ] telemetry เข้าฐานข้อมูลต่อเนื่องโดยไม่ซ้ำ — restart `ingestion` กลางทางแล้ว `SELECT drone_id, ts, count(*) FROM telemetry GROUP BY 1,2 HAVING count(*) > 1` ได้ 0 แถว (B3)
- [ ] วัดขนาด JPEG จริงจากกล้อง Gazebo (`image_max_kb` < 2048) — ปิดข้อค้างของ A3
- [x] ยืนยันชนิด timestamp ของ PX4 จาก log `px4 offset` — ปิดข้อค้างของ A4 (เกณฑ์ ±100 ms, SITL ผ่าน 100%)

## 8. ชุดทดสอบที่เหลือของ Phase B (ทำตามลำดับ)

| # | ทดสอบ | คำสั่ง | ผ่านเมื่อ |
|---|---|---|---|
| 1 | กันข้อมูลซ้ำ (B3) | `tools/check_dedupe.sh` | `PASS: full replay produced no duplicates` |
| 2 | โดรน 3 ลำพร้อมกล้อง (B2) | `tools/sitl/start_swarm.sh 3` | เห็น `/uav_1 … /uav_3/fmu/out/vehicle_attitude` |
| 3 | กล้อง Gazebo → ROS 2 | `python3 tools/sitl/make_cam_bridge.py` | มี `/uav1..3/camera/image` |
| 4 | bridge แบบ SITL + กล้อง | ดูด้านล่าง | log: `image-frames=60 telemetry=300` ต่อ 10 วินาที |
| 5 | เวลาภาพ = เวลา telemetry (A4) | `python3 tools/check_time_sync.py --seconds 30` | ทุกลำ `PASS` (มี telemetry ก่อน-หลังทุกภาพ ห่างไม่เกิน ±100 ms ≥ 90% ของภาพ, clock_gap < 250 ms) |
| 6 | ตรวจรวม Phase B | `tools/phase_b_check.sh` | `0 failed` |
| 7 | DDS ข้าม Wi-Fi 3 → 10 ลำ | `tools/dds/measure_round.sh <label>` | loss < 5 % ทุกลำ |

ขั้นที่ 2–4 ใช้ 4 แท็บ (ต้องมี Docker รันอยู่และติดตั้ง `sudo apt install python3-opencv` ครั้งเดียว):

```bash
# แท็บ 1
MicroXRCEAgent udp4 -p 8888
# แท็บ 2  (HEADLESS=1 ถ้าเครื่องช้า; MODEL=x500 ถ้าไม่ต้องการกล้อง)
tools/sitl/start_swarm.sh 3
# แท็บ 3
python3 tools/sitl/make_cam_bridge.py
# แท็บ 4
ros2 launch aquarius_bridge bridge.launch.py use_sim_time:=true \
  config:=$(ros2 pkg prefix aquarius_bridge)/share/aquarius_bridge/config/bridge_sitl.yaml
```

ปิดทุกอย่าง: `tools/sitl/stop_swarm.sh` (แก้อาการ `vehicle_imu ... timestamp error` จาก Gazebo ที่ค้าง)

> โหมด SITL + กล้องต้องใช้ `use_sim_time:=true` เพราะภาพจาก Gazebo ประทับเวลาเป็น sim time ส่วน PX4 ส่งเวลาตามนาฬิกาเครื่อง bridge จะแปลงทั้งคู่ให้อยู่นาฬิกาเดียวกัน — ข้อ 5 คือการพิสูจน์ว่าทำได้จริง

## 9. Phase C — Database Schema และ Spatial Query

### ใช้งาน

```bash
docker compose up -d --build        # db-migrate รัน migration ที่ยังไม่ได้รันให้เอง แล้ว ingestion ค่อยเริ่ม
tools/phase_c_check.sh              # ตรวจ C1–C5 บนฐานข้อมูลทดสอบชั่วคราว (ไม่แตะข้อมูลจริง)
docker compose run --rm db-migrate python -m aquarius_ingest.migrate --status   # ดูว่ารันไฟล์ไหนไปแล้ว
```

**แก้ schema ต่อจากนี้:** เพิ่มไฟล์ใหม่ `db/migrations/0006_....sql` ห้ามแก้ไฟล์เดิมที่รันไปแล้ว (runner จะเตือนว่า `changed` และไม่รันซ้ำ)

### ตาราง

| ตาราง | มีอะไร | ใครเขียน |
|---|---|---|
| `missions` | 1 แถวต่อภารกิจ: ชื่อ, สถานะ, เวลาข้อมูลแรก/ล่าสุด | ingestion สร้างเองเมื่อเจอ `mission_id` ใหม่ · ชื่อ/สถานะตั้งเอง |
| `drones` | 1 แถวต่อโดรน + เห็นล่าสุดเมื่อไร (`last_ts`) | ingestion |
| `telemetry` (hypertable) · `detections` · `survivors` · `replanning_events` | จาก Phase B | ingestion |
| `search_area` | polygon พื้นที่ค้นหา + ขนาดกริด | planner ผ่าน `aq_create_search_area()` |
| `grid_cells` | ช่องกริดสี่เหลี่ยม (ช่องขอบถูกตัดให้พอดี polygon) | `aq_make_grid()` |
| `flight_paths` | แนวบิน `planned` (จาก mCPP, มี version เมื่อ replan) และ `flown` (เก็บหลังภารกิจ) | planner / `aq_save_flown_paths()` |
| `persons` | 1 หมุดต่อผู้ประสบภัย 1 คน (รวมจากหลายเฟรม/หลายลำ) + สถานะ pending/confirmed/rejected | ingestion เรียก `aq_merge_survivors()` |
| `ground_truth_persons` | ตำแหน่งจริงของคนใน Gazebo (ใช้ใน F3) | สคริปต์โหลด world (Phase F) |
| view `survivor_pins` | หมุด + ภาพหลักฐานที่มั่นใจสุด | สำหรับ dashboard (E2/E3) |

### ฟังก์ชัน

```sql
-- สร้างพื้นที่ค้นหาจาก GeoJSON (Polygon หรือ Feature, lon/lat) พร้อมกริด 20 m
SELECT aq_create_search_area('<mission_id>', '<geojson>', 'โซน A', 20);

-- coverage % (C4): 'flown' = จาก telemetry สด · 'planned' = แผน mCPP · 'saved' = แนวบินที่เก็บไว้
SELECT * FROM aq_coverage('<area_id>');                 -- เส้นทางบินผ่านช่องไหน นับช่องนั้น
SELECT * FROM aq_coverage('<area_id>', 'flown', 30);    -- คิดความกว้างภาพกล้อง 30 m

SELECT * FROM aq_flown_paths('<mission_id>');           -- เส้นทางบินจริงต่อลำ (ใช้ใน Geomap ได้)
SELECT aq_save_flown_paths('<mission_id>');             -- เก็บเส้นทางหลังจบภารกิจ
SELECT aq_remerge_survivors('<mission_id>', 5.0);       -- ลองรวมหมุดใหม่ด้วยรัศมีอื่น
```

### ค่าที่ตั้งไว้ (ปรับได้)

| เรื่อง | ค่า | หมายเหตุ |
|---|---|---|
| ระบบพิกัด (C3) | เก็บ EPSG:4326 · คำนวณใน UTM โซนของพื้นที่ | ในไทย = 32647 ตามที่ตกลง; `aq_utm_srid()` เลือกโซนเอง เพราะ world ของ PX4 อยู่ซูริก (32632) ถ้าใช้ 32647 ตายตัวพื้นที่จะเพี้ยน |
| ขนาดกริด | 20 m | รอยืนยันกับ mCPP (Nitis) |
| รวมหมุด (C5) | ห่างกันไม่เกิน 3 m = คนเดียวกัน | `MERGE_RADIUS_M` ใน `.env` |
| อายุข้อมูล (C2) | telemetry **ไม่ลบ** · บีบอัดหลัง 7 วัน · `ingest_rejects` ลบด้วย `aq_prune_ingest_rejects()` (30 วัน) | E4/F4 ต้องใช้ข้อมูลทั้งภารกิจ |

## แก้ปัญหาที่เจอบ่อย

| อาการ | สาเหตุ / วิธีแก้ |
|---|---|
| bridge ไม่ได้ข้อมูล PX4 เลย | ชื่อ topic/namespace ไม่ตรง (`ros2 topic list`) หรือ `px4_msgs` คนละเวอร์ชันกับ PX4 |
| `ros2 topic echo` เห็น แต่ bridge ไม่เห็น | terminal ของ bridge ยังไม่ได้ `source tools/dds/env_discovery_server.sh` หรือ Discovery Server ไม่ได้รัน |
| telemetry ไม่ออกในโหมด sim | ยังไม่มี `/clock` (Gazebo ยังไม่เริ่ม หรือไม่ได้ bridge `/clock`) |
| `Message size too large` | ภาพเกิน 2 MB: ลดความละเอียด/คุณภาพ JPEG |
| เครื่องอื่นต่อ Kafka ไม่ได้ | ตั้ง `KAFKA_ADVERTISED_HOST` เป็น IP ของ ground station แล้ว `docker compose up -d` |
| `ros2 topic list` ว่างทั้งที่ node รันอยู่ | ลืม source env ของ Discovery Server ใน terminal นั้น (ต้องใช้ super client) |
| `ingestion` ไม่ขึ้น, `db-migrate` exited (1) | `docker compose logs db-migrate` บอกไฟล์ที่ล้มเหลว แก้แล้ว `docker compose up -d` |
| เปลี่ยน `KAFKA_PARTITIONS` แล้วไม่มีผล | topic มีอยู่แล้ว: `docker compose down -v` (ลบข้อมูลทั้งหมด) แล้วเปิดใหม่ |
