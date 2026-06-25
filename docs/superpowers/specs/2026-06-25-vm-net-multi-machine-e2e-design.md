# vm-net — Multi-machine e2e test harness — Design Spec

**Status:** approved (brainstorming) · **Date:** 2026-06-25 · **Next:** implementation plan (writing-plans)

A 4-VM DICOM network that exercises `clarinet_proxy.py` across **real machine boundaries**: a PACS,
the proxy on an Astra-1.7-like host, and two SCU clients registered **only in the proxy**. Complements
the existing single-host harnesses (`staging/vm/`, `staging/vm-lsb/`); does not replace them.

---

## 1. Problem

The current harnesses run every DICOM node on **one host over loopback**:

- `staging/vm/` — `docker compose` (pacs/proxy/worker) inside one Ubuntu VM.
- `staging/vm-lsb/` — three plain Orthanc LSB processes inside one Buster VM, talking to `localhost`.

Neither exercises what production actually depends on: a client that can reach the PACS **only through
the proxy**, real cross-machine DICOM associations, C-MOVE results routed back to the requesting client,
and the proxy's cache/eviction behaviour under concurrent retrievals of **large** studies. This harness
adds that topology.

## 2. Goals (what this must prove)

| # | Scenario | Pass condition |
|---|---|---|
| S1 | C-MOVE routing to the requesting client | Client A does C-FIND→C-MOVE(`study1`, dest `CLIENTA`) **through the proxy**; A's Storage SCP receives all N instances of `study1`; B receives 0. |
| S2 | AET isolation / negative tests | A's direct association to the PACS (C-ECHO/C-FIND) is **rejected** (PACS does not know `CLIENTA`); a spoofed `GHOST` AET to the proxy is **rejected**. |
| S3 | Full pass-through | C-FIND with a Cyrillic `PatientName` returns correct UTF-8; C-STORE push client→proxy→PACS lands the instance on the PACS; QIDO/WADO over the proxy REST returns the study. |
| S4 | Concurrency — different studies | A↔`study1` and B↔`study2` fired simultaneously; both complete, each receives its own study in full, with no cross-contamination. |
| S5 | Concurrency — same study (characterization) | A and B↔`study1` fired simultaneously; **hard assert**: both receive `study1` in full. **Observation (recorded, not asserted):** proxy→PACS fetch count for the study. |
| S6 | Memory / eviction under load | With a small `TTL_SECONDS` + `MAX_STORAGE_MB`, drive retrievals then run `evict.py`; TTL-expired studies are deleted and a fill WARN is logged at the threshold. |

**Resource ceiling:** the whole stack must fit in **24 GB free RAM** (§8).

## 3. Fixed decisions (do not revisit)

| # | Decision | Value |
|---|---|---|
| D1 | Topology | 4 VMs: PACS, proxy (Astra-like), client A, client B — separate machines, real network between them. |
| D2 | Client nature | Lightweight **SCU agents** (`pynetdicom`): each is a Storage SCP **and** can issue C-FIND/C-MOVE/C-STORE. Not full Orthanc nodes. |
| D3 | Proxy image | **Rebuilt every run** from the real deploy artifacts: Debian 10 Buster base → `deploy/install.sh` → LSB Orthanc 1.12.11 + `debian-buster-python-3.7` plugin + current `clarinet_proxy.py`. Mirrors `staging/vm-lsb/`. |
| D4 | PACS & client images | **Golden** pre-baked qcow2 (built once, booted as fresh overlays). PACS golden = distro Orthanc + `orthanc-dicomweb` with the synthetic studies **baked into its storage**. Client golden = minimal base + `pynetdicom` + the agent script. |
| D5 | Inter-VM network | QEMU **socket-multicast** L2 (`-netdev socket,mcast=230.0.0.1:1234`) — rootless, no bridge/TAP. Static IPs + `/etc/hosts`. Second per-VM `-netdev user` NIC for provisioning downloads (only the proxy needs it at runtime). |
| D6 | Orchestration | Host `run.sh` boots all 4 VMs, hands out roles via cloud-init + the 9p share, waits for a global sentinel. Assertions run **host-side** over structured JSON the VMs write back (the host is not on the mcast LAN, so it cannot speak DICOM itself). |
| D7 | Concurrency coordination | Barrier files on the 9p share (`ready_A`/`ready_B`/`go`): clients signal readiness, poll for `go`, fire simultaneously. No SSH, no extra channel. |
| D8 | Scope | `vm-net/` **adds to** `vm/` and `vm-lsb/`; it does not replace them. Astra-only ЗПС/МКЦ/ГОСТ layers remain out of scope (need a licensed Astra image — see `deploy/astra-notes.md`). |

