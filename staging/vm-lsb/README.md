# LSB / Astra-1.7-like test environment

Validates the **production artifacts** — the Orthanc 1.12.11 **LSB** binaries + the
`debian-buster-python-3.7` plugin installed via `deploy/install.sh` — running our
`clarinet_proxy.py`, on the **technical base of Astra SE 1.7 (Debian 10 Buster:
glibc 2.28, libpython3.7)**, with **no Docker** (three plain Orthanc processes), like prod.

The Docker harness (`staging/vm/`) tests the *logic* on the `orthancteam` image; this one tests
the *actual binaries on the actual OS base*. Astra-only ЗПС/МКЦ layers are out of scope here —
they need a licensed Astra SE image (see `deploy/astra-notes.md`).

```bash
bash staging/vm-lsb/run.sh
```

A throwaway Debian 10 Buster VM (QEMU/KVM): the repo is shared in over 9p; cloud-init runs
`provision.sh`, which fixes apt to `archive.debian.org` (Buster is EOL), installs `libpython3.7`
+ pytest/pydicom, runs `deploy/install.sh` (→ Orthanc LSB + buster-3.7 plugin + DicomWeb), starts
3 Orthanc instances (pacs :8051/4251, proxy :8052/4252, worker :8053/4253, all `DefaultEncoding:Utf8`),
and runs the e2e suite with `PACS_URL`/`PROXY_URL`/`WORKER_URL` pointing at them.

## What it confirmed

- `install.sh` detects Buster and pulls the **`debian-buster-python-3.7`** plugin build.
- The **LSB `buster-3.7` Python plugin loads `clarinet_proxy.py`** — `GET /plugins` →
  `["explorer.js", "dicom-web", "python"]`; the proxy comes up as AET `CLARINETPROXY`.
- **C-FIND (Cyrillic), C-MOVE→worker, C-MOVE→self, DICOMweb** all pass on the real LSB stack.

## Known limitation — C-GET on Orthanc 1.12.11

`test_cget_from_cache` is an `xfail` here. Driving C-GET via Orthanc's `/queries/{id}/retrieve`
against the plugin-served proxy sends a C-GET-RQ with an **empty StudyInstanceUID** on the LSB
1.12.11 build (`"Cannot determine what resources are requested by C-GET"`); the same test passes on
the orthancteam 24.10 image (Orthanc 1.12.5). The proxy's C-GET SCP itself is fine — a downstream
issuing a well-formed C-GET-RQ is served from cache; only this Orthanc query-handle driver
regresses. C-GET is optional (spec §4); the primary retrieval paths (C-MOVE, DICOMweb) work.
