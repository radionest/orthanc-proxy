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
sudo cp -r etc /etc/orthanc-proxy
sudo cp deploy/orthanc-proxy.service deploy/orthanc-proxy-evict.{service,timer} /etc/systemd/system/
```
Edit `/etc/orthanc-proxy/30-modalities.json`: set the real `pacs` AET/host/port and add one
`worker_<name>` entry per downstream worker (AET, host, port). Then set up the encrypted
volume (`deploy/luks-setup.md`) and start:
```bash
sudo systemctl enable --now orthanc-proxy.service orthanc-proxy-evict.timer
```

### 3. Configure downstream (Clarinet side — not in this repo)
Each project: `pacs_host=<proxy>`, `pacs_port=4242`, `pacs_aet="CLARINETPROXY"`,
`dicom_aet="WORKER_X"` (must match a `DicomModalities` entry), `dicom_retrieve_mode="c-move"`
(or `"c-get"`). OHIF: `dicomweb_backend="external"`, `dicomweb_external_root="/pacs-web"`.
nginx must reverse-proxy `/pacs-web/` → `127.0.0.1:8042/dicom-web/` and forward
`Forwarded`/`X-Forwarded-*` headers so `BulkDataURI` resolves back through nginx.

### 4. Firewall
Allow inbound :4242 only from loopback, the worker LAN IPs, and the PACS IP. HTTP :8042
is bound to localhost; OHIF reaches it only via the same-host nginx.

### 5. Operate
- Logs: `journalctl -u orthanc-proxy -u orthanc-proxy-evict`.
- Cache fill: eviction logs a `WARN` at ≥80% of `MaximumStorageSize` (14 GB).
- Eviction: studies are deleted ~20 min after last update; `MaximumStorageSize`+Recycle is the backstop.

## Development
```bash
pytest -q                              # unit tests (pure core + glue with fake orthanc)
cd staging && docker compose up -d --build && pytest -q   # end-to-end DICOM tests
```