## 4. Topology

QEMU socket-multicast forms one isolated L2 segment shared by all four VMs. Static addressing with
`/etc/hosts` so the Orthanc configs stay readable by alias.

| Node | hostname | IP | DICOM port | REST port | AET |
|---|---|---|---|---|---|
| PACS | `pacs` | 10.0.0.10 | 4242 | 8042 | `HOSPITALPACS` |
| Proxy (Astra-like) | `proxy` | 10.0.0.20 | 4242 | 8042 | `CLARINETPROXY` |
| Client A | `clienta` | 10.0.0.31 | 11112 | — | `CLIENTA` |
| Client B | `clientb` | 10.0.0.32 | 11112 | — | `CLIENTB` |

Each VM has two NICs: `eth0` = user-mode NAT (`-netdev user`, for `apt`/plugin downloads during
provisioning), `eth1` = the mcast DICOM LAN (static IP). Golden nodes need `eth0` only at build time.

## 5. Node roles and images

### 5.1 Proxy — rebuilt every run
Reuses the `staging/vm-lsb/` provisioning path verbatim, networked instead of loopback: Buster cloud
image (cached base + fresh overlay) → cloud-init fixes apt to `archive.debian.org`, installs
`libpython3.7` → `DEST=/opt/orthanc bash /repo/deploy/install.sh` → starts one Orthanc from
`config/proxy.json`. This always validates the production LSB artifact and the current proxy code.

### 5.2 PACS — golden
`apt install orthanc orthanc-dicomweb` on a minimal Debian/Ubuntu base; the synthetic studies (§6) are
imported into Orthanc's storage **at golden-build time**, so a booted overlay has data instantly. Config
is tightened for isolation (§7).

### 5.3 Clients — golden
One `client-golden.qcow2` (minimal base + `pynetdicom`) boots as two overlays; role/IP/AET come from
cloud-init. `roles/client_agent.py` provides:
- a **Storage SCP** on port 11112 that counts/records received instances per study;
- **SCU** helpers to issue C-FIND / C-MOVE / C-STORE against a target (proxy or — for negative tests —
  the PACS directly);
- a **barrier** primitive over the 9p share for synchronized concurrent firing;
- a **JSON writer** that records every observation (received counts, association accept/reject, timings)
  to `staging/.data/vm-net/<role>.json`.

### 5.4 Build & run scripts
- `build-golden.sh` — one-time: boots a base image with cloud-init that installs deps, (PACS) imports
  studies, shuts down; the resulting qcow2 is cached in `WORK` as `pacs-golden.qcow2` /
  `client-golden.qcow2`.
- `run.sh` — every run: makes fresh overlays over the goldens + the Buster proxy base, boots all four
  with the mcast LAN + 9p shares, waits for `staging/.data/vm-net/vm-net-done`, then invokes the host
  assertions and prints the result. Env overrides `WORK` / `TIMEOUT` like the existing runners.

## 6. Test data

`gen_studies.py` (pydicom) produces 2–3 studies of **1000+ instances each**. Pixel data is intentionally
tiny (e.g. 32×32) — the test needs instance *count*, not bytes. At least one study carries a Cyrillic
`PatientName` for charset coverage. Generated once and baked into the PACS golden image; the generator is
deterministic so the dataset is reproducible.

## 7. Config and isolation

- `config/proxy.json` — derived from `staging/config/proxy.json`: `DicomModalities` = `pacs` + `clienta`
  + `clientb`; keeps `DicomCheckCalledAet: true` and all `DicomAlwaysAllow*: false`. Modality hosts use
  the static IPs / `/etc/hosts` aliases. `PythonScript` → the installed `clarinet_proxy.py`;
  `DefaultEncoding: "Utf8"`.
