#!/usr/bin/env python3
"""mavlink_mqtt.py — MAVLink telemetry of every drone -> MQTT (docker/communication_plan.md §4.2, §5).

One UDP socket for all drones: PX4 instance i opens a 3rd MAVLink link 14740+i -> 127.0.0.1:14700
(docker/px4-sim/entrypoint.sh, scripts/run_sim_native.sh); drones are told apart by sysid (= i + 1).
pymavlink, not MAVSDK: no mavsdk_server per drone, and the control ports 14540+i stay untouched.

Topics  aquarius/<FLEET>/drone/<i>/<stream>, payload one flat JSON object {"t": <unix ms>, ...}.
  online    {online}                                          1 Hz, retained (0 after OFFLINE_S without HEARTBEAT)
  status    {armed, mode, landed, landed_state, system_status} 1 Hz + on change, retained
  position  {lat, lon, alt_amsl, alt_rel, vn, ve, vd, speed, climb, hdg}   POS_RATE Hz
  attitude  {roll, pitch, yaw}  (deg)                          ATT_RATE Hz
  battery   {pct, voltage, current}                            1 Hz, retained
  gps       {fix_type, sats, eph, epv}                         1 Hz
  health    {gyro_ok, accel_ok, mag_ok, baro_ok, gps_ok, all_ok, load}   1 Hz
  mission   {seq, total}  (total 0 = no mission)               on change
  event     {severity, text}                                   STATUSTEXT
  aquarius/<FLEET>/bridge/online   1 / 0 (MQTT last-will)
Booleans are sent as 1 / 0 so Telegraf stores them as numbers. No commands over MQTT (§5.2).
Sends a 1 Hz GCS HEARTBEAT (PX4 forwards STATUSTEXT only to GCS links) and SET_MESSAGE_INTERVAL, nothing else.

Env: MAVLINK_IN=udpin:0.0.0.0:14700  MQTT_URL=mqtt://127.0.0.1:1883  MQTT_USER / MQTT_PASS
     FLEET=sim  POS_RATE=10  ATT_RATE=10  OFFLINE_S=3
"""
import json
import math
import os
import signal
import sys
import time
from urllib.parse import urlparse

os.environ.setdefault('MAVLINK20', '1')
from pymavlink import mavutil  # noqa: E402
import paho.mqtt.client as mqtt  # noqa: E402

MAVLINK_IN = os.environ.get('MAVLINK_IN', 'udpin:0.0.0.0:14700')
MQTT_URL = urlparse(os.environ.get('MQTT_URL') or 'mqtt://127.0.0.1:1883')
FLEET = os.environ.get('FLEET', 'sim')
POS_RATE = float(os.environ.get('POS_RATE') or 10)
ATT_RATE = float(os.environ.get('ATT_RATE') or 10)
OFFLINE_S = float(os.environ.get('OFFLINE_S') or 3)
PREFIX = f'aquarius/{FLEET}'

M = mavutil.mavlink
# MAVLink streams asked from PX4 per drone (message id -> Hz); the link's "normal" mode sends the rest
INTERVALS = {M.MAVLINK_MSG_ID_GLOBAL_POSITION_INT: POS_RATE, M.MAVLINK_MSG_ID_ATTITUDE: ATT_RATE,
             M.MAVLINK_MSG_ID_SYS_STATUS: 1, M.MAVLINK_MSG_ID_GPS_RAW_INT: 1,
             M.MAVLINK_MSG_ID_EXTENDED_SYS_STATE: 1, M.MAVLINK_MSG_ID_MISSION_CURRENT: 1}
LANDED = {0: 'unknown', 1: 'on_ground', 2: 'in_air', 3: 'takeoff', 4: 'landing'}
SYSTEM_STATUS = {0: 'uninit', 1: 'boot', 2: 'calibrating', 3: 'standby', 4: 'active',
                 5: 'critical', 6: 'emergency', 7: 'poweroff', 8: 'terminating'}
SENSORS = {'gyro_ok': M.MAV_SYS_STATUS_SENSOR_3D_GYRO, 'accel_ok': M.MAV_SYS_STATUS_SENSOR_3D_ACCEL,
           'mag_ok': M.MAV_SYS_STATUS_SENSOR_3D_MAG, 'baro_ok': M.MAV_SYS_STATUS_SENSOR_ABSOLUTE_PRESSURE,
           'gps_ok': M.MAV_SYS_STATUS_SENSOR_GPS}


