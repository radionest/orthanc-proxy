# vm-net Multi-machine e2e Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A 4-VM DICOM network (PACS / Astra-like proxy / two SCU clients) that validates `clarinet_proxy.py` across real machine boundaries — C-MOVE routing, AET isolation, pass-through, concurrency, and TTL eviction — within 24 GB RAM.

**Architecture:** Rootless QEMU/KVM VMs on a socket-multicast L2. PACS and clients boot from pre-baked golden qcow2 images; the proxy is rebuilt every run from `deploy/install.sh` (the real LSB artifact). Pure logic lives in host-unit-testable modules (`study_plan.py`, `agent_core.py`, `vmnet_assert.py`); pynetdicom/QEMU glue is validated by the end-to-end run. Nodes coordinate and report through the 9p-shared `staging/.data/vm-net/` directory; the host asserts over the collected JSON.

**Tech Stack:** Python 3.7 (deploy runtime) / pydicom / pynetdicom, QEMU/KVM, cloud-init (NoCloud + network-config v2), 9p, Orthanc (distro for PACS, LSB for proxy), pytest.

## Global Constraints

- **Python floor:** all Python that runs on a VM must work on **Python 3.7** (Buster/Astra runtime). Pure host-test modules use stdlib only.
- **Pin DICOM libs for 3.7:** `pydicom==2.4.4`, `pynetdicom==2.0.2`.
- **No DICOM stack on the host:** host unit tests (`tests/`) import only stdlib + the pure modules — never `pydicom`/`pynetdicom`/`orthanc`.
- **UTF-8 everywhere:** every Orthanc node config sets `"DefaultEncoding": "Utf8"`; the proxy answers C-FIND in `ISO_IR 192`.
- **AET / IP / port map is fixed** (single source of truth `staging/vm-net/net.env`): PACS `HOSPITALPACS` 10.0.0.10:4242/8042 · proxy `CLARINETPROXY` 10.0.0.20:4242/8042 · client A `CLIENTA` 10.0.0.31:11112 · client B `CLIENTB` 10.0.0.32:11112 · mcast `230.0.0.1:1234`.
- **Isolation invariant:** PACS knows **only** the proxy and has every `DicomAlwaysAllow*: false`; the proxy knows `pacs`+`clienta`+`clientb`.
- **RAM ceiling 24 GB:** proxy 4096 MB, PACS 3072 MB, clients 1024 MB each (~9 GB guests).
- **Results dir** `staging/.data/vm-net/` is gitignored (covered by existing `staging/.data/` rule).
- **Run host unit tests with** `uv run pytest` (the repo has no pyproject; uv provides pytest).
- **Commit style:** English messages, no `Co-Authored-By`, no emoji.

---

### Task 1: Topology map, Orthanc configs, isolation invariants

**Files:**
- Create: `staging/vm-net/net.env`
- Create: `staging/vm-net/config/pacs.json`
- Create: `staging/vm-net/config/proxy.json`
- Modify: `pytest.ini` (add `staging/vm-net` to `pythonpath`)
- Test: `tests/test_vmnet_config.py`

**Interfaces:**
- Produces: the two config files and `net.env`; the invariant test pins the isolation guarantees other tasks rely on.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vmnet_config.py
import json
import os

CFG = os.path.join(os.path.dirname(__file__), "..", "staging", "vm-net", "config")
ALLOW_FLAGS = [
    "DicomAlwaysAllowEcho", "DicomAlwaysAllowStore", "DicomAlwaysAllowFind",
    "DicomAlwaysAllowMove", "DicomAlwaysAllowGet",
]


def _load(name):
    with open(os.path.join(CFG, name), encoding="utf-8") as f:
        return json.load(f)


def test_pacs_knows_only_proxy_and_denies_all_defaults():
    pacs = _load("pacs.json")
    assert pacs["DicomAet"] == "HOSPITALPACS"
    assert pacs["DicomCheckCalledAet"] is True
    for flag in ALLOW_FLAGS:
        assert pacs[flag] is False, flag
    assert list(pacs["DicomModalities"].keys()) == ["proxy"]
    assert pacs["DicomModalities"]["proxy"]["AET"] == "CLARINETPROXY"
    assert pacs["DefaultEncoding"] == "Utf8"


def test_proxy_knows_pacs_and_both_clients():
    proxy = _load("proxy.json")
    assert proxy["DicomAet"] == "CLARINETPROXY"
    for flag in ALLOW_FLAGS:
        assert proxy[flag] is False, flag
    assert set(proxy["DicomModalities"].keys()) == {"pacs", "clienta", "clientb"}
    assert proxy["DicomModalities"]["pacs"]["AET"] == "HOSPITALPACS"
    assert proxy["DicomModalities"]["clienta"]["AET"] == "CLIENTA"
    assert proxy["DicomModalities"]["clientb"]["AET"] == "CLIENTB"
    assert proxy["PythonScript"] == "/opt/clarinet/clarinet_proxy.py"
    assert proxy["DefaultEncoding"] == "Utf8"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vmnet_config.py -v`
Expected: FAIL — `FileNotFoundError` (configs not created yet).

- [ ] **Step 3: Create `staging/vm-net/net.env`**

```bash
# staging/vm-net/net.env — single source of truth for the vm-net topology.
# Sourced by build-golden.sh and run.sh.
NET_PREFIX=10.0.0
MCAST=230.0.0.1:1234

PACS_IP=10.0.0.10;    PACS_AET=HOSPITALPACS;  PACS_DICOM=4242; PACS_REST=8042
PROXY_IP=10.0.0.20;   PROXY_AET=CLARINETPROXY; PROXY_DICOM=4242; PROXY_REST=8042
CLIENTA_IP=10.0.0.31; CLIENTA_AET=CLIENTA;     CLIENTA_SCP=11112
CLIENTB_IP=10.0.0.32; CLIENTB_AET=CLIENTB;     CLIENTB_SCP=11112