- `config/pacs.json` — **tightened versus `staging/config/pacs.json`**, which is permissive
  (`DicomAlwaysAllow*: true`). For isolation we set `DicomAlwaysAllow*: false`, `DicomCheckCalledAet:
  true`, and `DicomModalities` = **only** `proxy`. The PACS therefore rejects any association from an
  unknown caller — which is exactly the S2 negative path. `DefaultEncoding: "Utf8"` (charset
  requirement: every node on the path must be UTF-8).

The proxy's existing logic is unchanged: `resolve_destination` (plugin/proxy_core.py) forwards a C-MOVE
only to an AET present in the proxy's `DicomModalities`, so the clients **must** be registered there and
the PACS must **not** know them — the two configs above encode precisely that asymmetry.

## 8. Resource budget (≤ 24 GB)

| VM | `-m` |
|---|---|
| Proxy | 4096 MB |
| PACS | 3072 MB |
| Client A | 1024 MB |
| Client B | 1024 MB |
| **Guests total** | **~9 GB** |

Well under 24 GB; the headroom absorbs qemu per-process overhead, 9p `msize` buffers, and host page
cache. The proxy may be bumped to 6 GB if large-study retrieval needs it. **Eviction is tested via small
config thresholds (`TTL_SECONDS`, `MAX_STORAGE_MB`), not by exhausting 24 GB** — `evict.py` deletes by
TTL and only *warns* on storage fill, so a deterministic test sets a short TTL and a low cap and asserts
the delete + the WARN.

## 9. Scenario → mechanism mapping

- **S1** — A: C-FIND(`study1`) via proxy, then C-MOVE(`study1`, dest `CLIENTA`) via proxy. The proxy
  pulls from the PACS into its cache and forwards each instance to `CLIENTA`. Assert A's SCP got all N;
  B's got 0.
- **S2** — A issues C-ECHO/C-FIND straight to `pacs:4242`; expect association reject. A second probe
  associates to the proxy under AET `GHOST` (not in the proxy modalities); expect reject. Both rejections
  recorded as facts.
- **S3** — C-FIND with Cyrillic name through the proxy → assert UTF-8 round-trips; C-STORE from A to the
  proxy → assert the instance reaches the PACS; QIDO `/dicom-web/studies` + WADO over the proxy REST →
  assert the study is listed/retrievable.
- **S4** — barrier-synchronized A↔`study1`, B↔`study2`; assert both complete in full, no cross-leak
  (A never sees `study2` instances and vice-versa).
- **S5** — barrier-synchronized A↔`study1`, B↔`study1`; **hard**: both receive `study1` in full;
  **observation**: count proxy→PACS retrievals for `study1` (from the proxy/PACS logs), recorded in JSON.
  No in-flight dedup exists in `proxy_core` today, so the expected observation is "1 or 2 depending on
  cache warmth"; the test documents the actual number rather than failing on it.
- **S6** — set short `TTL_SECONDS` and low `MAX_STORAGE_MB` for the proxy; A/B drive enough large-study
  retrievals to cross the fill threshold; run `evict.py` once; assert TTL-expired studies are gone and a
  fill WARN was logged.

## 10. Layout

```
staging/vm-net/
  README.md            run.sh             build-golden.sh
  gen_studies.py       net.env            test_vm_net.py
  config/   pacs.json   proxy.json
  roles/    client_agent.py   proxy_provision.sh
```

Results land in `staging/.data/vm-net/` (gitignored, like the other harnesses): per-role JSON, node
logs, and the `vm-net-done` sentinel.

## 11. Out of scope / known limits

- **Astra ЗПС/МКЦ/ГОСТ** layers are not exercised — they need a licensed Astra SE image. This harness
  validates the LSB artifact on the Astra *technical base* (Buster), like `vm-lsb/`.
- **C-GET** is not in the selected scenarios. With real `pynetdicom` clients it could be tested honestly
  (unlike the `vm-lsb/` `xfail`, which is an Orthanc query-handle driver regression, not a proxy bug) —
  added only on request.
- **9p cross-VM coherence** has a small lag; barrier polling tolerates it. If it proves flaky, the
  fallback is a tiny TCP barrier over the DICOM LAN — deferred unless needed.
