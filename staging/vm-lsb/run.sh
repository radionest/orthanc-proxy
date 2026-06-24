#!/usr/bin/env bash
# Astra-1.7-like test environment for the plugin: a Debian 10 Buster VM (the technical base of
# Astra SE 1.7 — glibc 2.28, libpython3.7) where we install the REAL Orthanc 1.12.11 LSB binaries
# + the buster-3.7 Python plugin via deploy/install.sh, run a 3-node DICOM network as plain
# systemd-less Orthanc processes (NO Docker — like production), and run the e2e suite against it.
# This validates the actual production artifacts (LSB plugin, our clarinet_proxy.py) on the
# production OS base. (Astra-only ЗПС/МКЦ layers are not exercised here — those need a real Astra image.)
#
# Usage: bash staging/vm-lsb/run.sh
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${WORK:-/tmp/orthanc-proxy-vm-lsb}"
TIMEOUT="${TIMEOUT:-1800}"
# "generic" (not "genericcloud") — its kernel includes the 9p modules we need for the repo share.
IMG_URL="https://cloud.debian.org/images/cloud/buster/latest/debian-10-generic-amd64.qcow2"

BASE="$WORK/buster.qcow2"
OVERLAY="$WORK/overlay.qcow2"
SEED="$WORK/seed.iso"
CONSOLE="$WORK/console.log"
PIDFILE="$WORK/qemu.pid"
RESULT="$REPO/staging/.data/vm-lsb-result.txt"
DONE="$REPO/staging/.data/vm-lsb-done"

mkdir -p "$WORK" "$REPO/staging/.data"
rm -f "$RESULT" "$DONE" "$CONSOLE"

if [ ! -f "$BASE" ]; then
  echo "Downloading Debian 10 Buster cloud image (resumable)..."
  # The Debian mirror resets large downloads mid-stream; -C - resumes the .part. With -f, curl
  # returns 0 only on a fully-received transfer — break on that, then promote .part -> $BASE.
  ok=0
  for i in $(seq 1 30); do
    if curl -fL --retry 5 --retry-delay 3 --retry-all-errors -C - "$IMG_URL" -o "$BASE.part"; then
      ok=1; break
    fi
    echo "  ... $(stat -c%s "$BASE.part" 2>/dev/null || echo 0) bytes so far, resuming ($i)"; sleep 3
  done
  [ "$ok" = 1 ] || { echo "image download failed after retries"; exit 1; }
  mv "$BASE.part" "$BASE"
fi

rm -f "$OVERLAY"
qemu-img create -f qcow2 -b "$BASE" -F qcow2 "$OVERLAY" 20G >/dev/null

cat > "$WORK/meta-data" <<EOF
instance-id: orthanc-lsb-staging
local-hostname: lsb
EOF
cat > "$WORK/user-data" <<'EOF'
#cloud-config
write_files:
  - path: /root/provision.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      set -x
      R=/repo/staging/.data/vm-lsb-result.txt
      mkdir -p /repo 2>/dev/null || true
      modprobe 9p 2>/dev/null || true; modprobe 9pnet_virtio 2>/dev/null || true
      mount -t 9p -o trans=virtio,version=9p2000.L,msize=104857600,access=any repo /repo
      if ! mountpoint -q /repo; then echo "FATAL: 9p mount failed"; lsmod | grep -i 9p; exit 1; fi
      mkdir -p /repo/staging/.data
      exec > >(tee -a "$R" /dev/ttyS0) 2>&1

      echo "=== host facts (Astra 1.7 base = Buster) ==="
      . /etc/os-release; echo "ID=$ID VERSION_ID=$VERSION_ID"
      python3 --version; ldd --version | head -1
      ldconfig -p | grep -E 'libpython3\.7' || echo "libpython3.7 NOT FOUND"

      echo "=== apt via archive.debian.org (Buster is EOL) ==="
      cat > /etc/apt/sources.list <<'SRC'
      deb http://archive.debian.org/debian buster main
      deb http://archive.debian.org/debian-security buster/updates main
      SRC
      apt-get -o Acquire::Check-Valid-Until=false update
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl ca-certificates python3-pip libpython3.7

      echo "=== python test deps (Py3.7-compatible pins) ==="
      python3 -m pip install --upgrade "pip<25" >/dev/null 2>&1 || true
      python3 -m pip install "pytest==7.4.4" "requests==2.31.0" "pydicom==2.4.4"

      echo "=== install Orthanc LSB + buster-3.7 plugin via OUR install.sh ==="
      DEST=/opt/orthanc bash /repo/deploy/install.sh
      echo "--- /opt/orthanc layout ---"; find /opt/orthanc -maxdepth 2 -type f | sort

      mkdir -p /var/lib/lsb-pacs /var/lib/lsb-proxy /var/lib/lsb-worker
      CFG=/repo/staging/vm-lsb/config
      echo "=== start 3 Orthanc LSB instances (no Docker) ==="
      /opt/orthanc/bin/Orthanc "$CFG/pacs.json"   > /repo/staging/.data/lsb-pacs.log   2>&1 &
      /opt/orthanc/bin/Orthanc "$CFG/worker.json" > /repo/staging/.data/lsb-worker.log 2>&1 &
      /opt/orthanc/bin/Orthanc "$CFG/proxy.json"  > /repo/staging/.data/lsb-proxy.log  2>&1 &
      sleep 8
      echo "--- proxy /plugins (expect python + dicom-web) ---"
      curl -s http://localhost:8052/plugins; echo

      echo "=== e2e suite against the LSB stack ==="
      cd /repo/staging
      PACS_URL=http://localhost:8051 PROXY_URL=http://localhost:8052 WORKER_URL=http://localhost:8053 \
        python3 -m pytest -q -o cache_dir=/tmp/ptc \
        test_cfind.py test_cmove_worker.py test_cache_paths.py test_harness.py
      echo "PYTEST_EXIT=$?"

      echo "--- proxy log tail ---"; tail -50 /repo/staging/.data/lsb-proxy.log
      sync
      touch /repo/staging/.data/vm-lsb-done
runcmd:
  - [ bash, /root/provision.sh ]
EOF
cloud-localds "$SEED" "$WORK/user-data" "$WORK/meta-data"

echo "Booting Buster VM (console -> $CONSOLE)..."
qemu-system-x86_64 \
  -enable-kvm -m 4096 -smp 2 \
  -drive file="$OVERLAY",if=virtio,format=qcow2 \
  -drive file="$SEED",if=virtio,format=raw \
  -fsdev local,id=repo,path="$REPO",security_model=mapped-xattr \
  -device virtio-9p-pci,fsdev=repo,mount_tag=repo \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
  -serial file:"$CONSOLE" -display none -pidfile "$PIDFILE" &
QPID=$!

echo "Waiting for LSB e2e (timeout ${TIMEOUT}s)..."
elapsed=0
while [ ! -f "$DONE" ]; do
  if ! kill -0 "$QPID" 2>/dev/null; then echo "qemu exited before finishing"; break; fi
  [ "$elapsed" -ge "$TIMEOUT" ] && { echo "TIMEOUT"; break; }
  sleep 10; elapsed=$((elapsed + 10))
done

kill "$QPID" 2>/dev/null || true
wait "$QPID" 2>/dev/null || true

echo "================ LSB e2e result ================"
cat "$RESULT" 2>/dev/null || echo "(no result file)"
echo "================================================"
[ -f "$DONE" ] && echo "VM run completed." || { echo "did NOT complete; see $CONSOLE"; exit 1; }