# MACs: nat NIC (user-mode) and lan NIC (mcast). network-config matches on these.
PACS_NAT_MAC=52:54:00:00:01:0a;    PACS_LAN_MAC=52:54:00:00:00:0a
PROXY_NAT_MAC=52:54:00:00:01:14;   PROXY_LAN_MAC=52:54:00:00:00:14
CLIENTA_NAT_MAC=52:54:00:00:01:1f; CLIENTA_LAN_MAC=52:54:00:00:00:1f
CLIENTB_NAT_MAC=52:54:00:00:01:20; CLIENTB_LAN_MAC=52:54:00:00:00:20
```

- [ ] **Step 4: Create `staging/vm-net/config/pacs.json`**

```json
{
  "Name": "vmnet-pacs",
  "DicomAet": "HOSPITALPACS",
  "DicomPort": 4242,
  "HttpPort": 8042,
  "RemoteAccessAllowed": true,
  "AuthenticationEnabled": false,
  "DicomCheckCalledAet": true,
  "DicomCheckModalityHost": false,
  "DicomAlwaysAllowEcho": false,
  "DicomAlwaysAllowStore": false,
  "DicomAlwaysAllowFind": false,
  "DicomAlwaysAllowMove": false,
  "DicomAlwaysAllowGet": false,
  "StorageDirectory": "/var/lib/orthanc/db",
  "IndexDirectory": "/var/lib/orthanc/db",
  "DicomModalities": {
    "proxy": { "AET": "CLARINETPROXY", "Host": "proxy", "Port": 4242, "AllowEcho": true, "AllowFind": true, "AllowMove": true, "AllowStore": true }
  },
  "DefaultEncoding": "Utf8"
}
```

- [ ] **Step 5: Create `staging/vm-net/config/proxy.json`**

```json
{
  "Name": "vmnet-proxy",
  "DicomAet": "CLARINETPROXY",
  "DicomPort": 4242,
  "HttpPort": 8042,
  "RemoteAccessAllowed": true,
  "AuthenticationEnabled": false,
  "DicomCheckCalledAet": true,
  "DicomCheckModalityHost": false,
  "DicomAlwaysAllowEcho": false,
  "DicomAlwaysAllowStore": false,
  "DicomAlwaysAllowFind": false,
  "DicomAlwaysAllowMove": false,
  "DicomAlwaysAllowGet": false,
  "DicomModalities": {
    "pacs":    { "AET": "HOSPITALPACS", "Host": "pacs",    "Port": 4242, "AllowEcho": true, "AllowFind": true, "AllowMove": true, "AllowStore": true },
    "clienta": { "AET": "CLIENTA",      "Host": "clienta", "Port": 11112, "AllowEcho": true, "AllowStore": true },
    "clientb": { "AET": "CLIENTB",      "Host": "clientb", "Port": 11112, "AllowEcho": true, "AllowStore": true }
  },
  "DicomWeb": { "Enable": true, "Root": "/dicom-web/", "PublicRoot": "/dicom-web/", "EnableWado": true },
  "Plugins": ["/usr/share/orthanc/plugins"],
  "PythonScript": "/opt/clarinet/clarinet_proxy.py",
  "PythonVerbose": true,
  "DefaultEncoding": "Utf8"
}
```

- [ ] **Step 6: Add the vm-net dir to pytest's pythonpath**

Edit `pytest.ini` — change the `pythonpath` line:

```ini
[pytest]
pythonpath = plugin tests deploy staging/vm-net
testpaths = tests
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/test_vmnet_config.py -v`
Expected: PASS (2 passed).

- [ ] **Step 8: Commit**

```bash
git add staging/vm-net/net.env staging/vm-net/config tests/test_vmnet_config.py pytest.ini
git commit -m "test(vm-net): topology map + isolation-pinned Orthanc configs"
```

---

### Task 2: Synthetic study planning + generator

**Files:**
- Create: `staging/vm-net/study_plan.py` (pure — no pydicom)
- Create: `staging/vm-net/gen_studies.py` (pydicom writer + CLI; runs only in a VM)
- Test: `tests/test_vmnet_study_plan.py`

**Interfaces:**
- Produces: `build_study_plan(num_studies=3, instances_per_study=1000) -> list[dict]`. Each dict has keys `StudyInstanceUID:str`, `SeriesInstanceUID:str`, `PatientName:str`, `PatientID:str`, `SOPInstanceUIDs:list[str]`. Study index 0 carries the Cyrillic patient name `CYRILLIC_NAME`. Consumed by `gen_studies.py` and referenced by the agents/assertions for expected counts.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vmnet_study_plan.py
import study_plan


def test_three_studies_each_at_least_1000_instances():
    plan = study_plan.build_study_plan()
    assert len(plan) == 3
    for s in plan:
        assert len(s["SOPInstanceUIDs"]) >= 1000


def test_first_study_has_cyrillic_name():
    plan = study_plan.build_study_plan()
    assert plan[0]["PatientName"] == study_plan.CYRILLIC_NAME
    assert any(ord(c) > 127 for c in plan[0]["PatientName"])


def test_uids_are_globally_unique():
    plan = study_plan.build_study_plan(num_studies=3, instances_per_study=50)
    sop = [u for s in plan for u in s["SOPInstanceUIDs"]]
    assert len(sop) == len(set(sop))
    studies = [s["StudyInstanceUID"] for s in plan]
    assert len(studies) == len(set(studies))


def test_is_deterministic():
    assert study_plan.build_study_plan(2, 10) == study_plan.build_study_plan(2, 10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vmnet_study_plan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'study_plan'`.

- [ ] **Step 3: Create `staging/vm-net/study_plan.py`**

```python
"""Pure planning for synthetic studies — no pydicom, host-unit-testable."""

CYRILLIC_NAME = "Иванов^Пётр"
ROOT = "1.2.826.0.1.3680043.8.498"


def build_study_plan(num_studies=3, instances_per_study=1000):
    studies = []
    for s in range(num_studies):
        study_uid = f"{ROOT}.{s + 1}"
        series_uid = f"{study_uid}.1"
        name = CYRILLIC_NAME if s == 0 else f"Patient^{s + 1}"
        sops = [f"{series_uid}.{i + 1}" for i in range(instances_per_study)]
        studies.append(
            {
                "StudyInstanceUID": study_uid,
                "SeriesInstanceUID": series_uid,
                "PatientName": name,
                "PatientID": f"VMNET{s + 1:03d}",
                "SOPInstanceUIDs": sops,
            }
        )
    return studies
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_vmnet_study_plan.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Create `staging/vm-net/gen_studies.py` (writer; runs in the PACS golden build only)**

```python
"""Write the synthetic studies from study_plan as tiny CT instances (pydicom).

Runs inside the PACS golden-image build, where pydicom is installed. Not imported
by host unit tests. Output is one .dcm per instance under --out."""

import argparse
import os

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

import study_plan

ROWS = COLS = 32


