#!/usr/bin/env bash
# Bring up a throwaway Ubuntu/KVM VM with Docker and run the staging DICOM
# end-to-end suite INSIDE it. The multi-host DICOM network (pacs/proxy/worker)
# lives in the VM because the host has no Docker. The repository is shared into
# the guest over 9p; the guest writes results back to staging/.data/ (gitignored),
# which the host reads — no SSH, no port forwarding.
#
# Usage:  bash staging/vm/run.sh            (downloads the cloud image on first run)
# Env:    WORK=<dir>  TIMEOUT=<seconds>
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${WORK:-/tmp/orthanc-proxy-vm}"
TIMEOUT="${TIMEOUT:-1500}"
IMG_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"

BASE="$WORK/noble.img"
OVERLAY="$WORK/overlay.qcow2"
SEED="$WORK/seed.iso"
CONSOLE="$WORK/console.log"
PIDFILE="$WORK/qemu.pid"
RESULT="$REPO/staging/.data/vm-result.txt"
DONE="$REPO/staging/.data/vm-done"

mkdir -p "$WORK" "$REPO/staging/.data"
rm -f "$RESULT" "$DONE" "$CONSOLE"

# 1. base cloud image (cached across runs)
if [ ! -f "$BASE" ]; then
  echo "Downloading Ubuntu cloud image..."
  curl -fsSL "$IMG_URL" -o "$BASE"
fi

# 2. fresh overlay so the base stays pristine; grown for docker images
rm -f "$OVERLAY"
qemu-img create -f qcow2 -b "$BASE" -F qcow2 "$OVERLAY" 20G >/dev/null

# 3. cloud-init seed: install docker + test deps, mount the repo, run the suite
cat > "$WORK/meta-data" <<EOF
instance-id: orthanc-proxy-staging
local-hostname: staging
EOF
cat > "$WORK/user-data" <<'EOF'
#cloud-config
package_update: true
packages:
  - docker.io
  - docker-compose-v2
  - python3-pip
runcmd:
  - [ mkdir, -p, /repo ]
  - [ mount, -t, 9p, -o, "trans=virtio,version=9p2000.L,msize=104857600,access=any", repo, /repo ]
  - [ mkdir, -p, /repo/staging/.data ]
  - [ systemctl, enable, --now, docker ]
  - bash -lc 'pip3 install --break-system-packages -q pytest requests pydicom > /repo/staging/.data/vm-result.txt 2>&1 || true'
  - bash -lc 'python3 -c "import pydicom,sys; print(\"pydicom\", pydicom.__version__)" >> /repo/staging/.data/vm-result.txt 2>&1'
  - bash -lc 'cd /repo/staging && docker compose up -d --build >> /repo/staging/.data/vm-result.txt 2>&1 || true'
  - bash -lc 'cd /repo/staging && python3 -m pytest -q -o cache_dir=/tmp/ptc >> /repo/staging/.data/vm-result.txt 2>&1; echo "PYTEST_EXIT=$?" >> /repo/staging/.data/vm-result.txt'
  - bash -lc 'echo "--- PACS stored studies (diagnostic) ---" >> /repo/staging/.data/vm-result.txt; curl -s "http://localhost:8101/studies?expand" >> /repo/staging/.data/vm-result.txt 2>&1 || true; echo >> /repo/staging/.data/vm-result.txt'
  - bash -lc 'cd /repo/staging && echo "--- proxy logs ---" >> /repo/staging/.data/vm-result.txt && docker compose logs --tail=40 proxy >> /repo/staging/.data/vm-result.txt 2>&1 || true'
  - bash -lc 'cd /repo/staging && docker compose down -v >> /repo/staging/.data/vm-result.txt 2>&1 || true'
  - [ sync ]
  - [ touch, /repo/staging/.data/vm-done ]
EOF
cloud-localds "$SEED" "$WORK/user-data" "$WORK/meta-data"

# 4. boot headless with KVM; share the repo over 9p; user-mode NAT for pulls
echo "Booting VM (console -> $CONSOLE)..."
qemu-system-x86_64 \
  -enable-kvm -m 4096 -smp 2 \
  -drive file="$OVERLAY",if=virtio,format=qcow2 \
  -drive file="$SEED",if=virtio,format=raw \
  -fsdev local,id=repo,path="$REPO",security_model=mapped-xattr \
  -device virtio-9p-pci,fsdev=repo,mount_tag=repo \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
  -serial file:"$CONSOLE" -display none -pidfile "$PIDFILE" &
QPID=$!

# 5. wait for the guest's completion sentinel
echo "Waiting for staging suite (timeout ${TIMEOUT}s)..."
elapsed=0
while [ ! -f "$DONE" ]; do
  if ! kill -0 "$QPID" 2>/dev/null; then echo "qemu exited before finishing"; break; fi
  [ "$elapsed" -ge "$TIMEOUT" ] && { echo "TIMEOUT"; break; }
  sleep 10; elapsed=$((elapsed + 10))
done

# 6. shut the VM down
kill "$QPID" 2>/dev/null || true
wait "$QPID" 2>/dev/null || true

echo "================ staging e2e result ================"
cat "$RESULT" 2>/dev/null || echo "(no result file produced)"
echo "===================================================="
if [ -f "$DONE" ]; then
  echo "VM run completed."
else
  echo "VM run did NOT complete; inspect $CONSOLE"
  exit 1
fi
