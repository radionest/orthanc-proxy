# clarinet-pacs-proxy — Design Spec

**Status:** approved (brainstorming) · **Date:** 2026-06-24 · **Next:** implementation plan (writing-plans)

Orthanc-based DICOM + DICOMweb pass-through proxy in front of a hospital PACS. Standalone
infrastructure; Clarinet is only a downstream consumer (its repo is **not** touched).

---

## 1. Problem

- **Upstream** hospital PACS speaks only classic DIMSE: **C-FIND + C-MOVE**. No C-GET, no DICOMweb.
- **Downstream** several Clarinet projects (API + pipeline workers) on one production host + a
  browser OHIF viewer.
- **Core constraint:** C-MOVE requires the PACS to know each move-destination AET (host/port) in
  advance. Registering every worker with the hospital PACS admins is infeasible.
- **Goal:** a single node registered in the PACS under one AET (`CLARINETPROXY`) that transparently
  proxies Q/R for all downstream consumers. Workers register in the proxy, not in the PACS.

## 2. Fixed architecture (do not revisit)

Orthanc + Python plugin, one node serving both paths:
- **DIMSE** (workers/API): C-FIND + C-MOVE pass-through via the plugin.
- **DICOMweb** (OHIF): native Orthanc DicomWeb plugin over the cache.

Rejected (settled): Go (no mature networked DIMSE stack); a hand-rolled pynetdicom proxy
(redundant since C-GET pass-through is not required, and no DICOMweb/OHIF out of the box).

## 3. Decisions

| # | Decision | Value |
|---|---|---|
| D1 | Worker location | Mixed: some localhost, some LAN hosts → SCP on internal iface; firewall IP-allowlist + AET-allowlist |
| D2 | Study size / concurrency | Medium CT/MR 0.5–2 GB, 2–4 concurrent transits (peak 2–8 GB) |
| D3 | Upstream TLS | None — trusted network, plain DIMSE |
| D4 | Orthanc distribution | Latest **LSB** (Linux Standard Base) precompiled binaries, **1.12.11** |
| D5 | Deploy | **systemd** units + **SSD on LUKS** (encryption-at-rest), caching scheme **A** |
| D6 | Cache lifecycle | **Unified TTL.** No immediate-delete. `OnMove` branches on `TargetAet`: worker → pull + forward + keep until TTL; `CLARINETPROXY` (self) → pull + cache only (OHIF/C-GET pre-load). One TTL eviction + Recycle backstop cleans everything. |
| D7 | Storage cap | `MaximumStorageSize` is a **disk** cap ≈ **14 GB**; RAM safety from reclaimable page cache + `MaximumStorageCacheSize ≤ 512 MB` |
| D8 | Plugin callbacks | Match the official sample: `RegisterFindCallback` (4-arg) + `RegisterMoveCallback2` (4-callback, dict). Modern `RegisterFindCallback2`/`RegisterMoveCallback3` (plugin v7.0, connection object) exist on 1.12.11 but add only cosmetics. |
| D9 | DICOMweb public prefix | `DicomWeb.PublicRoot = "/pacs-web/"` (not `Root`); nginx forwards `Forwarded`/`X-Forwarded-*` |

## 4. Verified API reference (do not re-research)

Confirmed against the Orthanc Book, C SDK, the Python-plugin changelog/source, and the LSB
downloads tree. Starting point: `github.com/orthanc-team/dicom-dicomweb-proxy` (branch `main`,
single file `proxy.py`) — we **keep its OnFind/MoveDriver structure and swap only the upstream
side** from DICOMweb (`/dicom-web/servers/{alias}/get|retrieve`) to native DIMSE.

### 4.1 Plugin callbacks

- `orthanc.RegisterFindCallback(OnFind)` — `def OnFind(answers, query, issuerAet, calledAet)`.
  `query.GetFindQuerySize()`, `query.GetFindQueryTagName(i)`, `query.GetFindQueryValue(i)`,
  `query.GetFindQueryTagGroup(i)`, `query.GetFindQueryTagElement(i)`. **No `GetFindQueryLevel()`** —
  read `QueryRetrieveLevel` from the iterated tags.
  `answers.FindAddAnswer(<dicom buffer>)` where the buffer comes from
  `orthanc.CreateDicom(json.dumps(tags), None, orthanc.CreateDicomFlags.NONE)`.
