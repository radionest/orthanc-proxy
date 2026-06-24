# clarinet-pacs-proxy

An Orthanc-based DICOM + DICOMweb pass-through proxy in front of a hospital PACS that
speaks only C-FIND + C-MOVE. It registers in the PACS under a single AET
(`CLARINETPROXY`) and transparently proxies query/retrieve for unregistered Clarinet
workers and an OHIF viewer. See `docs/superpowers/specs/2026-06-24-clarinet-pacs-proxy-design.md`.

## Topology

```
   Hospital PACS  ──C-FIND/C-MOVE SCU──►  ┌──────────────── proxy host ───────────────┐
   (C-FIND/C-MOVE) ◄─C-STORE (move-to-self)│  Orthanc 1.12.11 + clarinet_proxy.py       │
                                           │  AET CLARINETPROXY  :4242 (LAN+lo, firewalled)
   workers (lo+LAN) ─C-FIND/C-MOVE/C-GET──►│  HTTP :8042 (127.0.0.1 only)               │
   OHIF ◄─ nginx (Clarinet) ─DICOMweb /pacs-web─►  DicomWeb plugin over the cache       │
                                           │  storage+index on LUKS-SSD, TTL eviction   │
                                           └────────────────────────────────────────────┘
```

- **C-FIND** (worker→proxy): forwarded to the PACS, answers returned. Zero storage.
- **C-MOVE dest=worker**: proxy pulls the study from the PACS (C-MOVE-to-self), then
  C-STOREs it to the worker. Transit copy cached until TTL.
- **C-MOVE dest=CLARINETPROXY**: pulled and cached only (pre-loads OHIF/C-GET).
- **C-GET / DICOMweb**: served from the local cache.

## Runbook

### 1. Register at the hospital PACS (one-time, by PACS admins)
Give the PACS admins this move-destination:
`AET=CLARINETPROXY`, `Host=<proxy LAN IP>`, `Port=4242`.

### 2. Install (systemd + LSB)
```bash
sudo DEST=/opt/orthanc bash deploy/install.sh        # downloads Orthanc 1.12.11 + plugins (host-ABI Python)
sudo install -d /etc/orthanc-proxy
sudo cp -r etc/. /etc/orthanc-proxy
sudo cp deploy/orthanc-proxy.service deploy/orthanc-proxy-evict.{service,timer} /etc/systemd/system/
```
Edit `/etc/orthanc-proxy/30-modalities.json`: set the real `pacs` AET/host/port and add one
`worker_<name>` entry per downstream worker (AET, host, port). Then set up the encrypted
volume (`deploy/luks-setup.md`) and start:
```bash
# evict.service is triggered by the timer — do not enable it directly
sudo systemctl enable --now orthanc-proxy.service orthanc-proxy-evict.timer
```
`install.sh` picks the LSB Python-plugin build for the host (Debian codename, or `VERSION_ID`
on Astra). **On Astra Linux SE «Smolensk» read [`deploy/astra-notes.md`](deploy/astra-notes.md)**
first — plugin build per Astra version, the `libpython` requirement when `python3` was swapped,
ЗПС self-signing, МКЦ write-paths, and GOST disk encryption.

### 3. Configure downstream (Clarinet side — not in this repo)
Each project: `pacs_host=<proxy>`, `pacs_port=4242`, `pacs_aet="CLARINETPROXY"`,
`dicom_aet="WORKER_X"` (must match a `DicomModalities` entry), `dicom_retrieve_mode="c-move"`
(or `"c-get"`). OHIF: `dicomweb_backend="external"`, `dicomweb_external_root="/pacs-web"`.
nginx must reverse-proxy `/pacs-web/` → `127.0.0.1:8042/dicom-web/` and forward
`Forwarded`/`X-Forwarded-*` headers so `BulkDataURI` resolves back through nginx.

> **Character set (important for non-ASCII names, e.g. Cyrillic).** The proxy answers
> C-FIND in **UTF-8** (`SpecificCharacterSet = ISO_IR 192`); this is set by
> `DefaultEncoding: "Utf8"` in `10-core.json`. Orthanc re-encodes every DICOM message to
> its own `DefaultEncoding`, so **every Orthanc node on the path must use `Utf8`** or it
> will down-convert names to Latin-1 and drop the characters it can't represent. If a
> downstream consumer is itself Orthanc-based, set its `DefaultEncoding` to `Utf8` too;
> non-Orthanc consumers must simply accept `ISO_IR 192` responses.

### 4. Firewall
Allow inbound :4242 only from loopback, the worker LAN IPs, and the PACS IP. HTTP :8042
is bound to localhost; OHIF reaches it only via the same-host nginx.

### 5. Operate
- Logs: `journalctl -u orthanc-proxy -u orthanc-proxy-evict`.
- Cache fill: eviction logs a `WARN` at ≥80% of `MaximumStorageSize` (14 GB).
- Eviction: studies are deleted ~20 min after last update; `MaximumStorageSize`+Recycle is the backstop.

## Development
```bash
uv run pytest -q                       # unit tests (pure core + glue with a fake orthanc)
bash staging/vm/run.sh                 # end-to-end DICOM tests in a throwaway Docker-in-VM
```
The unit suite needs no DICOM stack. The end-to-end suite (`staging/`) brings up a 3-node
DICOM network (pacs/proxy/worker) under Docker; on a host without Docker, `staging/vm/run.sh`
runs it inside a QEMU/KVM VM (see `staging/vm/README.md`). On a Docker host you can instead run
`cd staging && docker compose up -d --build && pytest -q`.
