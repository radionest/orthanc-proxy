# vm-net — multi-machine e2e harness

Four QEMU/KVM VMs on an isolated socket-multicast LAN, exercising `clarinet_proxy.py`
across **real machine boundaries** — clients reach the PACS **only through the proxy**.

| node | base image | role |
|------|------------|------|
| `pacs` 10.0.0.10 | golden (LSB Orthanc 1.12.11, 3×1000 instances baked in) | upstream PACS `HOSPITALPACS` |
| `proxy` 10.0.0.20 | **rebuilt every run** (Buster + `deploy/install.sh`) | the proxy under test `CLARINETPROXY` |
| `clienta` 10.0.0.31 | golden (pynetdicom) | SCU `CLIENTA`, registered **only** in the proxy |
| `clientb` 10.0.0.32 | golden (pynetdicom) | SCU `CLIENTB`, registered **only** in the proxy |

```bash
bash staging/vm-net/build-golden.sh   # once (cached in WORK); FORCE_REBUILD=pacs|client|all to rebuild
bash staging/vm-net/run.sh            # boot all 4, run scenarios, assert on the host
```

Env: `WORK=<dir>` (default `/tmp/orthanc-proxy-vm-net`; put it on a **disk**, not tmpfs),
`TIMEOUT=<seconds>`, `INSTANCES_PER_STUDY=<n>` (must match between build and run).

## How it works

- **Rootless networking.** All four VMs share one isolated L2 segment via QEMU socket
  multicast (`-netdev socket,mcast=230.0.0.1:1234`); each also has a user-mode NAT NIC for
  package pulls. No bridge, no TAP, no root.
- **Static IPs by MAC.** cloud-init's v2 network config does not render on Debian Buster, and
  golden images carry a stale `/etc/network/interfaces`, so each role script assigns its LAN IP
  (and DHCPs the NAT NIC) directly from `/sys/class/net` by MAC. Nodes are addressed by **IP**,
  not hostname, to avoid `/etc/hosts` resolution issues on golden reboots.
- **PACS = LSB Orthanc.** The distro Buster Orthanc (1.5.x) segfaults in `libdcmnet` on the first
  DICOM association, so the PACS runs the same stable LSB Orthanc 1.12.11 as the proxy (no plugins
  loaded). Studies are imported once at golden-build time (one ZIP `POST /instances`) and baked
  into `/var/lib/orthanc/db`.
- **Coordination + results** flow through the 9p-shared `staging/.data/vm-net/` (gitignored):
  per-role JSON, barrier files, console logs, and per-node `netdiag-<node>.txt` (boot-time
  `ip addr` + reachability, kept for triage).
- **Host gate.** `run.sh` waits for `proxy-done`, then runs `test_vm_net.py` on the host
  (`uv run --with pytest pytest --noconftest …`) — it asserts over the collected JSON; it does
  not speak DICOM itself (the host is not on the mcast LAN).

## Scenarios (all asserted by `test_vm_net.py`)

- **S1** C-MOVE routing — clientA C-FIND→C-MOVE(`study1`) via the proxy; A receives all 1000
  instances, B receives none.
- **S2** AET isolation — clientA's direct association to the PACS is rejected; a spoofed `GHOST`
  AET to the proxy is rejected.
- **S3** pass-through — Cyrillic C-FIND round-trips UTF-8; a client C-STORE to the proxy is cached
  and visible via QIDO; QIDO/WADO over the proxy REST work. (The proxy has **no** C-STORE→PACS
  forwarding — it caches; see the spec.)
- **S4** concurrency, different studies — A↔`study2`, B↔`study3` fired together; each gets its own
  study in full, no cross-contamination.
- **S5** concurrency, same study — A and B both pull `study1` together; both receive it in full.
  The proxy→PACS fetch count is recorded as an observation (not asserted).
- **S6** memory/eviction — with a short TTL + low storage cap, `evict.py` deletes the TTL-expired
  studies and logs the storage-fill WARN.

## Relation to the other harnesses

`staging/vm/` tests the logic on Docker; `staging/vm-lsb/` tests the LSB artifact on one Buster
host over loopback; **this** harness adds the real multi-machine topology and client-only-via-proxy
isolation. Astra ЗПС/МКЦ/ГОСТ layers remain out of scope (need a licensed Astra image).

## Resource budget (≤ 24 GB)

proxy 4096 MB · pacs 3072 MB · clientA/B 1024 MB each ≈ 9 GB of guests; the rest is qemu/9p/page-cache
headroom. Eviction is tested via small config thresholds, not by exhausting RAM.

## Notes / gotchas

- **`WORK` must be disk-backed**, not tmpfs — the qcow2 overlays would otherwise consume RAM and
  compete with the guests.
- The PACS golden import runs once and is cached; instance ingestion is bulk (one ZIP POST).
- First boot of each golden VM spends a few minutes in a failed `ifupdown` "Raise network
  interfaces" step before the role script assigns IPs — expected, it recovers.