- `orthanc.RegisterMoveCallback2(create, getMoveSize, applyMove, freeMove)`:
  - `create(**request)` → driver. `request` dict keys: `Level`
    (`PATIENT|STUDY|SERIES|INSTANCE`), `PatientID`, `AccessionNumber`, `StudyInstanceUID`,
    `SeriesInstanceUID`, `SOPInstanceUID`, `TargetAET`, `SourceAET`, `OriginatorAET`,
    `OriginatorID`. (Multi-study C-MOVE arrives as `\\`-separated UIDs.)
  - `getMoveSize(driver)` → int sub-operation count.
  - `applyMove(driver)` → performs **one** sub-operation; return `0` / `orthanc.ErrorCode.SUCCESS`;
    raise on error.
  - `freeMove(driver)` → cleanup.
- **`RegisterGetCallback` does not exist** — C-GET SCP cannot be intercepted by a plugin
  (confirmed). C-GET is served only by Orthanc's built-in SCP over the local cache.
- Versions: legacy `RegisterFindCallback`/`RegisterMoveCallback2` work on the LSB plugin; the
  modern `*Callback2/3` were added in plugin **v7.0** (2025-12-02, built against SDK 1.12.10, min
  SDK 1.7.2) — available on the 1.12.11 LSB plugin. We use the legacy pair (D8).

### 4.2 REST (upstream pull, native DIMSE)

- `POST /modalities/pacs/query` body `{"Level": "...", "Query": {tag: value, ...}}` → `{"ID": "...",
  "Path": "/queries/..."}`. Empty `""` value = return-this-tag.
- `GET /queries/{id}/answers` → JSON array of integer indices `[0,1,...]`.
- `GET /queries/{id}/answers/{i}/content?simplify` → tag-name-keyed dict of that answer.
  ⚠ `GET /queries/{id}/answers?expand` is **not documented** — use indices + per-answer content;
  test `?expand` on staging as an optimisation.
