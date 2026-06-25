#!/bin/bash
# Runs inside the proxy VM (Buster). Installs the real LSB Orthanc + buster-3.7 plugin
# via deploy/install.sh, starts Orthanc with the vm-net proxy config, waits for both
# clients to finish, then exercises evict.py (TTL delete + fill WARN) and records proxy.json.
set -x
R=/repo/staging/.data/vm-net
DATA="$R"
BARRIER="$R/barrier"
mkdir -p "$DATA" "$BARRIER"
exec > >(tee -a "$DATA/proxy-provision.log" /dev/ttyS0) 2>&1

. /etc/os-release; echo "proxy host ID=$ID VERSION_ID=$VERSION_ID"
cat > /etc/apt/sources.list <<'SRC'
deb http://archive.debian.org/debian buster main
deb http://archive.debian.org/debian-security buster/updates main
SRC
apt-get -o Acquire::Check-Valid-Until=false update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  curl ca-certificates python3-pip libpython3.7

DEST=/opt/orthanc bash /repo/deploy/install.sh
mkdir -p /opt/clarinet
cp /repo/plugin/clarinet_proxy.py /repo/plugin/proxy_core.py /opt/clarinet/
mkdir -p /var/lib/orthanc-proxy

/opt/orthanc/bin/Orthanc /repo/staging/vm-net/config/proxy.json \
  > "$DATA/proxy-orthanc.log" 2>&1 &
ORTHANC_PID=$!
sleep 8
echo "--- proxy /plugins ---"; curl -s http://localhost:8042/plugins; echo

# wait until both clients signalled completion (max ~10 min)
for _ in $(seq 1 120); do
  [ -f "$BARRIER/ready_clienta_phases_done" ] && [ -f "$BARRIER/ready_clientb_phases_done" ] && break
  sleep 5
done

BEFORE=$(curl -s http://localhost:8042/statistics | python3 -c 'import sys,json;print(json.load(sys.stdin).get("CountStudies",0))')

# (b) fill-WARN run: nothing expires (huge TTL), tiny cap -> fill >= WARN_FILL -> WARN logged
PROXY_CORE_DIR=/opt/clarinet ORTHANC_URL=http://localhost:8042 \
  TTL_SECONDS=100000 MAX_STORAGE_MB=1 WARN_FILL=0.8 \
  python3 /repo/deploy/evict.py > "$DATA/evict-warn.log" 2>&1
WARN=$(grep -c "storage fill" "$DATA/evict-warn.log" || true)

# (a) TTL run: TTL=1, after a short wait everything is expired -> deleted
sleep 2
PROXY_CORE_DIR=/opt/clarinet ORTHANC_URL=http://localhost:8042 \
  TTL_SECONDS=1 MAX_STORAGE_MB=100000 \
  python3 /repo/deploy/evict.py > "$DATA/evict-ttl.log" 2>&1
AFTER=$(curl -s http://localhost:8042/statistics | python3 -c 'import sys,json;print(json.load(sys.stdin).get("CountStudies",0))')

python3 - "$DATA/proxy.json" "$BEFORE" "$AFTER" "$WARN" <<'PY'
import json, sys
path, before, after, warn = sys.argv[1:5]
# pacs C-MOVE association count for study1, parsed from Orthanc's own log
pacs_moves = 0
try:
    with open("/repo/staging/.data/vm-net/proxy-orthanc.log", encoding="utf-8", errors="ignore") as f:
        pacs_moves = sum(1 for ln in f if "/modalities/pacs/move" in ln)
except OSError:
    pass
json.dump({
    "role": "proxy",
    "studies_before_evict": int(before),
    "studies_after_evict": int(after),
    "fill_warn_logged": int(warn) > 0,
    "pacs_move_jobs_observed": pacs_moves,
}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
PY

kill "$ORTHANC_PID" 2>/dev/null || true
touch "$BARRIER/ready_proxy_done"
sync
touch "$DATA/proxy-done"
