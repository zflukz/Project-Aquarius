import socket, select, time, collections
socks = {}
for p in (14540, 14541):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.bind(("0.0.0.0", p)); socks[s] = p
stats = collections.defaultdict(collections.Counter); peers = collections.defaultdict(set)
end = time.time() + 15
while time.time() < end:
    r, _, _ = select.select(list(socks), [], [], 1)
    for s in r:
        data, addr = s.recvfrom(4096); p = socks[s]; i = 0
        while i < len(data):
            if data[i] == 0xFD and i + 10 <= len(data):
                ln = data[i+1]; sys_, msg = data[i+5], data[i+7] | data[i+8] << 8 | data[i+9] << 16
                stats[p][(sys_, msg)] += 1; peers[p].add((addr, sys_)); i += 12 + ln + (13 if data[i+2] & 1 else 0)
            elif data[i] == 0xFE and i + 6 <= len(data):
                stats[p][(data[i+3], data[i+5])] += 1; peers[p].add((addr, data[i+3])); i += 8 + data[i+1]
            else: break
for p in (14540, 14541):
    print(f"port {p}: senders {sorted(peers[p])}")
    sysids = sorted({k[0] for k in stats[p]})
    for sid in sysids:
        hb = stats[p][(sid, 0)]; home = stats[p][(sid, 242)]; gpos = stats[p][(sid, 33)]
        print(f"   sysid {sid}: heartbeat {hb}, GLOBAL_POSITION_INT {gpos}, HOME_POSITION {home}, total {sum(v for k,v in stats[p].items() if k[0]==sid)}")