def now_ms():
    return int(time.time() * 1000)


class Drone:
    def __init__(self, i):
        self.i = i
        self.last_hb = 0.0
        self.online = False
        self.status = {}
        self.status_last = None   # last published status
        self.status_sent = 0.0
        self.mission = None
        self.text_parts = []      # STATUSTEXT chunks
        self.last_pub = {}        # stream -> monotonic time (rate limit)


class Bridge:
    def __init__(self):
        self.drones = {}
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f'aquarius-mavlink-{FLEET}')
        if os.environ.get('MQTT_USER'):
            self.mq.username_pw_set(os.environ['MQTT_USER'], os.environ.get('MQTT_PASS'))
        self.mq.will_set(f'{PREFIX}/bridge/online', '0', qos=1, retain=True)
        self.mq.on_connect = lambda c, u, f, rc, p: (
            print(f'MQTT connected {MQTT_URL.hostname}:{MQTT_URL.port or 1883} ({rc})', flush=True),
            c.publish(f'{PREFIX}/bridge/online', '1', qos=1, retain=True))
        self.mq.on_disconnect = lambda c, u, f, rc, p: print(f'MQTT disconnected ({rc}), retrying', flush=True)
        self.mq.reconnect_delay_set(1, 10)
        self.mq.connect_async(MQTT_URL.hostname or '127.0.0.1', MQTT_URL.port or 1883, keepalive=15)
        self.mq.loop_start()
        self.mav = mavutil.mavlink_connection(MAVLINK_IN, source_system=246, source_component=191)
        print(f'MAVLink {MAVLINK_IN} -> MQTT {PREFIX}/drone/<i>/<stream>', flush=True)

    def pub(self, d, stream, data, qos=0, retain=False, rate=None):
        if rate:
            t = time.monotonic()
            if t - d.last_pub.get(stream, 0) < 0.8 / rate:   # 0.8: PX4's own timing jitters
                return
            d.last_pub[stream] = t
        self.mq.publish(f'{PREFIX}/drone/{d.i}/{stream}', json.dumps({'t': now_ms(), **data}),
                        qos=qos, retain=retain)

    def request_streams(self, d):   # sent to every PX4 that talked to us; only sysid d.i + 1 acts on it
        for msg_id, hz in INTERVALS.items():
            self.mav.mav.command_long_send(d.i + 1, 1, M.MAV_CMD_SET_MESSAGE_INTERVAL, 0,
                                           msg_id, 1e6 / hz, 0, 0, 0, 0, 0)

    def set_online(self, d, online):   # on change; check_offline() repeats it every second
        if online == d.online:
            return
        print(f'drone{d.i} (sysid {d.i + 1}) {"online" if online else "OFFLINE"}', flush=True)
        d.online = online
        d.last_pub['online'] = time.monotonic()
        self.pub(d, 'online', {'online': int(online)}, qos=1, retain=True)

    def handle(self, msg):
        sysid, compid = msg.get_srcSystem(), msg.get_srcComponent()
        if sysid < 1 or compid != M.MAV_COMP_ID_AUTOPILOT1:
            return
        d = self.drones.get(sysid)
        if d is None:
            d = self.drones[sysid] = Drone(sysid - 1)
        t = msg.get_type()

        if t == 'HEARTBEAT':
            if not d.online:
                self.request_streams(d)   # again after a PX4 restart
            d.last_hb = time.monotonic()
            self.set_online(d, True)
            d.status.update(armed=int(bool(msg.base_mode & M.MAV_MODE_FLAG_SAFETY_ARMED)),
                            mode=mavutil.mode_string_v10(msg),
                            system_status=SYSTEM_STATUS.get(msg.system_status, str(msg.system_status)))
            self.send_status(d)
        elif t == 'EXTENDED_SYS_STATE':
            d.status.update(landed_state=msg.landed_state, landed=LANDED.get(msg.landed_state, 'unknown'))
        elif t == 'GLOBAL_POSITION_INT':
            vn, ve, vd = msg.vx / 100, msg.vy / 100, msg.vz / 100
            data = dict(lat=msg.lat / 1e7, lon=msg.lon / 1e7, alt_amsl=msg.alt / 1000,
                        alt_rel=msg.relative_alt / 1000, vn=vn, ve=ve, vd=vd,
                        speed=round(math.hypot(vn, ve), 3), climb=-vd)
            if msg.hdg != 65535:
                data['hdg'] = msg.hdg / 100
            self.pub(d, 'position', data, rate=POS_RATE)
        elif t == 'ATTITUDE':
            self.pub(d, 'attitude', dict(roll=math.degrees(msg.roll), pitch=math.degrees(msg.pitch),
                                         yaw=math.degrees(msg.yaw) % 360), rate=ATT_RATE)
        elif t == 'SYS_STATUS':
            data = {}
            if msg.battery_remaining >= 0:
                data['pct'] = msg.battery_remaining
            if msg.voltage_battery != 65535:
                data['voltage'] = msg.voltage_battery / 1000
            if msg.current_battery >= 0:
                data['current'] = msg.current_battery / 100
            if data:
                self.pub(d, 'battery', data, retain=True, rate=1)
            health = {name: int(bool(msg.onboard_control_sensors_health & bit))
                      for name, bit in SENSORS.items() if msg.onboard_control_sensors_present & bit}
            enabled = msg.onboard_control_sensors_enabled & msg.onboard_control_sensors_present
            health['all_ok'] = int(msg.onboard_control_sensors_health & enabled == enabled)
            health['load'] = msg.load / 10
            self.pub(d, 'health', health, rate=1)
        elif t == 'GPS_RAW_INT':
            data = dict(fix_type=msg.fix_type, sats=msg.satellites_visible)
            if msg.eph != 65535:
                data['eph'] = msg.eph / 100
            if msg.epv != 65535:
                data['epv'] = msg.epv / 100
            self.pub(d, 'gps', data, rate=1)
        elif t == 'MISSION_CURRENT':
            total = getattr(msg, 'total', 0)
            m = (msg.seq, 0 if total == 65535 else total)   # UINT16_MAX = no mission
            if m != d.mission:
                d.mission = m
                self.pub(d, 'mission', dict(seq=m[0], total=m[1]), qos=1)
        elif t == 'STATUSTEXT':
            text = msg.text if isinstance(msg.text, str) else msg.text.decode(errors='replace')
            text = text.rstrip('\x00')
            # long texts come in 50-char chunks with the same id; the last chunk is shorter
            if getattr(msg, 'id', 0):
                d.text_parts.append(text)
                if len(text) == 50:
                    return
                text, d.text_parts = ''.join(d.text_parts), []
            text = text.strip()
            print(f'drone{d.i}: [{msg.severity}] {text}', flush=True)
            self.pub(d, 'event', dict(severity=msg.severity, text=text), qos=1)

    def send_status(self, d, force=False):   # 1 Hz + on change
        t = time.monotonic()
        if d.status != d.status_last or force or t - d.status_sent >= 1.0:
            d.status_last = dict(d.status)
            d.status_sent = t
            self.pub(d, 'status', d.status, qos=1, retain=True)

    def check_offline(self):
        t = time.monotonic()
        for d in self.drones.values():
            if d.online and t - d.last_hb > OFFLINE_S:
                self.set_online(d, False)
            # 1 Hz either way: Grafana treats "no message for 5 s" as offline (bridge gone)
            self.pub(d, 'online', {'online': int(d.online)}, qos=1, retain=True, rate=1)

    def run(self):
        next_check = 0.0
        while True:
            msg = self.mav.recv_match(blocking=True, timeout=0.5)
            if msg is not None and msg.get_type() != 'BAD_DATA':
                self.handle(msg)
            if time.monotonic() >= next_check:
                next_check = time.monotonic() + 1.0
                self.check_offline()
                # GCS heartbeat: PX4 sends STATUSTEXT only on links with a GCS (streams/STATUSTEXT.hpp)
                self.mav.mav.heartbeat_send(M.MAV_TYPE_GCS, M.MAV_AUTOPILOT_INVALID, 0, 0, M.MAV_STATE_ACTIVE)

    def stop(self, *_):
        for d in self.drones.values():
            self.pub(d, 'online', {'online': 0}, qos=1, retain=True)
        self.mq.publish(f'{PREFIX}/bridge/online', '0', qos=1, retain=True).wait_for_publish(2)
        self.mq.loop_stop()
        sys.exit(0)


if __name__ == '__main__':
    b = Bridge()
    signal.signal(signal.SIGTERM, b.stop)
    signal.signal(signal.SIGINT, b.stop)
    b.run()