def _write_instance(out_dir, study, sop_uid):
    meta = Dataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = generate_uid()

    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.PatientName = study["PatientName"]
    ds.PatientID = study["PatientID"]
    ds.StudyInstanceUID = study["StudyInstanceUID"]
    ds.SeriesInstanceUID = study["SeriesInstanceUID"]
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = sop_uid
    ds.Modality = "CT"
    ds.Rows = ROWS
    ds.Columns = COLS
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = np.zeros((ROWS, COLS), dtype=np.uint16).tobytes()
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.save_as(os.path.join(out_dir, sop_uid + ".dcm"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--studies", type=int, default=3)
    ap.add_argument("--instances", type=int, default=1000)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    plan = study_plan.build_study_plan(args.studies, args.instances)
    n = 0
    for study in plan:
        for sop in study["SOPInstanceUIDs"]:
            _write_instance(args.out, study, sop)
            n += 1
    print(f"wrote {n} instances for {len(plan)} studies to {args.out}")
    _ = pydicom  # keep the import meaningful for linters


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Syntax-check the writer on the host (no pydicom needed to compile)**

Run: `python3 -m py_compile staging/vm-net/gen_studies.py && echo OK`
Expected: `OK` (real execution is exercised in the golden build, Task 6).

- [ ] **Step 7: Commit**

```bash
git add staging/vm-net/study_plan.py staging/vm-net/gen_studies.py tests/test_vmnet_study_plan.py
git commit -m "feat(vm-net): synthetic study plan + pydicom generator"
```

---

### Task 3: Client-agent pure helpers (barrier + result recording)

**Files:**
- Create: `staging/vm-net/agent_core.py` (pure — no pynetdicom)
- Test: `tests/test_vmnet_agent_core.py`

**Interfaces:**
- Produces:
  - `barrier_signal(barrier_dir, name) -> None` — touch `ready_<name>`.
  - `barrier_wait_all(barrier_dir, names, timeout=120, poll=0.5, sleep=time.sleep, clock=time.monotonic) -> bool` — True once a `ready_<n>` file exists for every `n` in `names`, else False on timeout.
  - `new_result(role, aet) -> dict` — `{"role","aet","received":{},"events":[]}`.
  - `record_received(result, phase, study_uid, calling_aet) -> None` — increments `received[phase][study_uid]["count"]`, stores `from`.
  - `record_event(result, kind, **fields) -> None` — append `{"kind",...}` to `events`.
  - `write_result(path, result) -> None` — atomic UTF-8 JSON (`ensure_ascii=False`).
  - `received_count(result, phase, study_uid) -> int`.
- Consumed by: `roles/client_agent.py` (Task 4) and mirrored by `vmnet_assert.py` (Task 5).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vmnet_agent_core.py
import json
import os

import agent_core


def test_barrier_signal_creates_file(tmp_path):
    agent_core.barrier_signal(str(tmp_path), "a")
    assert (tmp_path / "ready_a").exists()


def test_barrier_wait_all_true_when_all_present(tmp_path):
    agent_core.barrier_signal(str(tmp_path), "a")
    agent_core.barrier_signal(str(tmp_path), "b")
    ok = agent_core.barrier_wait_all(
        str(tmp_path), ["a", "b"], timeout=5, sleep=lambda *_: None
    )
    assert ok is True


def test_barrier_wait_all_times_out_without_real_sleep(tmp_path):
    ticks = iter([0, 1, 2, 3, 4, 5, 6])
    ok = agent_core.barrier_wait_all(
        str(tmp_path), ["a", "b"], timeout=5,
        sleep=lambda *_: None, clock=lambda: next(ticks),
    )
    assert ok is False


def test_record_received_accumulates_per_phase(tmp_path):
    r = agent_core.new_result("clienta", "CLIENTA")
    agent_core.record_received(r, "s1", "1.2.3", "CLARINETPROXY")
    agent_core.record_received(r, "s1", "1.2.3", "CLARINETPROXY")
    assert agent_core.received_count(r, "s1", "1.2.3") == 2
    assert r["received"]["s1"]["1.2.3"]["from"] == "CLARINETPROXY"
    assert agent_core.received_count(r, "s1", "9.9.9") == 0


def test_write_result_atomic_and_preserves_cyrillic(tmp_path):
    r = agent_core.new_result("clienta", "CLIENTA")
    agent_core.record_event(r, "cfind_cyrillic", name="Иванов^Пётр", ok=True)
    out = tmp_path / "sub" / "clienta.json"
    agent_core.write_result(str(out), r)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["events"][0]["name"] == "Иванов^Пётр"
    assert not os.path.exists(str(out) + ".tmp")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vmnet_agent_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_core'`.

- [ ] **Step 3: Create `staging/vm-net/agent_core.py`**

```python
"""Pure helpers for the client agent: 9p barrier + result recording. No pynetdicom."""

import json
import os
import time


def barrier_signal(barrier_dir, name):
    os.makedirs(barrier_dir, exist_ok=True)
    open(os.path.join(barrier_dir, "ready_" + name), "w").close()


def barrier_wait_all(barrier_dir, names, timeout=120, poll=0.5,
                     sleep=time.sleep, clock=time.monotonic):
    deadline = clock() + timeout
    while clock() < deadline:
        if all(os.path.exists(os.path.join(barrier_dir, "ready_" + n)) for n in names):
            return True
        sleep(poll)
    return False


def new_result(role, aet):
    return {"role": role, "aet": aet, "received": {}, "events": []}


def record_received(result, phase, study_uid, calling_aet):
    phase_bucket = result["received"].setdefault(phase, {})
    entry = phase_bucket.setdefault(study_uid, {"count": 0, "from": calling_aet})
    entry["count"] += 1
    entry["from"] = calling_aet


def record_event(result, kind, **fields):
    result["events"].append(dict(kind=kind, **fields))


def received_count(result, phase, study_uid):
    return result.get("received", {}).get(phase, {}).get(study_uid, {}).get("count", 0)


def write_result(path, result):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    os.replace(tmp, path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_vmnet_agent_core.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add staging/vm-net/agent_core.py tests/test_vmnet_agent_core.py
git commit -m "feat(vm-net): pure barrier + result-recording helpers for the client agent"
```

---

### Task 4: Client agent (pynetdicom binding) + proxy provisioning script

**Files:**
- Create: `staging/vm-net/roles/client_agent.py` (pynetdicom; runs in client VMs)
- Create: `staging/vm-net/roles/proxy_provision.sh` (runs in the proxy VM)

**Interfaces:**
- Consumes: `agent_core` (Task 3), `study_plan` (Task 2). Reads role/topology from env: `ROLE`, `SELF_AET`, `SCP_PORT`, `PROXY_HOST`, `PROXY_AET`, `PROXY_DICOM`, `PROXY_REST`, `PACS_HOST`, `PACS_AET`, `PACS_DICOM`, `BARRIER_DIR`, `RESULT_PATH`, `INSTANCES_PER_STUDY`.
- Produces: per-role JSON at `RESULT_PATH` and a `ready_<role>_phases_done` barrier file. The proxy script produces `proxy.json` + `ready_proxy_done`.
- Note: this task is **integration-validated by Task 8** (it needs pynetdicom and live peers). Host verification here is `py_compile` + ruff only.

- [ ] **Step 1: Create `staging/vm-net/roles/client_agent.py`**

```python
"""SCU client agent: a Storage SCP + scripted C-FIND/C-MOVE/C-STORE scenarios.

Driven entirely by env (see plan Task 4 interfaces). Records every observation
via agent_core and writes RESULT_PATH; coordinates the concurrent phases through
the 9p barrier dir. Runs on Python 3.7 inside the client golden VM."""

import os
import sys
import threading
import time
import urllib.request

import numpy as np
from pydicom.dataset import Dataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid
from pynetdicom import AE, StoragePresentationContexts, evt
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelMove,
    Verification,
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import agent_core as ac
import study_plan

ROLE = os.environ["ROLE"]                       # "clienta" | "clientb"
SELF_AET = os.environ["SELF_AET"]
SCP_PORT = int(os.environ["SCP_PORT"])
PROXY_HOST = os.environ["PROXY_HOST"]
PROXY_AET = os.environ["PROXY_AET"]
PROXY_DICOM = int(os.environ["PROXY_DICOM"])
PROXY_REST = int(os.environ["PROXY_REST"])
PACS_HOST = os.environ["PACS_HOST"]
PACS_AET = os.environ["PACS_AET"]
PACS_DICOM = int(os.environ["PACS_DICOM"])
BARRIER_DIR = os.environ["BARRIER_DIR"]
RESULT_PATH = os.environ["RESULT_PATH"]
INSTANCES = int(os.environ.get("INSTANCES_PER_STUDY", "1000"))

PLAN = study_plan.build_study_plan(3, INSTANCES)
STUDY = {i + 1: s["StudyInstanceUID"] for i, s in enumerate(PLAN)}  # study1..study3

result = ac.new_result(ROLE, SELF_AET)
_current_phase = {"name": "idle"}
_lock = threading.Lock()


def _on_store(event):
    ds = event.dataset
    ds.file_meta = event.file_meta
    calling = event.assoc.requestor.ae_title
    if hasattr(calling, "decode"):
        calling = calling.decode().strip()
    with _lock:
        ac.record_received(result, _current_phase["name"], str(ds.StudyInstanceUID), str(calling))
    return 0x0000


def start_scp():
    ae = AE(ae_title=SELF_AET)
    ae.supported_contexts = StoragePresentationContexts
    return ae.start_server(("0.0.0.0", SCP_PORT), block=False,
                           evt_handlers=[(evt.EVT_C_STORE, _on_store)])


def _find_identifier(study_uid):
    ds = Dataset()
    ds.QueryRetrieveLevel = "STUDY"
    ds.StudyInstanceUID = study_uid
    ds.PatientName = ""
    return ds


def cfind_cyrillic(study_uid, expect_name):
    ae = AE(ae_title=SELF_AET)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
    assoc = ae.associate(PROXY_HOST, PROXY_DICOM, ae_title=PROXY_AET)
    got = None
    if assoc.is_established:
        for status, ident in assoc.send_c_find(
            _find_identifier(study_uid), StudyRootQueryRetrieveInformationModelFind
        ):
            if ident is not None and "PatientName" in ident:
                got = str(ident.PatientName)
        assoc.release()
    ac.record_event(result, "cfind_cyrillic", name=got, ok=(got == expect_name))


def cmove(phase, study_uid):
    ae = AE(ae_title=SELF_AET)
    ae.add_requested_context(StudyRootQueryRetrieveInformationModelMove)
    assoc = ae.associate(PROXY_HOST, PROXY_DICOM, ae_title=PROXY_AET)
    ok = False
    if assoc.is_established:
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.StudyInstanceUID = study_uid
        for status, _ in assoc.send_c_move(ds, SELF_AET, StudyRootQueryRetrieveInformationModelMove):
            if status and status.Status in (0x0000, 0xFF00):
                ok = True
        assoc.release()
    ac.record_event(result, "cmove", phase=phase, study=study_uid, accepted=ok)


def cstore_to_proxy():
    ds = Dataset()
    ds.file_meta = Dataset()
    sop = generate_uid()
    ds.file_meta.MediaStorageSOPClassUID = CTImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = sop
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = sop
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.PatientName = "Pushed^FromClient"
    ds.PatientID = "PUSH001"
    ds.Modality = "CT"
    ds.Rows = ds.Columns = 16
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelData = np.zeros((16, 16), dtype=np.uint16).tobytes()
    ds.is_little_endian = True
    ds.is_implicit_VR = False

    ae = AE(ae_title=SELF_AET)
    ae.add_requested_context(CTImageStorage, ExplicitVRLittleEndian)
    assoc = ae.associate(PROXY_HOST, PROXY_DICOM, ae_title=PROXY_AET)
    accepted, queryable = False, False
    if assoc.is_established:
        st = assoc.send_c_store(ds)
        accepted = bool(st) and st.Status == 0x0000
        assoc.release()
        time.sleep(2)
        url = "http://%s:%d/dicom-web/studies?StudyInstanceUID=%s" % (
            PROXY_HOST, PROXY_REST, ds.StudyInstanceUID)
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                queryable = b"00080020" in r.read() or r.status == 200
        except Exception:
            queryable = False
    ac.record_event(result, "cstore_to_proxy", accepted=accepted, queryable=queryable)


def qido_cached(study_uid):
    url = "http://%s:%d/dicom-web/studies?StudyInstanceUID=%s" % (PROXY_HOST, PROXY_REST, study_uid)
    ok = False
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            ok = r.status == 200 and len(r.read()) > 2
    except Exception:
        ok = False
    ac.record_event(result, "qido", study=study_uid, ok=ok)


def probe_rejected(host, port, called_aet, calling_aet, kind):
    ae = AE(ae_title=calling_aet)
    ae.add_requested_context(Verification)
    assoc = ae.associate(host, port, ae_title=called_aet)
    rejected = not assoc.is_established
    if assoc.is_established:
        assoc.release()
    ac.record_event(result, kind, rejected=rejected)


def main():
    server = start_scp()
    try:
        if ROLE == "clienta":
            # S2 negative
            probe_rejected(PACS_HOST, PACS_DICOM, PACS_AET, SELF_AET, "direct_pacs_probe")
            probe_rejected(PROXY_HOST, PROXY_DICOM, PROXY_AET, "GHOST", "spoof_proxy_probe")
            # S1 routing + S3 pass-through (cache warm after the move)
            _current_phase["name"] = "s1"
            cmove("s1", STUDY[1])
            cfind_cyrillic(STUDY[1], study_plan.CYRILLIC_NAME)
            qido_cached(STUDY[1])
            cstore_to_proxy()
            # S4 different studies (A=study2)
            _current_phase["name"] = "s4_diff"
            ac.barrier_signal(BARRIER_DIR, "a_s4")
            ac.barrier_wait_all(BARRIER_DIR, ["a_s4", "b_s4"], timeout=180)
            cmove("s4_diff", STUDY[2])
            # S5 same study (both = study1)
            _current_phase["name"] = "s5_same"
            ac.barrier_signal(BARRIER_DIR, "a_s5")
            ac.barrier_wait_all(BARRIER_DIR, ["a_s5", "b_s5"], timeout=180)
            cmove("s5_same", STUDY[1])
        else:  # clientb
            _current_phase["name"] = "s1"   # idle: SCP up, receives nothing
            time.sleep(1)
            _current_phase["name"] = "s4_diff"
            ac.barrier_signal(BARRIER_DIR, "b_s4")
            ac.barrier_wait_all(BARRIER_DIR, ["a_s4", "b_s4"], timeout=180)
            cmove("s4_diff", STUDY[3])
            _current_phase["name"] = "s5_same"
            ac.barrier_signal(BARRIER_DIR, "b_s5")
            ac.barrier_wait_all(BARRIER_DIR, ["a_s5", "b_s5"], timeout=180)
            cmove("s5_same", STUDY[1])
        time.sleep(5)  # let final sub-operations land on the SCP
    finally:
        ac.write_result(RESULT_PATH, result)
        ac.barrier_signal(BARRIER_DIR, ROLE + "_phases_done")
        server.shutdown()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create `staging/vm-net/roles/proxy_provision.sh`**

```bash
#!/bin/bash
# Runs inside the proxy VM (Buster). Installs the real LSB Orthanc + buster-3.7 plugin
# via deploy/install.sh, starts Orthanc with the vm-net proxy config, waits for both
# clients to finish, then exercises evict.py (TTL delete + fill WARN) and records proxy.json.
set -x
R=/repo/staging/.data/vm-net
DATA="$R"
BARRIER="$R/barrier"
mkdir -p "$DATA" "$BARRIER"
exec > >(tee -a "$DATA/proxy-provision.log" /dev/ttyS0) 2>&1

. /etc/os-release; echo "proxy host ID=$ID VERSION_ID=$VERSION_ID"
cat > /etc/apt/sources.list <<'SRC'
deb http://archive.debian.org/debian buster main
deb http://archive.debian.org/debian-security buster/updates main
SRC
apt-get -o Acquire::Check-Valid-Until=false update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
  curl ca-certificates python3-pip libpython3.7

DEST=/opt/orthanc bash /repo/deploy/install.sh
mkdir -p /opt/clarinet
cp /repo/plugin/clarinet_proxy.py /repo/plugin/proxy_core.py /opt/clarinet/
mkdir -p /var/lib/orthanc-proxy

/opt/orthanc/bin/Orthanc /repo/staging/vm-net/config/proxy.json \
  > "$DATA/proxy-orthanc.log" 2>&1 &
ORTHANC_PID=$!
sleep 8
echo "--- proxy /plugins ---"; curl -s http://localhost:8042/plugins; echo

# wait until both clients signalled completion (max ~10 min)
for _ in $(seq 1 120); do
  [ -f "$BARRIER/ready_clienta_phases_done" ] && [ -f "$BARRIER/ready_clientb_phases_done" ] && break
  sleep 5
done

BEFORE=$(curl -s http://localhost:8042/statistics | python3 -c 'import sys,json;print(json.load(sys.stdin).get("CountStudies",0))')

# (b) fill-WARN run: nothing expires (huge TTL), tiny cap -> fill >= WARN_FILL -> WARN logged
PROXY_CORE_DIR=/opt/clarinet ORTHANC_URL=http://localhost:8042 \
  TTL_SECONDS=100000 MAX_STORAGE_MB=1 WARN_FILL=0.8 \
  python3 /repo/deploy/evict.py > "$DATA/evict-warn.log" 2>&1
WARN=$(grep -c "storage fill" "$DATA/evict-warn.log" || true)

# (a) TTL run: TTL=1, after a short wait everything is expired -> deleted
sleep 2
PROXY_CORE_DIR=/opt/clarinet ORTHANC_URL=http://localhost:8042 \
  TTL_SECONDS=1 MAX_STORAGE_MB=100000 \
  python3 /repo/deploy/evict.py > "$DATA/evict-ttl.log" 2>&1
AFTER=$(curl -s http://localhost:8042/statistics | python3 -c 'import sys,json;print(json.load(sys.stdin).get("CountStudies",0))')

python3 - "$DATA/proxy.json" "$BEFORE" "$AFTER" "$WARN" <<'PY'
import json, sys
path, before, after, warn = sys.argv[1:5]
# pacs C-MOVE association count for study1, parsed from Orthanc's own log
pacs_moves = 0
try:
    with open("/repo/staging/.data/vm-net/proxy-orthanc.log", encoding="utf-8", errors="ignore") as f:
        pacs_moves = sum(1 for ln in f if "/modalities/pacs/move" in ln)
except OSError:
    pass
json.dump({
    "role": "proxy",
    "studies_before_evict": int(before),
    "studies_after_evict": int(after),
    "fill_warn_logged": int(warn) > 0,
    "pacs_move_jobs_observed": pacs_moves,
}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
PY

kill "$ORTHANC_PID" 2>/dev/null || true
touch "$BARRIER/ready_proxy_done"
sync
touch "$DATA/proxy-done"
```

- [ ] **Step 3: Compile-check the agent and lint both files**

Run: `python3 -m py_compile staging/vm-net/roles/client_agent.py && bash -n staging/vm-net/roles/proxy_provision.sh && uvx ruff check staging/vm-net && echo OK`
Expected: `OK` (behavioral validation happens in Task 8).

- [ ] **Step 4: Commit**

```bash
git add staging/vm-net/roles
git commit -m "feat(vm-net): pynetdicom client agent + proxy provisioning/evict driver"
```

---

### Task 5: Host-side assertions over collected JSON

**Files:**
- Create: `staging/vm-net/vmnet_assert.py` (pure — no DICOM)
- Create: `staging/vm-net/test_vm_net.py` (pytest entrypoint, run explicitly with `VMNET_DATA`)
- Test: `tests/test_vmnet_assert.py`

**Interfaces:**
- Consumes: per-role JSON written by Task 4 (`received[phase][study]={count,from}`, `events[]`) and `proxy.json`.
- Produces:
  - `load_results(data_dir) -> dict[str, dict]` keyed by filename stem.
  - `received_count(result, phase, study) -> int`.
  - `event(result, kind) -> dict | None` (first matching event).
  - `check_s1/s2/s3/s4/s5/s6(...) -> list[str]` each returning a list of failure strings (empty = pass).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_vmnet_assert.py
import vmnet_assert as va

STUDY1, STUDY2, STUDY3 = "1.2.826.0.1.3680043.8.498.1", "...2", "...3"


def _client(role, received=None, events=None):
    return {"role": role, "aet": role.upper(),
            "received": received or {}, "events": events or []}


def test_s1_pass_when_a_gets_all_and_b_gets_none():
    a = _client("clienta", {"s1": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s1": {}})
    assert va.check_s1(a, b, STUDY1, 1000) == []


def test_s1_fails_when_b_also_received():
    a = _client("clienta", {"s1": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s1": {STUDY1: {"count": 5, "from": "CLARINETPROXY"}}})
    assert va.check_s1(a, b, STUDY1, 1000)  # non-empty -> failure


def test_s2_pass_when_both_probes_rejected():
    a = _client("clienta", events=[
        {"kind": "direct_pacs_probe", "rejected": True},
        {"kind": "spoof_proxy_probe", "rejected": True},
    ])
    assert va.check_s2(a) == []


def test_s2_fails_when_direct_pacs_accepted():
    a = _client("clienta", events=[
        {"kind": "direct_pacs_probe", "rejected": False},
        {"kind": "spoof_proxy_probe", "rejected": True},
    ])
    assert va.check_s2(a)


def test_s3_pass_on_cyrillic_and_qido_and_store():
    a = _client("clienta", events=[
        {"kind": "cfind_cyrillic", "name": "Иванов^Пётр", "ok": True},
        {"kind": "qido", "study": STUDY1, "ok": True},
        {"kind": "cstore_to_proxy", "accepted": True, "queryable": True},
    ])
    assert va.check_s3(a) == []


def test_s4_pass_no_cross_contamination():
    a = _client("clienta", {"s4_diff": {STUDY2: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s4_diff": {STUDY3: {"count": 1000, "from": "CLARINETPROXY"}}})
    assert va.check_s4(a, b, STUDY2, STUDY3, 1000) == []


def test_s4_fails_when_a_sees_b_study():
    a = _client("clienta", {"s4_diff": {
        STUDY2: {"count": 1000, "from": "CLARINETPROXY"},
        STUDY3: {"count": 3, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s4_diff": {STUDY3: {"count": 1000, "from": "CLARINETPROXY"}}})
    assert va.check_s4(a, b, STUDY2, STUDY3, 1000)


def test_s5_pass_when_both_get_full_study():
    a = _client("clienta", {"s5_same": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s5_same": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    assert va.check_s5(a, b, STUDY1, 1000) == []


def test_s6_pass_on_ttl_delete_and_warn():
    proxy = {"role": "proxy", "studies_before_evict": 3,
             "studies_after_evict": 0, "fill_warn_logged": True,
             "pacs_move_jobs_observed": 4}
    assert va.check_s6(proxy) == []


def test_s6_fails_when_nothing_evicted():
    proxy = {"role": "proxy", "studies_before_evict": 3,
             "studies_after_evict": 3, "fill_warn_logged": True}
    assert va.check_s6(proxy)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_vmnet_assert.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vmnet_assert'`.

- [ ] **Step 3: Create `staging/vm-net/vmnet_assert.py`**

```python
"""Pure host-side assertions over collected vm-net JSON. No DICOM stack.

Each check_* returns a list of human-readable failure strings; empty == pass."""

import glob
import json
import os


def load_results(data_dir):
    out = {}
    for p in glob.glob(os.path.join(data_dir, "*.json")):
        with open(p, encoding="utf-8") as f:
            out[os.path.splitext(os.path.basename(p))[0]] = json.load(f)
    return out


def received_count(result, phase, study):
    return result.get("received", {}).get(phase, {}).get(study, {}).get("count", 0)


def _studies_in(result, phase):
    return set(result.get("received", {}).get(phase, {}).keys())


def event(result, kind):
    for e in result.get("events", []):
        if e.get("kind") == kind:
            return e
    return None


def check_s1(clienta, clientb, study, n):
    fails = []
    if received_count(clienta, "s1", study) != n:
        fails.append("S1: clientA got %d of %d for %s" % (
            received_count(clienta, "s1", study), n, study))
    if _studies_in(clientb, "s1"):
        fails.append("S1: clientB received %s in s1 (expected nothing)" % _studies_in(clientb, "s1"))
    return fails


def check_s2(clienta):
    fails = []
    for kind in ("direct_pacs_probe", "spoof_proxy_probe"):
        e = event(clienta, kind)
        if e is None or not e.get("rejected"):
            fails.append("S2: %s was not rejected (%r)" % (kind, e))
    return fails


def check_s3(clienta):
    fails = []
    cf = event(clienta, "cfind_cyrillic")
    if cf is None or not cf.get("ok"):
        fails.append("S3: cyrillic C-FIND failed (%r)" % cf)
    q = event(clienta, "qido")
    if q is None or not q.get("ok"):
        fails.append("S3: QIDO on cached study failed (%r)" % q)
    cs = event(clienta, "cstore_to_proxy")
    if cs is None or not (cs.get("accepted") and cs.get("queryable")):
        fails.append("S3: client C-STORE not accepted/queryable on proxy (%r)" % cs)
    return fails


def check_s4(clienta, clientb, study_a, study_b, n):
    fails = []
    if received_count(clienta, "s4_diff", study_a) != n:
        fails.append("S4: clientA incomplete for %s" % study_a)
    if received_count(clientb, "s4_diff", study_b) != n:
        fails.append("S4: clientB incomplete for %s" % study_b)
    if _studies_in(clienta, "s4_diff") != {study_a}:
        fails.append("S4: clientA cross-contaminated: %s" % _studies_in(clienta, "s4_diff"))
    if _studies_in(clientb, "s4_diff") != {study_b}:
        fails.append("S4: clientB cross-contaminated: %s" % _studies_in(clientb, "s4_diff"))
    return fails


def check_s5(clienta, clientb, study, n):
    fails = []
    if received_count(clienta, "s5_same", study) != n:
        fails.append("S5: clientA incomplete for shared %s" % study)
    if received_count(clientb, "s5_same", study) != n:
        fails.append("S5: clientB incomplete for shared %s" % study)
    return fails  # proxy->PACS fetch count is recorded as an observation, not asserted


def check_s6(proxy):
    fails = []
    if not (proxy.get("studies_before_evict", 0) > proxy.get("studies_after_evict", 0)):
        fails.append("S6: TTL eviction did not reduce study count (%r -> %r)" % (
            proxy.get("studies_before_evict"), proxy.get("studies_after_evict")))
    if not proxy.get("fill_warn_logged"):
        fails.append("S6: storage-fill WARN was not logged")
    return fails
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_vmnet_assert.py -v`
Expected: PASS (10 passed).

- [ ] **Step 5: Create `staging/vm-net/test_vm_net.py` (integration entrypoint)**

```python
"""Host-side e2e gate. Run AFTER run.sh collected the VM results:

    VMNET_DATA=staging/.data/vm-net uv run pytest staging/vm-net/test_vm_net.py -v

Not in `testpaths`, so a bare `uv run pytest` never collects it."""

import os

import pytest

import study_plan
import vmnet_assert as va

DATA = os.environ.get("VMNET_DATA", "staging/.data/vm-net")
PLAN = study_plan.build_study_plan(3, int(os.environ.get("INSTANCES_PER_STUDY", "1000")))
S1, S2, S3 = (s["StudyInstanceUID"] for s in PLAN)
N = len(PLAN[0]["SOPInstanceUIDs"])


@pytest.fixture(scope="session")
def results():
    r = va.load_results(DATA)
    for need in ("clienta", "clientb", "proxy"):
        if need not in r:
            pytest.fail("missing %s.json in %s (collected: %s)" % (need, DATA, sorted(r)))
    return r


def test_s1_cmove_routing(results):
    assert va.check_s1(results["clienta"], results["clientb"], S1, N) == []


def test_s2_aet_isolation(results):
    assert va.check_s2(results["clienta"]) == []


def test_s3_pass_through(results):
    assert va.check_s3(results["clienta"]) == []


def test_s4_concurrent_different_studies(results):
    assert va.check_s4(results["clienta"], results["clientb"], S2, S3, N) == []


def test_s5_concurrent_same_study(results):
    assert va.check_s5(results["clienta"], results["clientb"], S1, N) == []
    print("OBSERVED proxy->PACS move jobs:", results["proxy"].get("pacs_move_jobs_observed"))


def test_s6_eviction(results):
    assert va.check_s6(results["proxy"]) == []
```

- [ ] **Step 6: Run the entrypoint against a crafted fixture dir to prove the wiring**

```bash
mkdir -p /tmp/vmnet-fix
python3 - <<'PY'
import json
N=1000; S1="1.2.826.0.1.3680043.8.498.1"; S2=S1[:-1]+"2"; S3=S1[:-1]+"3"
P="CLARINETPROXY"
json.dump({"role":"clienta","aet":"CLIENTA","received":{
  "s1":{S1:{"count":N,"from":P}},
  "s4_diff":{S2:{"count":N,"from":P}},
  "s5_same":{S1:{"count":N,"from":P}}},
  "events":[{"kind":"direct_pacs_probe","rejected":True},
            {"kind":"spoof_proxy_probe","rejected":True},
            {"kind":"cfind_cyrillic","name":"Иванов^Пётр","ok":True},
            {"kind":"qido","study":S1,"ok":True},
            {"kind":"cstore_to_proxy","accepted":True,"queryable":True}]},
  open("/tmp/vmnet-fix/clienta.json","w",encoding="utf-8"),ensure_ascii=False)
json.dump({"role":"clientb","aet":"CLIENTB","received":{
  "s1":{}, "s4_diff":{S3:{"count":N,"from":P}}, "s5_same":{S1:{"count":N,"from":P}}},
  "events":[]}, open("/tmp/vmnet-fix/clientb.json","w",encoding="utf-8"),ensure_ascii=False)
json.dump({"role":"proxy","studies_before_evict":3,"studies_after_evict":0,
  "fill_warn_logged":True,"pacs_move_jobs_observed":4},
  open("/tmp/vmnet-fix/proxy.json","w",encoding="utf-8"),ensure_ascii=False)
PY
VMNET_DATA=/tmp/vmnet-fix uv run pytest staging/vm-net/test_vm_net.py -v
```
Expected: PASS (6 passed) — proves `test_vm_net.py` + `vmnet_assert` + `study_plan` agree on shapes.

- [ ] **Step 7: Commit**

```bash
git add staging/vm-net/vmnet_assert.py staging/vm-net/test_vm_net.py tests/test_vmnet_assert.py
git commit -m "test(vm-net): host-side scenario assertions over collected JSON"
```

---

### Task 6: Golden-image builder

**Files:**
- Create: `staging/vm-net/build-golden.sh`

**Interfaces:**
- Produces (cached in `WORK`, default `/tmp/orthanc-proxy-vm-net`): `pacs-golden.qcow2` (distro Orthanc + 3×1000 instances baked in) and `client-golden.qcow2` (pydicom + pynetdicom). Consumed by `run.sh` (Task 7).
- Note: behavioral validation is Task 8. Host verification here is `shellcheck`/`bash -n` + a `--check` prerequisite probe.

- [ ] **Step 1: Create `staging/vm-net/build-golden.sh`**

```bash
#!/usr/bin/env bash
# Build the two cached golden images for the vm-net harness. Run once (re-run to
# rebuild, e.g. when gen_studies.py or the instance count changes). run.sh boots
# fresh overlays over these; the proxy is NOT golden — it is rebuilt every run.
#
#   pacs-golden.qcow2    Debian Buster + orthanc + python3-pydicom, 3x1000 instances baked into /var/lib/orthanc/db
#   client-golden.qcow2  Debian Buster + pydicom + pynetdicom (the SCU agent runtime)
#
# Usage: bash staging/vm-net/build-golden.sh [--check]
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
. "$REPO/staging/vm-net/net.env"
WORK="${WORK:-/tmp/orthanc-proxy-vm-net}"
TIMEOUT="${TIMEOUT:-2400}"
INSTANCES="${INSTANCES_PER_STUDY:-1000}"
IMG_URL="https://cloud.debian.org/images/cloud/buster/latest/debian-10-generic-amd64.qcow2"
BASE="$WORK/buster.qcow2"

if [ "${1:-}" = "--check" ]; then
  for t in qemu-system-x86_64 qemu-img cloud-localds; do command -v "$t" >/dev/null || { echo "missing $t"; exit 1; }; done
  [ -e /dev/kvm ] || { echo "missing /dev/kvm"; exit 1; }
  echo "prerequisites OK"; exit 0
fi

mkdir -p "$WORK"
if [ ! -f "$BASE" ]; then
  echo "Downloading Debian 10 Buster cloud image (resumable)..."
  ok=0
  for i in $(seq 1 30); do
    if curl -fL --retry 5 --retry-delay 3 --retry-all-errors -C - "$IMG_URL" -o "$BASE.part"; then ok=1; break; fi
    echo "  ... resuming ($i)"; sleep 3
  done
  [ "$ok" = 1 ] || { echo "image download failed"; exit 1; }
  mv "$BASE.part" "$BASE"
fi

build_one() {
  local name="$1" userdata_file="$2" out="$WORK/$name-golden.qcow2"
  local overlay="$WORK/$name-build.qcow2"
  echo "=== building $name golden ==="
  rm -f "$overlay" "$out"
  qemu-img create -f qcow2 -b "$BASE" -F qcow2 "$overlay" 20G >/dev/null
  cat > "$WORK/meta-data" <<EOF
instance-id: $name-golden
local-hostname: $name
EOF
  cloud-localds "$WORK/seed-$name.iso" "$userdata_file" "$WORK/meta-data"
  qemu-system-x86_64 -enable-kvm -m 2048 -smp 2 \
    -drive file="$overlay",if=virtio,format=qcow2 \
    -drive file="$WORK/seed-$name.iso",if=virtio,format=raw \
    -fsdev local,id=repo,path="$REPO",security_model=mapped-xattr \
    -device virtio-9p-pci,fsdev=repo,mount_tag=repo \
    -netdev user,id=n0 -device virtio-net-pci,netdev=n0 \
    -serial file:"$WORK/$name-build.log" -display none &
  local qpid=$! t=0
  while kill -0 "$qpid" 2>/dev/null; do
    [ "$t" -ge "$TIMEOUT" ] && { echo "golden $name TIMEOUT"; kill "$qpid" 2>/dev/null || true; break; }
    sleep 10; t=$((t + 10))
  done
  wait "$qpid" 2>/dev/null || true
  qemu-img convert -O qcow2 "$overlay" "$out"
  echo "built $out"
}

# --- PACS golden user-data: install orthanc, generate + import studies, poweroff ---
cat > "$WORK/ud-pacs" <<EOF
#cloud-config
write_files:
  - path: /root/seed-pacs.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      set -x
      mkdir -p /repo
      modprobe 9p 2>/dev/null || true; modprobe 9pnet_virtio 2>/dev/null || true
      mount -t 9p -o trans=virtio,version=9p2000.L,msize=104857600,access=any repo /repo
      cat > /etc/apt/sources.list <<'SRC'
      deb http://archive.debian.org/debian buster main
      deb http://archive.debian.org/debian-security buster/updates main
      SRC
      apt-get -o Acquire::Check-Valid-Until=false update
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \\
        orthanc python3-pip python3-numpy curl
      python3 -m pip install "pydicom==2.4.4"
      systemctl stop orthanc || true
      PYTHONPATH=/repo/staging/vm-net python3 /repo/staging/vm-net/gen_studies.py \\
        --out /var/lib/vmnet-studies --studies 3 --instances ${INSTANCES}
      # import into the distro Orthanc store at /var/lib/orthanc/db
      mkdir -p /var/lib/orthanc/db
      cat > /etc/orthanc/orthanc.json <<'OC'
      { "StorageDirectory": "/var/lib/orthanc/db", "IndexDirectory": "/var/lib/orthanc/db",
        "HttpPort": 8042, "DicomAet": "HOSPITALPACS", "RemoteAccessAllowed": true,
        "AuthenticationEnabled": false }
      OC
      chown -R orthanc:orthanc /var/lib/orthanc/db || true
      systemctl start orthanc; sleep 6
      for f in /var/lib/vmnet-studies/*.dcm; do
        curl -s -X POST http://localhost:8042/instances --data-binary @"\$f" >/dev/null
      done
      curl -s http://localhost:8042/statistics
      systemctl stop orthanc; sleep 2
      rm -rf /var/lib/vmnet-studies
      sync
runcmd:
  - [ bash, /root/seed-pacs.sh ]
power_state: { mode: poweroff, timeout: 60, condition: true }
EOF

# --- client golden user-data: pydicom + pynetdicom, poweroff ---
cat > "$WORK/ud-client" <<'EOF'
#cloud-config
write_files:
  - path: /root/seed-client.sh
    permissions: '0755'
    content: |
      #!/bin/bash
      set -x
      cat > /etc/apt/sources.list <<'SRC'
      deb http://archive.debian.org/debian buster main
      deb http://archive.debian.org/debian-security buster/updates main
      SRC
      apt-get -o Acquire::Check-Valid-Until=false update
      DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        python3-pip python3-numpy
      python3 -m pip install "pydicom==2.4.4" "pynetdicom==2.0.2"
      sync
runcmd:
  - [ bash, /root/seed-client.sh ]
power_state: { mode: poweroff, timeout: 60, condition: true }
EOF

build_one pacs "$WORK/ud-pacs"
build_one client "$WORK/ud-client"
echo "goldens ready in $WORK"
```

- [ ] **Step 2: Lint and prerequisite-check**

Run: `bash -n staging/vm-net/build-golden.sh && shellcheck -e SC1091 staging/vm-net/build-golden.sh && bash staging/vm-net/build-golden.sh --check`
Expected: no shellcheck errors; `prerequisites OK` (or a clear "missing ..." if KVM/qemu absent on this host — that is fine, the build runs on a KVM host).

- [ ] **Step 3: Commit**

```bash
git add staging/vm-net/build-golden.sh
git commit -m "feat(vm-net): golden-image builder for PACS + client VMs"
```

---

### Task 7: 4-VM orchestrator

**Files:**
- Create: `staging/vm-net/run.sh`

**Interfaces:**
- Consumes: the goldens from Task 6, `net.env`, configs, `roles/`, the host assertions (Task 5).
- Produces: boots PACS/proxy/clientA/clientB, waits for `proxy-done`, runs the host pytest gate, prints results. Behavioral validation is Task 8.

- [ ] **Step 1: Create `staging/vm-net/run.sh`**

```bash
#!/usr/bin/env bash
# Boot the 4-VM vm-net DICOM network and run the e2e scenarios. PACS + clients boot
# from cached goldens (build-golden.sh); the proxy is rebuilt from deploy/install.sh
# every run. VMs share an isolated socket-multicast LAN; each also has a user-mode NAT
# NIC for package pulls. Roles, coordination and results flow through the 9p-shared
# staging/.data/vm-net/ directory. The host then asserts over the collected JSON.
#
# Usage: bash staging/vm-net/run.sh
# Env:   WORK=<dir>  TIMEOUT=<seconds>  INSTANCES_PER_STUDY=<n>
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
. "$REPO/staging/vm-net/net.env"
WORK="${WORK:-/tmp/orthanc-proxy-vm-net}"
TIMEOUT="${TIMEOUT:-2400}"
INSTANCES="${INSTANCES_PER_STUDY:-1000}"
BUSTER="$WORK/buster.qcow2"
DATA="$REPO/staging/.data/vm-net"
BARRIER="$DATA/barrier"

for img in "$WORK/pacs-golden.qcow2" "$WORK/client-golden.qcow2" "$BUSTER"; do
  [ -f "$img" ] || { echo "missing $img — run: bash staging/vm-net/build-golden.sh"; exit 1; }
done

rm -rf "$DATA"; mkdir -p "$DATA" "$BARRIER"
PIDS=()
cleanup() { for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT

HOSTS=$'10.0.0.10 pacs\n10.0.0.20 proxy\n10.0.0.31 clienta\n10.0.0.32 clientb'

# boot_node <name> <base-image> <ip> <lan_mac> <nat_mac> <mem> <run-script-body>
boot_node() {
  local name="$1" base="$2" ip="$3" lanmac="$4" natmac="$5" mem="$6" body="$7"
  local overlay="$WORK/$name-overlay.qcow2"
  rm -f "$overlay"
  qemu-img create -f qcow2 -b "$base" -F qcow2 "$overlay" 20G >/dev/null

  cat > "$WORK/meta-$name" <<EOF
instance-id: vmnet-$name
local-hostname: $name
EOF
  cat > "$WORK/netcfg-$name" <<EOF
version: 2
ethernets:
  nat:
    match: { macaddress: "$natmac" }
    dhcp4: true
  lan:
    match: { macaddress: "$lanmac" }
    addresses: [ $ip/24 ]
EOF
  { echo "#cloud-config"
    echo "bootcmd:"
    echo "  - [ sh, -c, 'printf \"%b\\n\" \"$HOSTS\" >> /etc/hosts' ]"
    echo "write_files:"
    echo "  - path: /root/role.sh"
    echo "    permissions: '0755'"
    echo "    content: |"
    printf '%s\n' "$body" | sed 's/^/      /'
    echo "runcmd:"
    echo "  - [ bash, /root/role.sh ]"
  } > "$WORK/ud-$name"

  cloud-localds --network-config "$WORK/netcfg-$name" "$WORK/seed-$name.iso" "$WORK/ud-$name" "$WORK/meta-$name"

  qemu-system-x86_64 -enable-kvm -m "$mem" -smp 2 \
    -drive file="$overlay",if=virtio,format=qcow2 \
    -drive file="$WORK/seed-$name.iso",if=virtio,format=raw \
    -fsdev local,id=repo,path="$REPO",security_model=mapped-xattr \
    -device virtio-9p-pci,fsdev=repo,mount_tag=repo \
    -netdev user,id=nat -device virtio-net-pci,netdev=nat,mac="$natmac" \
    -netdev socket,mcast="$MCAST",id=lan -device virtio-net-pci,netdev=lan,mac="$lanmac" \
    -serial file:"$DATA/$name-console.log" -display none -pidfile "$WORK/$name.pid" &
  PIDS+=($!)
  echo "booted $name ($ip)"
}

MNT='mkdir -p /repo; modprobe 9p 2>/dev/null||true; modprobe 9pnet_virtio 2>/dev/null||true; mount -t 9p -o trans=virtio,version=9p2000.L,msize=104857600,access=any repo /repo'

# --- PACS: start distro Orthanc with the tightened vm-net config (data is baked in) ---
boot_node pacs "$WORK/pacs-golden.qcow2" 10.0.0.10 "$PACS_LAN_MAC" "$PACS_NAT_MAC" 3072 \
"#!/bin/bash
set -x
$MNT
systemctl stop orthanc || true
/usr/sbin/Orthanc /repo/staging/vm-net/config/pacs.json > /repo/staging/.data/vm-net/pacs-orthanc.log 2>&1 &
sleep 6
curl -s http://localhost:8042/statistics > /repo/staging/.data/vm-net/pacs-stats.json
touch /repo/staging/.data/vm-net/pacs-done"

# --- proxy: rebuild the real LSB artifact, then drive evict (separate provisioning script) ---
boot_node proxy "$BUSTER" 10.0.0.20 "$PROXY_LAN_MAC" "$PROXY_NAT_MAC" 4096 \
"#!/bin/bash
$MNT
bash /repo/staging/vm-net/roles/proxy_provision.sh"

# --- clients: run the SCU agent with role env ---
client_body() {  # $1 role, $2 self_aet, $3 scp_port
  echo "#!/bin/bash
set -x
$MNT
export ROLE=$1 SELF_AET=$2 SCP_PORT=$3
export PROXY_HOST=proxy PROXY_AET=$PROXY_AET PROXY_DICOM=$PROXY_DICOM PROXY_REST=$PROXY_REST
export PACS_HOST=pacs PACS_AET=$PACS_AET PACS_DICOM=$PACS_DICOM
export BARRIER_DIR=/repo/staging/.data/vm-net/barrier
export RESULT_PATH=/repo/staging/.data/vm-net/$1.json
export INSTANCES_PER_STUDY=$INSTANCES
python3 /repo/staging/vm-net/roles/client_agent.py
touch /repo/staging/.data/vm-net/$1-agent-done"
}
boot_node clienta "$WORK/client-golden.qcow2" 10.0.0.31 "$CLIENTA_LAN_MAC" "$CLIENTA_NAT_MAC" 1024 "$(client_body clienta "$CLIENTA_AET" "$CLIENTA_SCP")"
boot_node clientb "$WORK/client-golden.qcow2" 10.0.0.32 "$CLIENTB_LAN_MAC" "$CLIENTB_NAT_MAC" 1024 "$(client_body clientb "$CLIENTB_AET" "$CLIENTB_SCP")"

echo "Waiting for proxy-done (timeout ${TIMEOUT}s)..."
elapsed=0
while [ ! -f "$DATA/proxy-done" ]; do
  [ "$elapsed" -ge "$TIMEOUT" ] && { echo "TIMEOUT"; break; }
  sleep 10; elapsed=$((elapsed + 10))
done

echo "================ vm-net results ================"
ls -1 "$DATA"/*.json 2>/dev/null || echo "(no result JSON produced)"
if [ -f "$DATA/proxy-done" ]; then
  VMNET_DATA="$DATA" INSTANCES_PER_STUDY="$INSTANCES" \
    uv run pytest "$REPO/staging/vm-net/test_vm_net.py" -v || true
else
  echo "run did NOT complete; inspect $DATA/*-console.log"; exit 1
fi
```

- [ ] **Step 2: Lint**

Run: `bash -n staging/vm-net/run.sh && shellcheck -e SC1091,SC2034,SC2206 staging/vm-net/run.sh && echo OK`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add staging/vm-net/run.sh
git commit -m "feat(vm-net): 4-VM orchestrator (mcast LAN, 9p coordination, host gate)"
```

---

### Task 8: End-to-end run + README

**Files:**
- Create: `staging/vm-net/README.md`

**Interfaces:**
- Consumes: everything above. This is the capstone — the real boot on a KVM host.

- [ ] **Step 1: Build the goldens (one-time, on a KVM host with internet)**

Run: `bash staging/vm-net/build-golden.sh`
Expected: `goldens ready in <WORK>`; `pacs-golden.qcow2` and `client-golden.qcow2` exist in `WORK`. Inspect `"$WORK"/pacs-build.log` for the imported `CountInstances` (≈3000).

- [ ] **Step 2: Run the full harness**

Run: `bash staging/vm-net/run.sh`
Expected: `test_vm_net.py` reports **6 passed**. If a scenario fails, read `staging/.data/vm-net/*-console.log`, `proxy-provision.log`, and the per-role JSON; fix the responsible task and re-run. The S5 test prints the observed proxy→PACS move-job count.

- [ ] **Step 3: Triage notes for likely first-run issues (fix in the owning task, then re-run)**

- mcast LAN silent: confirm both clients and the proxy got their `lan` static IPs (`ip addr` via console log); a wrong MAC in `net.env`/netcfg drops the NIC match.
- proxy can't reach PACS: check `/etc/hosts` was appended (bootcmd) and `config/proxy.json` modality host `pacs` resolves.
- empty C-FIND: PACS storage path mismatch — the golden baked into `/var/lib/orthanc/db` but `pacs.json` must point `StorageDirectory`/`IndexDirectory` there (Task 1 config).
- S6 no WARN: the fill run needs `MAX_STORAGE_MB` low enough that `TotalDiskSizeMB/MAX ≥ WARN_FILL`; lower it in `proxy_provision.sh`.

- [ ] **Step 4: Write `staging/vm-net/README.md`**

```markdown
# vm-net — multi-machine e2e harness

Four QEMU/KVM VMs on an isolated socket-multicast LAN, exercising `clarinet_proxy.py`
across real machine boundaries:

| node | base | role |
|------|------|------|
| `pacs` 10.0.0.10 | golden (distro Orthanc, studies baked in) | upstream PACS `HOSPITALPACS` |
| `proxy` 10.0.0.20 | **rebuilt every run** (Buster + `deploy/install.sh`) | the proxy under test `CLARINETPROXY` |
| `clienta` 10.0.0.31 | golden (pynetdicom) | SCU `CLIENTA`, registered only in the proxy |
| `clientb` 10.0.0.32 | golden (pynetdicom) | SCU `CLIENTB`, registered only in the proxy |

```bash
bash staging/vm-net/build-golden.sh   # once (cached in WORK); rebuild when studies change
bash staging/vm-net/run.sh            # boot all 4, run scenarios, assert on the host
```

- Rootless: socket-multicast L2 (`230.0.0.1:1234`) + per-VM user-mode NAT for package pulls. No bridge/TAP, no SSH.
- Coordination + results go through the 9p-shared `staging/.data/vm-net/` (gitignored): per-role JSON, barrier files, console logs.
- The host gate (`test_vm_net.py`) asserts over the collected JSON — it does not speak DICOM itself.

## Scenarios

S1 C-MOVE routing to the requesting client · S2 AET isolation (direct-to-PACS and spoofed-AET rejected)
· S3 pass-through (Cyrillic C-FIND, client C-STORE cached + QIDO/WADO) · S4 concurrent different studies
· S5 concurrent same study (correctness asserted; proxy→PACS fetch count recorded) · S6 TTL eviction +
storage-fill WARN.

## Relation to the other harnesses

`staging/vm/` tests the logic on Docker; `staging/vm-lsb/` tests the LSB artifact on one Buster host
over loopback; **this** harness adds the real multi-machine topology and client-only-via-proxy isolation.
Astra ЗПС/МКЦ/ГОСТ layers remain out of scope (need a licensed Astra image). The proxy has no
C-STORE→PACS forwarding — S3 asserts caching, not forwarding.

## RAM budget (≤ 24 GB)

proxy 4096 MB · pacs 3072 MB · clientA/B 1024 MB each ≈ 9 GB guests; the rest is qemu/9p/page-cache headroom.
```

- [ ] **Step 5: Commit the README**

```bash
git add staging/vm-net/README.md
git commit -m "docs(vm-net): README for the multi-machine e2e harness"
```

---

## Self-Review

**1. Spec coverage:**
- §2/§9 S1–S6 → Task 5 `check_s1..s6` + Task 4 agent/proxy actors + Task 8 run. ✓
- §3 D1 topology / D5 mcast network → Task 1 `net.env`, Task 7 `run.sh`. ✓
- §3 D2 pynetdicom clients → Task 4 `client_agent.py`. ✓
- §3 D3 proxy rebuilt every run → Task 4 `proxy_provision.sh`, Task 7 boots `proxy` from `buster.qcow2`. ✓
- §3 D4 golden PACS+clients, studies baked → Task 6 `build-golden.sh`. ✓
- §3 D6 host-side assertions over JSON → Task 5. ✓
- §3 D7 barrier coordination → Task 3 `agent_core` + Task 4 phases. ✓
- §6 large studies (≥1000) → Task 2 `study_plan`/`gen_studies`. ✓
- §7 isolation configs → Task 1 + `tests/test_vmnet_config.py`. ✓
- §8 RAM budget → Task 7 per-node `-m`. ✓
- §11 C-STORE-not-forwarded → Task 4 `cstore_to_proxy` asserts cached+queryable, Task 5 `check_s3`. ✓

**2. Placeholder scan:** No TBD/TODO; every code step contains complete code; every command has expected output. ✓

**3. Type consistency:** `received[phase][study]={count,from}` is produced by `agent_core.record_received` (Task 3), written by `client_agent` (Task 4), and read by `vmnet_assert.received_count`/`_studies_in` (Task 5) — same shape. `study_plan.build_study_plan` signature/keys identical in Tasks 2, 4, 5. Barrier names (`a_s4`,`b_s4`,`a_s5`,`b_s5`,`<role>_phases_done`) match between `client_agent` (Task 4) and `proxy_provision.sh` (Task 4). Config keys asserted in Task 1 match the JSON authored in Task 1. ✓
