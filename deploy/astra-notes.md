# Deploying on Astra Linux SE «Smolensk»

Astra Linux Special Edition «Smolensk» is Debian-based, so the systemd + LSB deploy
(`install.sh` + `orthanc-proxy.service`) applies — with the Astra-specific points below.
Items marked **[verify on box]** could not be confirmed from public docs; check on the target.

## 1. Which plugin build (`install.sh` handles this automatically)

`install.sh` detects Astra by `ID=astra` and maps `VERSION_ID` → the LSB Python-plugin build.
Astra sets **no `VARIANT_ID`** and puts the release into both `VERSION_ID` and `VERSION_CODENAME`
as `1.7_x86-64` (a string, not a Debian codename like `buster`), so the build is selected from
`VERSION_ID` (`1.7*` → Buster, `1.8*` → Bookworm), not the codename:

| Astra SE | Debian base | glibc | LSB `libOrthancPython.so` build |
|---|---|---|---|
| **1.7** | Debian 10 Buster | 2.28 | `debian-buster-python-3.7` (Python **3.7**) |
| **1.8** | Debian 12 Bookworm | 2.36 | `debian-bookworm-python-3.11` (Python **3.11**) |
| 1.6 | Debian 9 Stretch | 2.24 | none (Py 3.5) — install `libpython3.7` + use the Buster build, or build from source |

On Astra **1.7** only the `buster-3.7` build can load at all: the bullseye/bookworm/trixie
builds are linked against newer glibc (2.31/2.36) than Buster's 2.28.

## 2. System Python ≠ plugin Python

The Orthanc Python plugin **embeds its own interpreter** (it links the libpython of its
build — `libpython3.7` for the Buster build). It runs that Python regardless of what the
system `python3` symlink points to. So an Astra box whose `python3` was **swapped to 3.12**
still works **as long as `libpython3.7` remains installed**:

```bash
/sbin/ldconfig -p | grep libpython3.7   # must be present for the buster-3.7 plugin (ldconfig lives in /sbin)
sudo apt-get install libpython3.7       # if missing — install alongside 3.12
```

`install.sh` prints a `WARNING` if the required `libpython` is absent. This proxy's plugin
code (`clarinet_proxy.py`, `proxy_core.py`) is **Python 3.7-compatible** (no 3.8+ syntax), so
running it under the embedded 3.7 needs no changes.

## 3. Closed software environment (ЗПС)

With ЗПС enabled (the default certified posture), unsigned ELF binaries are **refused
execution** — this includes the curl-downloaded `Orthanc` binary and every `.so` plugin.
**Do not disable ЗПС**; sign the artifacts instead:

1. Check the mode: `astra-modeswitch get` (or inspect `/etc/digsig/digsig_initramfs.conf`).
2. Generate a local signing key (no vendor certification needed), sign each
   `/opt/orthanc/bin/Orthanc` and `/opt/orthanc/plugins/*.so`, install the detached
   signatures and trust the public key, then register it with the running kernel.
   **[verify on box]** — the exact `bsign` / `astra-digsig-control` invocation and signature
   paths (`/etc/digsig/...`) must be taken from the on-box man pages; the public wiki was not
   reachable during research.

(apt `.deb` packages are pre-signed and pass ЗПС without this step, but the repo Orthanc is
~1.5.x — far older than the 1.12.11 this proxy needs — so stay on LSB and pay the one-time
self-signing cost.)

## 4. Mandatory integrity control (МКЦ)

A systemd-launched service runs at integrity level 0 and gets `EACCES` writing into
higher-integrity directories. Make sure `/opt/orthanc`, `/etc/orthanc-proxy`, the LUKS
storage volume (`/var/lib/orthanc-proxy`), the SQLite index, and the log dir sit at an
integrity level consistent with the service. **[verify on box]** — pilot the write paths
(storage, index, eviction DELETEs, logs) before go-live; prefer fixing labels over relaxing МКЦ.
PARSEC MAC needs no config if everything stays at level 0 (the usual case).

## 5. Disk encryption — LUKS vs GOST

`cryptsetup`/LUKS (`deploy/luks-setup.md`) works as written. **[verify with your security
officer]** — under FSTEC certification (Order #117) Astra SE may require a **GOST cipher**
(Кузнечик/Магма) for at-rest encryption rather than AES; if so, create the LUKS volume with the
GOST cipher instead of the default in `luks-setup.md`.

## 6. On-box pre-flight checklist

```bash
cat /etc/os-release            # ID=astra, VERSION_ID=1.7_x86-64 (no VARIANT_ID); 1.8_x86-64 on 1.8
python3 --version              # informational; the plugin uses its embedded Python, not this
/sbin/ldconfig -p | grep libpython3.7   # 3.7 runtime present for the Buster plugin? (1.8 → 3.11)
ldd --version                  # glibc — expect ~2.28 on 1.7, ~2.36 on 1.8
astra-modeswitch get           # is ЗПС active? (then §3 applies)
```
