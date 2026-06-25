# clarinet-pacs-proxy

Orthanc-based DICOM + DICOMweb pass-through proxy in front of a hospital PACS.
Full design: `docs/superpowers/specs/2026-06-24-clarinet-pacs-proxy-design.md`

## Layout

- `plugin/proxy_core.py` — pure proxy logic (no Orthanc imports, unit-testable)
- `plugin/clarinet_proxy.py` — Orthanc Python-plugin glue (callbacks, C-MOVE driver)
- `tests/` — unit suite over the core + glue with a fake Orthanc (no DICOM stack)
- `staging/` — end-to-end tests over a 3-node DICOM network (pacs/proxy/worker)
- `deploy/` — systemd units, `install.sh`, eviction; `etc/` — Orthanc JSON config

## Build / test

- `uv run pytest -q` — unit tests. Use `uv run`, not bare `pytest`; `pytest.ini`
  sets `pythonpath = plugin tests deploy`, so imports only resolve under it.
- `bash staging/vm/run.sh` — end-to-end suite. The host has no Docker; this brings
  the DICOM network up inside a throwaway QEMU/KVM VM (`staging/vm/README.md`).

## Gotchas

- **Charset:** C-FIND is answered in UTF-8 (`SpecificCharacterSet = ISO_IR 192`),
  driven by `DefaultEncoding: "Utf8"` in `etc/10-core.json`. Every Orthanc node on
  the path must use `Utf8` or non-ASCII (Cyrillic) names get down-converted and lost.
- **Astra Linux deploy:** read `deploy/astra-notes.md` before editing `deploy/install.sh`
  — plugin build differs per Astra version, plus libpython/ЗПС/МКЦ/GOST specifics.