- `POST /modalities/pacs/move` body `{"Level","Resources":[{full UID chain per item},...],
  "TargetAet","Timeout","Synchronous":false}`. Each `Resources` element carries the **full key
  chain** for `Level` (STUDY→`{StudyInstanceUID}`; SERIES→`+SeriesInstanceUID`;
  INSTANCE→`+SOPInstanceUID`); `N` backslash-separated UIDs in the C-MOVE-RQ → `N` elements (Orthanc
  copies tags verbatim into the upstream C-MOVE-RQ and cannot back-fill parent UIDs for a remote
  PACS). **C-MOVE-to-self confirmed**: `TargetAet` = proxy's own AET → PACS C-STOREs matched
  instances back; they land locally. (PACS must have `CLARINETPROXY` registered as a routable
  destination — that's the whole point of this proxy.)
- `POST /modalities/{worker}/store` body `{"Resources":[orthancId,...],"Synchronous":false,
  "LocalAet","Timeout"}` → C-STORE SCU down to the worker.
- `POST /tools/find` body `{"Level":"Instance","Query":{<full UID chain of the move>},
  "Expand":true}` → local Orthanc resource IDs. ⚠ The `Query` MUST carry the deepest requested
  UID(s) (STUDY: StudyInstanceUID; SERIES: +SeriesInstanceUID; INSTANCE: +SOPInstanceUID) — a
  `StudyInstanceUID`-only query over-selects, because the shared TTL cache (D6) may already hold
  other instances of the same study from a prior transit. Enumerate per requested item for
  multi-item moves to avoid cross-product mismatch.
- Async jobs: `{"Synchronous": false}` → `{"ID","Path":"/jobs/..."}`; poll `GET /jobs/{id}`,
  `State ∈ Pending|Running|Success|Failure|Paused|Retry`.

### 4.3 LSB distribution

- Root: `https://orthanc.uclouvain.be/downloads/linux-standard-base/`. Latest **1.12.11**.
  - Server: `.../orthanc/1.12.11/Orthanc` (+ bundled `.so` plugins). `chmod +x` required.
  - Python plugin: `.../orthanc-python/<debian-release>-python-<X.Y>/<ver>/libOrthancPython.so`
    — **organised by libpython ABI**; pick the subdir matching the prod host's Python.
  - DicomWeb plugin: `.../orthanc-dicomweb/1.23/libOrthancDicomWeb.so`.
- Orthanc accepts a **directory** of `*.json` configs: `Orthanc /etc/orthanc-proxy/`.
- No `.service` ships with LSB; model the unit on the Debian package
  (`ExecStart=<Orthanc> <configdir>`, `User=orthanc`, `Restart=on-failure`).

### 4.4 Config keys (verified, with units)

`DicomAet`, `DicomPort` (4242), `DicomServerEnabled`, `DicomCheckCalledAet` (bool),
`DicomCheckModalityHost` (bool), `HttpPort` (8042), `HttpServerEnabled`,
`HttpBindAddresses` (array; `["127.0.0.1"]` = localhost-only), `RemoteAccessAllowed` (bool),
`StorageDirectory`, `IndexDirectory` (may differ; "" → uses StorageDirectory),
`StorageCompression` (bool), `MaximumStorageSize` (**MB**, 0=unlimited),
`MaximumStorageMode` (`Recycle`|`Reject`, recycle = delete oldest **patient**),
`MaximumPatientCount`, `MaximumStorageCacheSize` (**MB**, RAM read-cache, default 128),
`StableAge` (seconds, default 60), `DicomModalities` (object/array form below),
`DicomAlwaysAllowEcho|Store|Find|Move|Get` (must all be `false`),
`Plugins` (array of `.so` files or dirs), `PythonScript`, `PythonVerbose`.
**No DICOM-SCP bind-address key exists** — restrict the SCP at the firewall.

DicomWeb: `DicomWeb.Enable`, `.Root` (`/dicom-web/`), `.PublicRoot` (public prefix behind proxy),
`.EnableWado`, `.WadoRoot`, `.Ssl`. (`.Host` is **deprecated since plugin 0.7** — do not set it; the
plugin computes host/scheme from the reverse-proxy `Forwarded`/`Host` headers, as D9 intends.)
⚠ Exact `BulkDataURI`-uses-`PublicRoot` wording unconfirmed in docs → verify on staging.

DicomModalities object form (per-modality permissions):
`{"AET","Host","Port","Manufacturer","AllowEcho","AllowFind","AllowMove","AllowGet","AllowStore",
"AllowStorageCommitment","LocalAet","Timeout","RetrieveMethod"}`.

## 5. Component design — `plugin/clarinet_proxy.py`

```python
SELF_AET = "CLARINETPROXY"; UPSTREAM = "pacs"
# WORKERS: AET -> Orthanc modality alias, resolved at startup from GET /modalities?expand

def OnFind(answers, query, issuerAet, calledAet):
    level, q = parse_query(query)        # QueryRetrieveLevel -> level; SpecificCharacterSet + rest -> q
    qid = RestApiPost('/modalities/%s/query' % UPSTREAM,
                      {"Level": level, "Query": q})["ID"]
    try:
        for i in RestApiGet('/queries/%s/answers' % qid):
            tags = RestApiGet('/queries/%s/answers/%d/content?simplify' % (qid, i))
            tags['SpecificCharacterSet'] = 'ISO_IR 192'   # ?simplify is UTF-8; pin SCS so CreateDicom
                                                          # does not transcode to Latin1 & corrupt Cyrillic
            answers.FindAddAnswer(orthanc.CreateDicom(json.dumps(tags), None,
                                                      orthanc.CreateDicomFlags.NONE))
    finally:
        RestApiDelete('/queries/%s' % qid)   # release query handle even on error

class MoveDriver:
    def __init__(self, request):
        self.level = request["Level"]
        self.uids  = parse_uids(request)   # -> list of FULLY-QUALIFIED UID dicts, one per requested item:
                                           # STUDY {StudyInstanceUID}; SERIES +SeriesInstanceUID;
                                           # INSTANCE +SOPInstanceUID. N "\\"-separated leaf UIDs -> N dicts.
        target = request["TargetAET"]      # the Move Destination AE; ALWAYS present in a C-MOVE-RQ.
        if not target:                raise Exception("malformed C-MOVE-RQ: missing TargetAET")
        if target == SELF_AET:        self.mode, self.worker = "cache", None     # OHIF/C-GET pre-load
        elif target in WORKERS:       self.mode, self.worker = "forward", WORKERS[target]
        else:                         raise Exception("unknown move destination AET %r" % target)
        # OriginatorAET/OriginatorID are the Move Originator fields — never the destination.
        self.forwarded = set(); self.move_job = None

    def get_size(self):                    # CHEAP: count via C-FIND, start the pull async (do NOT wait)
        self.expected = find_instance_count(UPSTREAM, self.level, self.uids)   # instance-level C-FIND to PACS
        self.move_job = RestApiPost('/modalities/%s/move' % UPSTREAM,
                          {"Level": self.level, "Resources": self.uids,
                           "TargetAet": SELF_AET, "Synchronous": False})["ID"]
        return self.expected

    def apply(self):                       # ONE sub-op per call: wait for next arrival, then forward it
        oid = self.next_arrival()          # poll /tools/find (Instance, full UID chain per item, Expand)
                                           # for an un-forwarded instance; check GET /jobs/{move_job}
                                           # and raise on job Failure or per-instance arrival timeout
        if self.mode == "forward":
            RestApiPost('/modalities/%s/store' % self.worker,
                        {"Resources": [oid], "Synchronous": True})
        self.forwarded.add(oid)            # cache mode: just acknowledge the arrival (no forward)
        return orthanc.ErrorCode.SUCCESS

    def free(self):
        pass                               # TTL eviction owns cleanup (no immediate-delete, D6)

orthanc.RegisterFindCallback(OnFind)
orthanc.RegisterMoveCallback2(
    lambda **r: MoveDriver(r), lambda d: d.get_size(), lambda d: d.apply(), lambda d: d.free())
```

Error contract: missing/unknown `TargetAET` → `__init__` raises (C-MOVE refused); the pull is an
**async** job started in `get_size`, so a job `Failure` or per-instance arrival timeout is detected
in `apply` and raises (failed sub-op); worker C-STORE fail → `apply` raises. `get_size` stays cheap
so Orthanc emits pending C-MOVE-RSP progress (decrementing remaining sub-ops) and the SCU does not
time out on large studies. All logged via `orthanc.LogError`.

## 6. Configuration (`etc/`, Orthanc loads all `*.json`)

- **`10-core.json`** — `DicomAet:"CLARINETPROXY"`, `DicomPort:4242`, `HttpPort:8042`,
  `HttpBindAddresses:["127.0.0.1"]`, `RemoteAccessAllowed:false`,
  `StorageDirectory:"/var/lib/orthanc-proxy/storage"`, `IndexDirectory:"/var/lib/orthanc-proxy/db"`,
  `MaximumStorageSize:14336`, `MaximumStorageMode:"Recycle"`, `MaximumStorageCacheSize:512`,
  `StorageCompression:false`, `StableAge:20`, `DefaultEncoding:"Utf8"` (Orthanc re-encodes every
  C-FIND answer to its `DefaultEncoding`; `Utf8`=ISO_IR 192 is required end-to-end so Cyrillic
  `PatientName` survives — see §12; the upstream PACS and any Orthanc-based downstream consumer
  must use `Utf8` too).
- **`20-security.json`** — `DicomCheckCalledAet:true`, `DicomCheckModalityHost:true`,
  `DicomAlwaysAllowEcho|Store|Find|Move|Get:false`.
- **`30-modalities.json`** —
  - `pacs`: `{AET,Host,Port, AllowEcho:true, AllowStore:true}` (AllowStore = accept C-STORE-back on
    move-to-self).
  - `worker_X` (one per downstream): `{AET,Host,Port, AllowEcho:true, AllowFind:true,
    AllowMove:true, AllowGet:true, AllowStore:false}`.
- **`40-dicomweb.json`** — `{"DicomWeb":{Enable:true, Root:"/dicom-web/",
  PublicRoot:"/pacs-web/", EnableWado:true, Ssl:false}}`.
- **`50-python.json`** — `{Plugins:["/opt/orthanc/plugins"], PythonScript:".../clarinet_proxy.py"}`.

## 7. Storage & eviction (scheme A)

- `StorageDirectory` + `IndexDirectory` both on the LUKS-SSD volume → consistent after reboot.
- Three layers: (1) **primary** TTL systemd-timer (`deploy/evict.py`, `.timer`
  `OnUnitActiveSec=5min`: `GET /studies?expand`, `DELETE /studies/{id}` for any whose top-level
  `LastUpdate` field — Orthanc datetime, e.g. `20180414T091528` — is older than a fixed TTL of
  **1200 s (20 min)**); (2) **backstop** `MaximumStorageSize` + `Recycle`; (3) `StableAge:20` so
  resources stabilise quickly (orthogonal: 20 s stabilisation vs 20 min retention). **No
  immediate-delete** — transit copies double as the OHIF/C-GET cache during their TTL window.
- Monitoring: the eviction timer logs storage fill (used / `MaximumStorageSize`) each run and emits
  a `WARN` at ≥ 80 % fill. Active alert delivery (paging/Prometheus) is out of scope — log scraping
  is left to host ops tooling.

## 8. Security

- HTTP localhost-only (`HttpBindAddresses:["127.0.0.1"]`, `RemoteAccessAllowed:false`); OHIF reaches
  it only via Clarinet nginx on the same host.
- DICOM SCP on :4242 — no bind-address key in Orthanc → **host firewall** allowlist (loopback + LAN
  worker IPs + PACS IP) plus `DicomCheckCalledAet` + `DicomCheckModalityHost` + AET allowlist via
  `DicomModalities`. All `DicomAlwaysAllow*` off.
- PHI: raw transit (anonymisation is Clarinet-side, out of scope); minimised lifetime (TTL) +
  encryption-at-rest (LUKS). TLS to PACS not used (D3).

## 9. Deployment (systemd + LSB + LUKS)

- `deploy/install.sh` — download Orthanc 1.12.11 LSB + `libOrthancDicomWeb.so` (1.23) +
  `libOrthancPython.so` **matching the host Python ABI** (detect `python3 --version` → pick
  `debian-*-python-*` subdir), place under `/opt/orthanc`, `chmod +x`.
- `deploy/orthanc-proxy.service` — modelled on the Debian unit;
  `ExecStart=/opt/orthanc/Orthanc /etc/orthanc-proxy/`, `User=orthanc`, `Restart=on-failure`,
  `After=`/`RequiresMountsFor=` the LUKS mount.
- `deploy/orthanc-proxy-evict.service` + `.timer` → `deploy/evict.py`.
- `deploy/luks-setup.md` — cryptsetup volume, keyfile, `crypttab`/`fstab`, unlock-before-Orthanc
  ordering.

## 10. Deliverables / structure

```
README.md                       purpose, ASCII topology, runbook (register CLARINETPROXY at PACS,
                                configure downstream, LSB+ABI install, LUKS, ops/monitoring)
docs/superpowers/specs/         this spec
etc/{10-core,20-security,30-modalities,40-dicomweb,50-python}.json
plugin/clarinet_proxy.py        OnFind + MoveDriver
deploy/{install.sh, orthanc-proxy.service, orthanc-proxy-evict.service,
        orthanc-proxy-evict.timer, evict.py, luks-setup.md}
staging/{docker-compose.yml, test.py}
```

## 11. Staging test plan (`staging/`)

docker-compose: test-PACS (`orthancteam/orthanc`, DIMSE only as far as the proxy is concerned),
a simulated downstream worker (another Orthanc as C-STORE SCP), and the proxy. `test.py` exercises:
1. **C-FIND** → forwarded to PACS, answers returned (incl. Cyrillic `PatientName`/charset).
2. **C-MOVE dest=worker** → study pulled from PACS, forwarded, received on the sim-worker.
3. **C-MOVE dest=CLARINETPROXY** → cached → served via DICOMweb (`/pacs-web/...metadata`).
4. **C-GET** from cache.
5. **TTL eviction** removes transit after the window.

## 12. Risks — verify on staging

`?expand` on query answers; Orthanc 1.12.11 **C-GET SCP** availability + `AllowGet` semantics;
move-to-self timing/timeout for 2 GB studies (async job + per-instance arrival polling);
`PublicRoot` + `Forwarded` → correct `BulkDataURI`; Python-plugin **ABI match** on the prod host.

**Charset (RESOLVED on staging).** Cyrillic `PatientName` round-trip was verified end-to-end. Root
cause found during e2e: Orthanc's C-FIND SCP re-encodes **every** answer to the node's
`DefaultEncoding` (default Latin1, which drops Cyrillic) — there is no plugin API to set per-answer
encoding. Fix: `DefaultEncoding:"Utf8"` on the proxy **and** the upstream PACS **and** any
Orthanc-based downstream consumer (each node re-encodes what it sends/receives). OnFind also pins
`ISO_IR 192` on the answer dataset and requests it in the upstream C-FIND query, but
`DefaultEncoding` is the decisive setting.

## 13. Downstream Clarinet compatibility (do not change Clarinet)

Each downstream sets: `pacs_host=<proxy>`, `pacs_port=4242`, `pacs_aet="CLARINETPROXY"`,
`dicom_aet="WORKER_X"` (must be in `DicomModalities`), `dicom_retrieve_mode="c-move"` (or `"c-get"`
from cache). OHIF: `dicomweb_backend="external"`, `dicomweb_external_root="/pacs-web"`. The proxy
must accept C-FIND/C-MOVE under `CLARINETPROXY`, hold a `DicomModalities` entry per worker AET, and
serve DicomWeb with a public root matching `/pacs-web`.

## 14. Out of scope

No Clarinet repo edits; no anonymisation (raw transit); no C-GET pass-through (C-GET only from
local cache); nginx/auth config is Clarinet-side; alert *delivery* for storage-fill warnings
(log-only, scraped by host ops tooling).
