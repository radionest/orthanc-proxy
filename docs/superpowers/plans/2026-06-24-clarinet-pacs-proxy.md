# clarinet-pacs-proxy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an Orthanc + Python-plugin DICOM/DICOMweb pass-through proxy that fronts a C-FIND/C-MOVE-only hospital PACS under one AET (`CLARINETPROXY`), so unregistered Clarinet workers and an OHIF viewer can query/retrieve through it.

**Architecture:** A Python plugin replaces Orthanc's C-FIND/C-MOVE SCP: `OnFind` forwards C-FIND to the PACS via the native REST modality API; a `MoveDriver` pulls studies from the PACS with a C-MOVE-to-self and either forwards them to a worker (C-STORE) or keeps them cached for OHIF/C-GET. All retrieved data is transit cache on a LUKS-SSD, cleaned by a TTL timer. Pure logic lives in `proxy_core.py` (unit-tested); Orthanc glue lives in `clarinet_proxy.py` (tested with a fake `orthanc` module and end-to-end on a docker staging harness).

**Tech Stack:** Orthanc 1.12.11 (LSB binaries) + Orthanc Python plugin (legacy `RegisterFindCallback` + `RegisterMoveCallback2`) + DicomWeb plugin; Python 3; pytest; docker-compose + pydicom/requests for staging; systemd + cryptsetup for deploy.

**Spec:** `docs/superpowers/specs/2026-06-24-clarinet-pacs-proxy-design.md` (read it; this plan implements it verbatim).

## Global Constraints

Copied from the spec — every task inherits these:

- Orthanc core **1.12.11** LSB; plugin callbacks **`RegisterFindCallback`** (4-arg `answers, query, issuerAet, calledAet`) + **`RegisterMoveCallback2`** (4-callback dict form). Do **not** use `*Callback2/3`.
- One DICOM AET: **`CLARINETPROXY`**. Upstream PACS modality alias: **`pacs`**.
- `orthanc.RestApiGet/Post` return **JSON strings** → always `json.loads` / `json.dumps`.
- C-FIND answers must pin **`SpecificCharacterSet = "ISO_IR 192"`** before `CreateDicom` (UTF-8 round-trip for Cyrillic `PatientName`).
- `OnMove` routes on **`TargetAET`** only (never `OriginatorAET`); `TargetAET == CLARINETPROXY` → cache-only, a known worker AET → forward, else raise.
- `get_size` must stay cheap (count via C-FIND, start the pull **async**, do not block); `apply` advances **one** sub-operation per call.
- `/tools/find` and count queries must use the **full UID hierarchy** of the move (per requested item), not `StudyInstanceUID` alone.
- Cache lifecycle: **no immediate-delete**; single TTL eviction (`OnUnitActiveSec=5min`, TTL **1200 s**) + `MaximumStorageSize` **14336 MB** with `MaximumStorageMode "Recycle"` backstop; `MaximumStorageCacheSize` **512 MB**; `StableAge` **20**.
- Security: `HttpBindAddresses ["127.0.0.1"]`, `RemoteAccessAllowed false`, `DicomCheckCalledAet true`, `DicomCheckModalityHost true`, all `DicomAlwaysAllow* false`.
- Out of scope: editing Clarinet, anonymisation, C-GET pass-through, nginx/auth config, alert delivery.

---

### Task 1: Project skeleton + pytest harness

**Files:**
- Create: `pytest.ini`
- Create: `.gitignore`
- Create: `plugin/proxy_core.py` (empty module placeholder)
- Create: `tests/test_smoke.py`

**Interfaces:**
- Produces: a working `pytest` run; the `plugin/` and `tests/` dirs on the import path.

- [ ] **Step 1: Create `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
staging/.data/
*.log
```

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
pythonpath = plugin tests deploy
testpaths = tests
```

- [ ] **Step 3: Create `plugin/proxy_core.py` with constants only**

```python
"""Pure proxy logic — no `import orthanc`, fully unit-testable."""

SELF_AET = "CLARINETPROXY"
UPSTREAM = "pacs"
ANSWER_CHARSET = "ISO_IR 192"

# DICOM unique keys required at each Q/R level (PATIENT level is unsupported).
LEVEL_KEYS = {
    "STUDY": ["StudyInstanceUID"],
    "SERIES": ["StudyInstanceUID", "SeriesInstanceUID"],
    "INSTANCE": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"],
}
```

- [ ] **Step 4: Write the smoke test**

```python
import proxy_core


def test_constants_present():
    assert proxy_core.SELF_AET == "CLARINETPROXY"
    assert proxy_core.UPSTREAM == "pacs"
    assert proxy_core.ANSWER_CHARSET == "ISO_IR 192"
    assert proxy_core.LEVEL_KEYS["SERIES"] == ["StudyInstanceUID", "SeriesInstanceUID"]
```

- [ ] **Step 5: Run tests — expect PASS**

Run: `pytest -q`
Expected: `1 passed`

- [ ] **Step 6: Commit**

```bash
git add .gitignore pytest.ini plugin/proxy_core.py tests/test_smoke.py
git commit -m "chore: project skeleton and pytest harness"
```

---

### Task 2: proxy_core — C-FIND request builder + charset pinning

**Files:**
- Modify: `plugin/proxy_core.py`
- Test: `tests/test_proxy_core.py`

**Interfaces:**
- Produces:
  - `build_find_request(tags) -> (level, query)` — `tags` is `list[(name, value)]`; `QueryRetrieveLevel` → `level` (default `"STUDY"`), all other tags → `query` dict.
  - `pin_charset(content) -> dict` — returns a copy of `content` with `SpecificCharacterSet` forced to `ANSWER_CHARSET`.

- [ ] **Step 1: Write the failing tests**

```python
import proxy_core


def test_build_find_request_splits_level_from_query():
    tags = [("QueryRetrieveLevel", "SERIES"),
            ("PatientID", "42"),
            ("StudyInstanceUID", "")]
    level, query = proxy_core.build_find_request(tags)
    assert level == "SERIES"
    assert query == {"PatientID": "42", "StudyInstanceUID": ""}


def test_build_find_request_defaults_to_study():
    level, query = proxy_core.build_find_request([("PatientName", "")])
    assert level == "STUDY"
    assert query == {"PatientName": ""}


def test_pin_charset_forces_utf8_and_copies():
    content = {"PatientName": "Иванов", "SpecificCharacterSet": "ISO_IR 100"}
    answer = proxy_core.pin_charset(content)
    assert answer["SpecificCharacterSet"] == "ISO_IR 192"
    assert answer["PatientName"] == "Иванов"
    assert content["SpecificCharacterSet"] == "ISO_IR 100"  # original untouched
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: module 'proxy_core' has no attribute 'build_find_request'`)

Run: `pytest tests/test_proxy_core.py -q`

- [ ] **Step 3: Implement in `plugin/proxy_core.py`**

```python
def build_find_request(tags):
    """tags: list[(name, value)] from the C-FIND query object.
    Returns (level, query_dict)."""
    level = "STUDY"
    query = {}
    for name, value in tags:
        if name == "QueryRetrieveLevel":
            level = value or "STUDY"
        else:
            query[name] = value
    return level, query


def pin_charset(content):
    """content: simplified tag->value dict (UTF-8) from /answers/{i}/content?simplify.
    Returns a copy with SpecificCharacterSet pinned to ISO_IR 192 so CreateDicom keeps UTF-8."""
    answer = dict(content)
    answer["SpecificCharacterSet"] = ANSWER_CHARSET
    return answer
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_proxy_core.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add plugin/proxy_core.py tests/test_proxy_core.py
git commit -m "feat: C-FIND request builder and charset pinning"
```

---

### Task 3: proxy_core — move-request parsing + destination routing

**Files:**
- Modify: `plugin/proxy_core.py`
- Test: `tests/test_proxy_core.py`

**Interfaces:**
- Produces:
  - `parse_move_request(request) -> (level, uids)` — `request` is the C-MOVE callback dict; `uids` is a `list[dict]`, one fully-qualified UID dict per requested item (`\\`-separated values expand positionally). Raises `ValueError` for unsupported (e.g. PATIENT) level.
  - `find_alias_for_aet(modalities, aet) -> alias|None` — `modalities` is the `GET /modalities?expand` object (`{alias: {"AET":...}}`).
  - `resolve_destination(target_aet, self_aet, modalities, upstream_alias) -> (mode, worker)` — `("cache", None)` if `target_aet == self_aet`; `("forward", alias)` if it maps to a non-upstream modality; raises `ValueError` otherwise.

- [ ] **Step 1: Write the failing tests**

```python
import pytest
import proxy_core

MODS = {
    "pacs":   {"AET": "PACS",   "Host": "10.0.0.1", "Port": 104},
    "worker": {"AET": "WORKER", "Host": "10.0.0.2", "Port": 4242},
}


def test_parse_move_request_study_level():
    level, uids = proxy_core.parse_move_request(
        {"Level": "STUDY", "StudyInstanceUID": "1.2.3"})
    assert level == "STUDY"
    assert uids == [{"StudyInstanceUID": "1.2.3"}]


def test_parse_move_request_series_full_hierarchy():
    level, uids = proxy_core.parse_move_request(
        {"Level": "SERIES", "StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.9"})
    assert uids == [{"StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.9"}]


def test_parse_move_request_multi_study_splits_positionally():
    level, uids = proxy_core.parse_move_request(
        {"Level": "STUDY", "StudyInstanceUID": "1.2\\1.3"})
    assert uids == [{"StudyInstanceUID": "1.2"}, {"StudyInstanceUID": "1.3"}]


def test_parse_move_request_rejects_patient_level():
    with pytest.raises(ValueError):
        proxy_core.parse_move_request({"Level": "PATIENT", "PatientID": "x"})


def test_resolve_destination_self_is_cache():
    assert proxy_core.resolve_destination("CLARINETPROXY", "CLARINETPROXY", MODS, "pacs") == ("cache", None)


def test_resolve_destination_worker_is_forward():
    assert proxy_core.resolve_destination("WORKER", "CLARINETPROXY", MODS, "pacs") == ("forward", "worker")


def test_resolve_destination_unknown_raises():
    with pytest.raises(ValueError):
        proxy_core.resolve_destination("GHOST", "CLARINETPROXY", MODS, "pacs")


def test_resolve_destination_upstream_is_not_a_valid_worker():
    with pytest.raises(ValueError):
        proxy_core.resolve_destination("PACS", "CLARINETPROXY", MODS, "pacs")


def test_resolve_destination_missing_target_raises():
    with pytest.raises(ValueError):
        proxy_core.resolve_destination("", "CLARINETPROXY", MODS, "pacs")
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_proxy_core.py -q -k "move_request or resolve_destination"`

- [ ] **Step 3: Implement in `plugin/proxy_core.py`**

```python
def parse_move_request(request):
    """request: the C-MOVE callback dict. Returns (level, uids) where uids is a list of
    fully-qualified UID dicts, one per requested item. '\\'-separated values expand positionally."""
    level = request["Level"]
    if level not in LEVEL_KEYS:
        raise ValueError("unsupported C-MOVE level %r (PATIENT not supported)" % level)
    keys = LEVEL_KEYS[level]
    split = {k: (request.get(k, "") or "").split("\\") for k in keys}
    n = max((len(v) for v in split.values()), default=1)
    uids = []
    for i in range(n):
        item = {}
        for k in keys:
            vals = split[k]
            item[k] = vals[i] if i < len(vals) else vals[-1]
        uids.append(item)
    return level, uids


def find_alias_for_aet(modalities, aet):
    for alias, entry in modalities.items():
        if entry.get("AET") == aet:
            return alias
    return None


def resolve_destination(target_aet, self_aet, modalities, upstream_alias):
    if not target_aet:
        raise ValueError("malformed C-MOVE-RQ: missing TargetAET")
    if target_aet == self_aet:
        return ("cache", None)
    alias = find_alias_for_aet(modalities, target_aet)
    if alias is None or alias == upstream_alias:
        raise ValueError("unknown move destination AET %r" % target_aet)
    return ("forward", alias)
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_proxy_core.py -q`
Expected: all passed (12+)

- [ ] **Step 5: Commit**

```bash
git add plugin/proxy_core.py tests/test_proxy_core.py
git commit -m "feat: C-MOVE request parsing and destination routing"
```

---

### Task 4: proxy_core — find/count query bodies + arrival selection

**Files:**
- Modify: `plugin/proxy_core.py`
- Test: `tests/test_proxy_core.py`

**Interfaces:**
- Produces:
  - `local_find_bodies(level, uids) -> list[dict]` — one `POST /tools/find` body per item, `Level "Instance"`, constrained to the item's full UID chain, `Expand True`.
  - `count_query_bodies(level, uids) -> list[dict]` — one `POST /modalities/pacs/query` body per item, `Level "Instance"`, item's full UID chain.
  - `select_unforwarded(found_ids, forwarded) -> str|None` — first id in `found_ids` not in the `forwarded` set.

- [ ] **Step 1: Write the failing tests**

```python
import proxy_core


def test_local_find_bodies_per_item_full_hierarchy():
    uids = [{"StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.9"}]
    bodies = proxy_core.local_find_bodies("SERIES", uids)
    assert bodies == [{
        "Level": "Instance",
        "Query": {"StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.9"},
        "Expand": True,
    }]


def test_count_query_bodies_instance_level():
    uids = [{"StudyInstanceUID": "1.2"}, {"StudyInstanceUID": "1.3"}]
    bodies = proxy_core.count_query_bodies("STUDY", uids)
    assert bodies == [
        {"Level": "Instance", "Query": {"StudyInstanceUID": "1.2"}},
        {"Level": "Instance", "Query": {"StudyInstanceUID": "1.3"}},
    ]


def test_select_unforwarded_skips_forwarded():
    assert proxy_core.select_unforwarded(["a", "b", "c"], {"a", "b"}) == "c"


def test_select_unforwarded_none_left():
    assert proxy_core.select_unforwarded(["a"], {"a"}) is None
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_proxy_core.py -q -k "bodies or unforwarded"`

- [ ] **Step 3: Implement in `plugin/proxy_core.py`**

```python
def local_find_bodies(level, uids):
    keys = LEVEL_KEYS[level]
    return [{"Level": "Instance", "Query": {k: u[k] for k in keys}, "Expand": True} for u in uids]


def count_query_bodies(level, uids):
    keys = LEVEL_KEYS[level]
    return [{"Level": "Instance", "Query": {k: u[k] for k in keys}} for u in uids]


def select_unforwarded(found_ids, forwarded):
    for oid in found_ids:
        if oid not in forwarded:
            return oid
    return None
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_proxy_core.py -q`

- [ ] **Step 5: Commit**

```bash
git add plugin/proxy_core.py tests/test_proxy_core.py
git commit -m "feat: find/count query bodies and arrival selection"
```

---

### Task 5: proxy_core — TTL expiry helpers (for eviction)

**Files:**
- Modify: `plugin/proxy_core.py`
- Test: `tests/test_proxy_core.py`

**Interfaces:**
- Produces:
  - `is_expired(last_update, now, ttl_seconds) -> bool` — `last_update` is an Orthanc datetime string `"YYYYMMDDTHHMMSS"`; `now` is a `datetime.datetime`.
  - `expired_studies(studies, now, ttl_seconds) -> list[str]` — `studies` is the `GET /studies?expand` array (each item has top-level `"ID"` and `"LastUpdate"`); returns the IDs whose `LastUpdate` is older than the TTL.

- [ ] **Step 1: Write the failing tests**

```python
import datetime
import proxy_core


def test_is_expired_true_and_false():
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    assert proxy_core.is_expired("20260624T113000", now, 1200) is True   # 30 min old > 20 min
    assert proxy_core.is_expired("20260624T115500", now, 1200) is False  # 5 min old


def test_expired_studies_filters_by_last_update():
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    studies = [
        {"ID": "old", "LastUpdate": "20260624T112000"},
        {"ID": "fresh", "LastUpdate": "20260624T115900"},
    ]
    assert proxy_core.expired_studies(studies, now, 1200) == ["old"]
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_proxy_core.py -q -k "expired"`

- [ ] **Step 3: Implement in `plugin/proxy_core.py`** (add `import datetime` at top of module)

```python
import datetime


def is_expired(last_update, now, ttl_seconds):
    ts = datetime.datetime.strptime(last_update, "%Y%m%dT%H%M%S")
    return (now - ts).total_seconds() > ttl_seconds


def expired_studies(studies, now, ttl_seconds):
    return [s["ID"] for s in studies if is_expired(s["LastUpdate"], now, ttl_seconds)]
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_proxy_core.py -q`

- [ ] **Step 5: Commit**

```bash
git add plugin/proxy_core.py tests/test_proxy_core.py
git commit -m "feat: TTL expiry helpers for eviction"
```

---

### Task 6: Fake `orthanc` test double

**Files:**
- Create: `tests/fakes.py`
- Test: `tests/test_fakes.py`

**Interfaces:**
- Produces: `FakeOrthanc` — a stand-in for the `orthanc` module used to unit-test the glue.
  - `FakeOrthanc(routes)` where `routes` maps `("GET"|"POST"|"DELETE", uri)` to a value (returned JSON-encoded) or a callable `(uri, body) -> value`.
  - `RestApiGet(uri) -> str`, `RestApiPost(uri, body) -> str`, `RestApiDelete(uri) -> None`.
  - `CreateDicom(json_str, parent, flags) -> bytes`; `CreateDicomFlags.NONE`; `ErrorCode.SUCCESS = 0`.
  - `RegisterFindCallback(cb)` / `RegisterMoveCallback2(c, g, a, f)` record into `.find_cb` / `.move_cbs`.
  - `LogError/LogWarning/LogInfo` no-ops. `.calls` records every REST call as `(method, uri, body)`.
  - `FakeAnswers` (`.added` list, `FindAddAnswer(buf)`), `FakeQuery(tags)` (`GetFindQuerySize/TagName/Value`).

- [ ] **Step 1: Write the failing test**

```python
import json
from fakes import FakeOrthanc, FakeQuery, FakeAnswers


def test_fake_routes_and_records():
    o = FakeOrthanc({("POST", "/x"): {"ID": "q1"}, ("GET", "/y"): [0, 1]})
    assert json.loads(o.RestApiPost("/x", json.dumps({"a": 1}))) == {"ID": "q1"}
    assert json.loads(o.RestApiGet("/y")) == [0, 1]
    o.RestApiDelete("/z")
    assert ("POST", "/x", '{"a": 1}') in o.calls
    assert ("DELETE", "/z", None) in o.calls


def test_fake_query_iteration():
    q = FakeQuery([("QueryRetrieveLevel", "STUDY"), ("PatientID", "7")])
    assert q.GetFindQuerySize() == 2
    assert q.GetFindQueryTagName(1) == "PatientID"
    assert q.GetFindQueryValue(1) == "7"


def test_fake_answers_collect():
    a = FakeAnswers()
    a.FindAddAnswer(b"buf")
    assert a.added == [b"buf"]
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_fakes.py -q`

- [ ] **Step 3: Implement `tests/fakes.py`**

```python
import json


class CreateDicomFlags:
    NONE = 0


class ErrorCode:
    SUCCESS = 0


class FakeAnswers:
    def __init__(self):
        self.added = []

    def FindAddAnswer(self, buf):
        self.added.append(buf)


class FakeQuery:
    def __init__(self, tags):
        self._tags = list(tags)

    def GetFindQuerySize(self):
        return len(self._tags)

    def GetFindQueryTagName(self, i):
        return self._tags[i][0]

    def GetFindQueryValue(self, i):
        return self._tags[i][1]


class FakeOrthanc:
    CreateDicomFlags = CreateDicomFlags
    ErrorCode = ErrorCode

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self.find_cb = None
        self.move_cbs = None

    def _resolve(self, method, uri, body):
        self.calls.append((method, uri, body))
        if (method, uri) not in self.routes:
            raise KeyError("no fake route for %s %s" % (method, uri))
        val = self.routes[(method, uri)]
        if callable(val):
            val = val(uri, body)
        return val

    def RestApiGet(self, uri):
        return json.dumps(self._resolve("GET", uri, None))

    def RestApiPost(self, uri, body):
        return json.dumps(self._resolve("POST", uri, body))

    def RestApiDelete(self, uri):
        self.calls.append(("DELETE", uri, None))

    def CreateDicom(self, json_str, parent, flags):
        return json_str.encode("utf-8")

    def RegisterFindCallback(self, cb):
        self.find_cb = cb

    def RegisterMoveCallback2(self, create, get_size, apply, free):
        self.move_cbs = (create, get_size, apply, free)

    def LogError(self, *a):
        pass

    def LogWarning(self, *a):
        pass

    def LogInfo(self, *a):
        pass
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_fakes.py -q`

- [ ] **Step 5: Commit**

```bash
git add tests/fakes.py tests/test_fakes.py
git commit -m "test: fake orthanc module for glue unit tests"
```

---

### Task 7: clarinet_proxy — OnFind glue

**Files:**
- Create: `plugin/clarinet_proxy.py`
- Test: `tests/test_onfind.py`

**Interfaces:**
- Consumes: `proxy_core.build_find_request`, `proxy_core.pin_charset`; `FakeOrthanc`/`FakeQuery`/`FakeAnswers`.
- Produces: importable module `clarinet_proxy` with `OnFind(answers, query, issuerAet, calledAet)`; module-level `_get(uri)` / `_post(uri, body)` JSON helpers. Importing it registers callbacks on whatever `orthanc` is in `sys.modules`.

- [ ] **Step 1: Write the failing test**

```python
import sys
import json
import importlib
import pytest
from fakes import FakeOrthanc, FakeQuery, FakeAnswers


def load_proxy(routes):
    fake = FakeOrthanc(routes)
    sys.modules["orthanc"] = fake
    sys.modules.pop("clarinet_proxy", None)
    cp = importlib.import_module("clarinet_proxy")
    return cp, fake


def test_onfind_forwards_query_and_pins_charset():
    routes = {
        ("POST", "/modalities/pacs/query"): {"ID": "q1"},
        ("GET", "/queries/q1/answers"): [0],
        ("GET", "/queries/q1/answers/0/content?simplify"): {"PatientName": "Иванов"},
    }
    cp, fake = load_proxy(routes)
    answers = FakeAnswers()
    query = FakeQuery([("QueryRetrieveLevel", "STUDY"), ("PatientName", "")])
    cp.OnFind(answers, query, "WORKER", "CLARINETPROXY")

    # forwarded the right query upstream
    posted = [b for (m, u, b) in fake.calls if u == "/modalities/pacs/query"][0]
    assert json.loads(posted) == {"Level": "STUDY", "Query": {"PatientName": ""}}
    # answer carries pinned charset + Cyrillic intact
    assert json.loads(answers.added[0].decode("utf-8")) == {
        "PatientName": "Иванов", "SpecificCharacterSet": "ISO_IR 192"}
    # query handle released
    assert ("DELETE", "/queries/q1", None) in fake.calls


def test_onfind_releases_query_handle_on_error():
    def boom(uri, body):
        raise RuntimeError("answers fetch failed")
    routes = {
        ("POST", "/modalities/pacs/query"): {"ID": "q1"},
        ("GET", "/queries/q1/answers"): boom,
    }
    cp, fake = load_proxy(routes)
    with pytest.raises(RuntimeError):
        cp.OnFind(FakeAnswers(), FakeQuery([("PatientName", "")]), "W", "CLARINETPROXY")
    assert ("DELETE", "/queries/q1", None) in fake.calls
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: clarinet_proxy`)

Run: `pytest tests/test_onfind.py -q`

- [ ] **Step 3: Implement `plugin/clarinet_proxy.py`**

```python
"""Orthanc entry point: replaces the C-FIND/C-MOVE SCP with a pass-through to the hospital PACS."""

import os
import sys
import json
import time

import orthanc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proxy_core as core

SELF_AET = core.SELF_AET
UPSTREAM = core.UPSTREAM
ARRIVAL_TIMEOUT = 600.0   # seconds to wait for the upstream pull to deliver all instances
POLL_INTERVAL = 1.0


def _get(uri):
    return json.loads(orthanc.RestApiGet(uri))


def _post(uri, body):
    return json.loads(orthanc.RestApiPost(uri, json.dumps(body)))


def OnFind(answers, query, issuerAet, calledAet):
    tags = [(query.GetFindQueryTagName(i), query.GetFindQueryValue(i))
            for i in range(query.GetFindQuerySize())]
    level, q = core.build_find_request(tags)
    qid = _post("/modalities/%s/query" % UPSTREAM, {"Level": level, "Query": q})["ID"]
    try:
        for i in _get("/queries/%s/answers" % qid):
            content = _get("/queries/%s/answers/%s/content?simplify" % (qid, i))
            answer = core.pin_charset(content)
            answers.FindAddAnswer(orthanc.CreateDicom(
                json.dumps(answer), None, orthanc.CreateDicomFlags.NONE))
    finally:
        orthanc.RestApiDelete("/queries/%s" % qid)


orthanc.RegisterFindCallback(OnFind)
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_onfind.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add plugin/clarinet_proxy.py tests/test_onfind.py
git commit -m "feat: OnFind glue forwarding C-FIND to the PACS"
```

---

### Task 8: clarinet_proxy — MoveDriver glue + registration

**Files:**
- Modify: `plugin/clarinet_proxy.py`
- Test: `tests/test_movedriver.py`

**Interfaces:**
- Consumes: `proxy_core.parse_move_request`, `resolve_destination`, `count_query_bodies`, `local_find_bodies`, `select_unforwarded`; `FakeOrthanc`.
- Produces: `MoveDriver(request)` with `get_size()`, `apply()`, `free()`; the `RegisterMoveCallback2(...)` registration. `MoveDriver` attributes: `.level`, `.uids`, `.mode`, `.worker`, `.expected`, `.move_job`, `.forwarded`.

- [ ] **Step 1: Write the failing tests**

```python
import sys
import importlib
import pytest
from fakes import FakeOrthanc

MODS = {"pacs": {"AET": "PACS"}, "worker": {"AET": "WORKER"}}


def load_proxy(routes):
    fake = FakeOrthanc(routes)
    sys.modules["orthanc"] = fake
    sys.modules.pop("clarinet_proxy", None)
    return importlib.import_module("clarinet_proxy"), fake


def base_routes():
    return {
        ("GET", "/modalities?expand"): MODS,
        ("POST", "/modalities/pacs/query"): {"ID": "cq"},
        ("GET", "/queries/cq/answers"): [0, 1],          # 2 instances expected
        ("POST", "/modalities/pacs/move"): {"ID": "mj"},
        ("GET", "/jobs/mj"): {"State": "Running"},
    }


def test_get_size_counts_and_starts_async_move():
    cp, fake = load_proxy(base_routes())
    d = cp.MoveDriver({"Level": "STUDY", "StudyInstanceUID": "1.2", "TargetAET": "WORKER"})
    assert d.mode == "forward" and d.worker == "worker"
    assert d.get_size() == 2
    assert d.move_job == "mj"
    posted = [b for (m, u, b) in fake.calls if u == "/modalities/pacs/move"][0]
    import json
    assert json.loads(posted)["Synchronous"] is False
    assert json.loads(posted)["TargetAet"] == "CLARINETPROXY"


def test_apply_forwards_one_instance_per_call():
    routes = base_routes()
    routes[("POST", "/tools/find")] = [{"ID": "i1"}, {"ID": "i2"}]
    routes[("POST", "/modalities/worker/store")] = {}
    cp, fake = load_proxy(routes)
    d = cp.MoveDriver({"Level": "STUDY", "StudyInstanceUID": "1.2", "TargetAET": "WORKER"})
    d.get_size()
    assert d.apply() == 0
    assert d.apply() == 0
    stores = [b for (m, u, b) in fake.calls if u == "/modalities/worker/store"]
    import json
    forwarded = sorted(json.loads(b)["Resources"][0] for b in stores)
    assert forwarded == ["i1", "i2"]


def test_apply_cache_mode_does_not_forward():
    routes = base_routes()
    routes[("POST", "/tools/find")] = [{"ID": "i1"}]
    cp, fake = load_proxy(routes)
    d = cp.MoveDriver({"Level": "STUDY", "StudyInstanceUID": "1.2", "TargetAET": "CLARINETPROXY"})
    d.get_size()
    d.apply()
    assert not any(u.endswith("/store") for (m, u, b) in fake.calls)


def test_apply_raises_on_job_failure_when_no_arrival():
    routes = base_routes()
    routes[("POST", "/tools/find")] = []                 # nothing arrived
    routes[("GET", "/jobs/mj")] = {"State": "Failure"}
    cp, fake = load_proxy(routes)
    d = cp.MoveDriver({"Level": "STUDY", "StudyInstanceUID": "1.2", "TargetAET": "WORKER"})
    d.get_size()
    with pytest.raises(Exception):
        d.apply()


def test_init_rejects_unknown_target():
    cp, fake = load_proxy(base_routes())
    with pytest.raises(ValueError):
        cp.MoveDriver({"Level": "STUDY", "StudyInstanceUID": "1.2", "TargetAET": "GHOST"})
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: module 'clarinet_proxy' has no attribute 'MoveDriver'`)

Run: `pytest tests/test_movedriver.py -q`

- [ ] **Step 3: Implement in `plugin/clarinet_proxy.py`** (append before any registration line; keep `RegisterFindCallback` where it is, add the move registration at the very end)

```python
class MoveDriver:
    def __init__(self, request):
        self.level, self.uids = core.parse_move_request(request)
        modalities = _get("/modalities?expand")
        self.mode, self.worker = core.resolve_destination(
            request.get("TargetAET"), SELF_AET, modalities, UPSTREAM)
        self.forwarded = set()
        self.move_job = None
        self.expected = 0

    def _count(self):
        total = 0
        for body in core.count_query_bodies(self.level, self.uids):
            qid = _post("/modalities/%s/query" % UPSTREAM, body)["ID"]
            try:
                total += len(_get("/queries/%s/answers" % qid))
            finally:
                orthanc.RestApiDelete("/queries/%s" % qid)
        return total

    def get_size(self):
        self.expected = self._count()
        self.move_job = _post("/modalities/%s/move" % UPSTREAM, {
            "Level": self.level, "Resources": self.uids,
            "TargetAet": SELF_AET, "Synchronous": False})["ID"]
        return self.expected

    def _local_ids(self):
        ids = []
        for body in core.local_find_bodies(self.level, self.uids):
            ids.extend(r["ID"] for r in _post("/tools/find", body))
        return ids

    def _job_failed(self):
        return _get("/jobs/%s" % self.move_job)["State"] == "Failure"

    def _next_arrival(self):
        deadline = time.time() + ARRIVAL_TIMEOUT
        while True:
            oid = core.select_unforwarded(self._local_ids(), self.forwarded)
            if oid is not None:
                return oid
            if self._job_failed():
                raise Exception("upstream C-MOVE job %s failed" % self.move_job)
            if time.time() > deadline:
                raise Exception("timed out waiting for instance arrival")
            time.sleep(POLL_INTERVAL)

    def apply(self):
        oid = self._next_arrival()
        if self.mode == "forward":
            _post("/modalities/%s/store" % self.worker,
                  {"Resources": [oid], "Synchronous": True})
        self.forwarded.add(oid)
        return orthanc.ErrorCode.SUCCESS

    def free(self):
        pass


orthanc.RegisterMoveCallback2(
    lambda **r: MoveDriver(r),
    lambda d: d.get_size(),
    lambda d: d.apply(),
    lambda d: d.free())
```

Note: `test_apply_raises_on_job_failure_when_no_arrival` sets `ARRIVAL_TIMEOUT` indirectly — to keep it fast, the test relies on the job being `Failure` so `_next_arrival` raises on the first poll before any `sleep`. Verify the failure branch is checked **before** `time.sleep`.

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_movedriver.py -q`
Expected: `5 passed`

- [ ] **Step 5: Run the whole unit suite**

Run: `pytest -q`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add plugin/clarinet_proxy.py tests/test_movedriver.py
git commit -m "feat: MoveDriver glue with async pull and per-instance forwarding"
```

---

### Task 9: Orthanc production config (`etc/*.json`)

**Files:**
- Create: `etc/10-core.json`, `etc/20-security.json`, `etc/30-modalities.json`, `etc/40-dicomweb.json`, `etc/50-python.json`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: the production config directory loaded by `Orthanc /etc/orthanc-proxy/`. `30-modalities.json` has a `pacs` entry and one `worker_X` template entry.

- [ ] **Step 1: Write the failing test** (validates JSON + key invariants from Global Constraints)

```python
import json
import glob
import os

ETC = os.path.join(os.path.dirname(__file__), "..", "etc")


def load(name):
    with open(os.path.join(ETC, name)) as f:
        return json.load(f)


def test_all_config_files_are_valid_json():
    files = glob.glob(os.path.join(ETC, "*.json"))
    assert len(files) == 5
    for f in files:
        with open(f) as fh:
            json.load(fh)


def test_core_security_invariants():
    core = load("10-core.json")
    assert core["DicomAet"] == "CLARINETPROXY"
    assert core["HttpBindAddresses"] == ["127.0.0.1"]
    assert core["RemoteAccessAllowed"] is False
    assert core["MaximumStorageSize"] == 14336
    assert core["MaximumStorageMode"] == "Recycle"
    assert core["MaximumStorageCacheSize"] == 512
    assert core["StableAge"] == 20

    sec = load("20-security.json")
    assert sec["DicomCheckCalledAet"] is True
    assert sec["DicomCheckModalityHost"] is True
    for k in ("DicomAlwaysAllowEcho", "DicomAlwaysAllowStore",
              "DicomAlwaysAllowFind", "DicomAlwaysAllowMove", "DicomAlwaysAllowGet"):
        assert sec[k] is False


def test_modalities_and_dicomweb():
    mods = load("30-modalities.json")["DicomModalities"]
    assert mods["pacs"]["AllowStore"] is True            # accept C-STORE-back on move-to-self
    worker = mods["worker_X"]
    assert worker["AllowFind"] and worker["AllowMove"] and worker["AllowGet"]
    assert worker["AllowStore"] is False

    dw = load("40-dicomweb.json")["DicomWeb"]
    assert dw["Enable"] is True
    assert dw["Root"] == "/dicom-web/"
    assert dw["PublicRoot"] == "/pacs-web/"
    assert "Host" not in dw                               # deprecated; must not be set
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_config.py -q`

- [ ] **Step 3: Create the five config files**

`etc/10-core.json`:
```json
{
  "Name": "clarinet-pacs-proxy",
  "DicomAet": "CLARINETPROXY",
  "DicomPort": 4242,
  "DicomServerEnabled": true,
  "HttpServerEnabled": true,
  "HttpPort": 8042,
  "HttpBindAddresses": ["127.0.0.1"],
  "RemoteAccessAllowed": false,
  "StorageDirectory": "/var/lib/orthanc-proxy/storage",
  "IndexDirectory": "/var/lib/orthanc-proxy/db",
  "StorageCompression": false,
  "MaximumStorageSize": 14336,
  "MaximumStorageMode": "Recycle",
  "MaximumStorageCacheSize": 512,
  "StableAge": 20
}
```

`etc/20-security.json`:
```json
{
  "DicomCheckCalledAet": true,
  "DicomCheckModalityHost": true,
  "DicomAlwaysAllowEcho": false,
  "DicomAlwaysAllowStore": false,
  "DicomAlwaysAllowFind": false,
  "DicomAlwaysAllowMove": false,
  "DicomAlwaysAllowGet": false
}
```

`etc/30-modalities.json` (replace host/port/AET with real values at deploy; `worker_X` is a template — duplicate per worker):
```json
{
  "DicomModalities": {
    "pacs": {
      "AET": "HOSPITALPACS",
      "Host": "10.0.0.10",
      "Port": 104,
      "AllowEcho": true,
      "AllowStore": true,
      "AllowFind": false,
      "AllowMove": false,
      "AllowGet": false
    },
    "worker_X": {
      "AET": "WORKER_X",
      "Host": "10.0.0.21",
      "Port": 4242,
      "AllowEcho": true,
      "AllowFind": true,
      "AllowMove": true,
      "AllowGet": true,
      "AllowStore": false
    }
  }
}
```

`etc/40-dicomweb.json`:
```json
{
  "DicomWeb": {
    "Enable": true,
    "Root": "/dicom-web/",
    "PublicRoot": "/pacs-web/",
    "EnableWado": true,
    "WadoRoot": "/wado",
    "Ssl": false
  }
}
```

`etc/50-python.json` (the `Plugins` dir holds `libOrthancPython.so` + `libOrthancDicomWeb.so`; the plugin scripts are deployed next to them):
```json
{
  "Plugins": ["/opt/orthanc/plugins"],
  "PythonScript": "/opt/orthanc/plugins/clarinet_proxy.py",
  "PythonVerbose": false
}
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_config.py -q`

- [ ] **Step 5: Commit**

```bash
git add etc/ tests/test_config.py
git commit -m "feat: production Orthanc config (core, security, modalities, dicomweb, python)"
```

---

### Task 10: Staging harness (docker-compose) + DICOM fixture

**Files:**
- Create: `staging/docker-compose.yml`
- Create: `staging/Dockerfile`
- Create: `staging/config/pacs.json`, `staging/config/proxy.json`, `staging/config/worker.json`
- Create: `staging/requirements.txt`
- Create: `staging/conftest.py`
- Create: `staging/fixtures.py`
- Create: `staging/test_harness.py`

**Interfaces:**
- Produces: a 3-node DICOM network (`pacs`, `proxy`, `worker`) on a docker network, the proxy running the real `plugin/` code; `fixtures.upload_cyrillic_study(base_url)` seeds the PACS; pytest helpers `pacs_url`/`proxy_url`/`worker_url` and `wait_ready()`.
- Consumes: `plugin/clarinet_proxy.py`, `plugin/proxy_core.py`.

Staging differs from prod **intentionally**: HTTP is open (no localhost bind, no auth) so the test driver can introspect via REST. DICOM AET/allowlist semantics mirror prod.

- [ ] **Step 1: `staging/Dockerfile`** (proxy image = official Orthanc image + our plugin)

```dockerfile
FROM orthancteam/orthanc:24.10.3
COPY plugin/clarinet_proxy.py plugin/proxy_core.py /opt/clarinet/
```

- [ ] **Step 2: `staging/config/pacs.json`** (test PACS — holds studies, relaxed for staging)

```json
{
  "Name": "staging-pacs",
  "DicomAet": "HOSPITALPACS",
  "DicomPort": 4242,
  "HttpPort": 8042,
  "RemoteAccessAllowed": true,
  "AuthenticationEnabled": false,
  "DicomAlwaysAllowEcho": true,
  "DicomAlwaysAllowFind": true,
  "DicomAlwaysAllowMove": true,
  "DicomAlwaysAllowStore": true,
  "DicomModalities": { "proxy": ["CLARINETPROXY", "proxy", 4242] }
}
```

- [ ] **Step 3: `staging/config/worker.json`** (downstream SCU + C-STORE destination)

```json
{
  "Name": "staging-worker",
  "DicomAet": "WORKER",
  "DicomPort": 4242,
  "HttpPort": 8042,
  "RemoteAccessAllowed": true,
  "AuthenticationEnabled": false,
  "DicomAlwaysAllowStore": true,
  "DicomModalities": { "proxy": ["CLARINETPROXY", "proxy", 4242] }
}
```

- [ ] **Step 4: `staging/config/proxy.json`** (our plugin; prod-like DICOM allowlist, open HTTP)

```json
{
  "Name": "staging-proxy",
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
    "pacs":   { "AET": "HOSPITALPACS", "Host": "pacs",   "Port": 4242, "AllowEcho": true, "AllowStore": true },
    "worker": { "AET": "WORKER", "Host": "worker", "Port": 4242, "AllowEcho": true, "AllowFind": true, "AllowMove": true, "AllowGet": true, "AllowStore": false }
  },
  "DicomWeb": { "Enable": true, "Root": "/dicom-web/", "PublicRoot": "/dicom-web/", "EnableWado": true },
  "Plugins": ["/usr/share/orthanc/plugins"],
  "PythonScript": "/opt/clarinet/clarinet_proxy.py",
  "PythonVerbose": true
}
```

- [ ] **Step 5: `staging/docker-compose.yml`**

```yaml
services:
  pacs:
    image: orthancteam/orthanc:24.10.3
    volumes:
      - ./config/pacs.json:/etc/orthanc/orthanc.json:ro
    ports: ["8101:8042"]
  worker:
    image: orthancteam/orthanc:24.10.3
    volumes:
      - ./config/worker.json:/etc/orthanc/orthanc.json:ro
    ports: ["8103:8042"]
  proxy:
    build:
      context: ..
      dockerfile: staging/Dockerfile
    volumes:
      - ./config/proxy.json:/etc/orthanc/orthanc.json:ro
    ports: ["8102:8042", "4242:4242"]
    depends_on: [pacs, worker]
```

- [ ] **Step 6: `staging/requirements.txt`**

```
pytest
requests
pydicom
```

- [ ] **Step 7: `staging/fixtures.py`** (build + upload a minimal Cyrillic study to a node)

```python
import io
import requests
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid, CTImageStorage


def build_cyrillic_instance(study_uid, series_uid, sop_uid):
    ds = Dataset()
    ds.PatientName = "Иванов^Иван"
    ds.PatientID = "PROXY-TEST-1"
    ds.StudyInstanceUID = study_uid
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = sop_uid
    ds.SOPClassUID = CTImageStorage
    ds.Modality = "CT"
    ds.SpecificCharacterSet = "ISO_IR 192"
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop_uid
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta = meta
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    buf = io.BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue()


def upload_cyrillic_study(base_url):
    """Upload one CT instance to an Orthanc node. Returns the StudyInstanceUID."""
    study_uid, series_uid, sop_uid = generate_uid(), generate_uid(), generate_uid()
    dicom = build_cyrillic_instance(study_uid, series_uid, sop_uid)
    r = requests.post(base_url + "/instances", data=dicom, timeout=30)
    r.raise_for_status()
    return study_uid
```

- [ ] **Step 8: `staging/conftest.py`** (URLs + readiness)

```python
import time
import requests
import pytest

PACS = "http://localhost:8101"
PROXY = "http://localhost:8102"
WORKER = "http://localhost:8103"


def _ready(url):
    try:
        return requests.get(url + "/system", timeout=2).ok
    except requests.RequestException:
        return False


def wait_ready(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(_ready(u) for u in (PACS, PROXY, WORKER)):
            return
        time.sleep(2)
    raise RuntimeError("staging nodes not ready")


@pytest.fixture(scope="session", autouse=True)
def _harness():
    wait_ready()


@pytest.fixture
def pacs_url():
    return PACS


@pytest.fixture
def proxy_url():
    return PROXY


@pytest.fixture
def worker_url():
    return WORKER
```

- [ ] **Step 9: `staging/test_harness.py`** (proves the harness boots and the plugin loaded)

```python
import requests


def test_nodes_up_and_plugin_loaded(proxy_url):
    plugins = requests.get(proxy_url + "/plugins", timeout=5).json()
    assert "python" in plugins
    assert "dicom-web" in plugins


def test_pacs_seed(pacs_url):
    from fixtures import upload_cyrillic_study
    study_uid = upload_cyrillic_study(pacs_url)
    found = requests.post(pacs_url + "/tools/find",
                          json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid}}).json()
    assert len(found) == 1
```

- [ ] **Step 10: Bring it up and run — expect PASS**

Run:
```bash
cd staging && docker compose up -d --build
pip install -r requirements.txt
pytest -q test_harness.py
```
Expected: `2 passed`. (Leave the stack up for Tasks 11-13.)

- [ ] **Step 11: Commit**

```bash
git add staging/
git commit -m "test: docker staging harness (pacs/proxy/worker) with Cyrillic fixture"
```

---

### Task 11: Staging — C-FIND pass-through (incl. Cyrillic round-trip)

**Files:**
- Create: `staging/test_cfind.py`

**Interfaces:**
- Consumes: `proxy_url`, `pacs_url`, `worker_url`, `fixtures.upload_cyrillic_study`. The worker node issues C-FIND to the proxy via `POST /modalities/proxy/query`.

- [ ] **Step 1: Write the test**

```python
import requests
from fixtures import upload_cyrillic_study


def test_cfind_forwarded_and_charset_preserved(pacs_url, proxy_url, worker_url):
    study_uid = upload_cyrillic_study(pacs_url)

    # worker issues C-FIND to the proxy; proxy forwards to the PACS
    q = requests.post(worker_url + "/modalities/proxy/query",
                      json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid, "PatientName": ""}})
    q.raise_for_status()
    query_id = q.json()["ID"]

    answers = requests.get(worker_url + "/queries/%s/answers" % query_id).json()
    assert len(answers) == 1

    content = requests.get(
        worker_url + "/queries/%s/answers/0/content?simplify" % query_id).json()
    assert content["StudyInstanceUID"] == study_uid
    assert content["PatientName"] == "Иванов^Иван"   # Cyrillic survived the proxy
```

- [ ] **Step 2: Run — expect PASS**

Run: `cd staging && pytest -q test_cfind.py`
Expected: `1 passed`. If `PatientName` is mojibake, the charset pin (Task 2/7) is wrong — fix there.

- [ ] **Step 3: Commit**

```bash
git add staging/test_cfind.py
git commit -m "test: staging C-FIND pass-through with Cyrillic round-trip"
```

---

### Task 12: Staging — C-MOVE dest=worker (store-and-forward)

**Files:**
- Create: `staging/test_cmove_worker.py`

**Interfaces:**
- Consumes: the same fixtures. The worker issues a C-MOVE to the proxy with `TargetAet=WORKER`; the proxy pulls from the PACS and C-STOREs to the worker.

- [ ] **Step 1: Write the test**

```python
import requests
from fixtures import upload_cyrillic_study


def test_cmove_to_worker_forwards_study(pacs_url, proxy_url, worker_url):
    study_uid = upload_cyrillic_study(pacs_url)

    # worker requests a C-MOVE through the proxy, destination = worker itself
    r = requests.post(worker_url + "/modalities/proxy/move",
                      json={"Level": "Study",
                            "Resources": [{"StudyInstanceUID": study_uid}],
                            "TargetAet": "WORKER",
                            "Synchronous": True},
                      timeout=120)
    r.raise_for_status()

    # the study landed on the worker
    on_worker = requests.post(worker_url + "/tools/find",
                              json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid}}).json()
    assert len(on_worker) == 1

    # and the transit copy is cached on the proxy (TTL model, no immediate delete)
    on_proxy = requests.post(proxy_url + "/tools/find",
                             json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid}}).json()
    assert len(on_proxy) == 1
```

- [ ] **Step 2: Run — expect PASS**

Run: `cd staging && pytest -q test_cmove_worker.py`
Expected: `1 passed`

- [ ] **Step 3: Commit**

```bash
git add staging/test_cmove_worker.py
git commit -m "test: staging C-MOVE store-and-forward to worker"
```

---

### Task 13: Staging — C-MOVE dest=self + DICOMweb + C-GET

**Files:**
- Create: `staging/test_cache_paths.py`

**Interfaces:**
- Consumes: the same fixtures. A `dest=CLARINETPROXY` move pre-loads the cache; then DICOMweb (QIDO) and a C-GET retrieve read it.

- [ ] **Step 1: Write the test**

```python
import requests
from fixtures import upload_cyrillic_study


def test_cmove_self_caches_then_dicomweb_and_cget(pacs_url, proxy_url, worker_url):
    study_uid = upload_cyrillic_study(pacs_url)

    # pre-load the proxy cache via dest=self C-MOVE (no forward)
    requests.post(worker_url + "/modalities/proxy/move",
                  json={"Level": "Study",
                        "Resources": [{"StudyInstanceUID": study_uid}],
                        "TargetAet": "CLARINETPROXY",
                        "Synchronous": True},
                  timeout=120).raise_for_status()

    # DICOMweb (QIDO) over the cache returns the study
    qido = requests.get(proxy_url + "/dicom-web/studies",
                        params={"StudyInstanceUID": study_uid},
                        headers={"Accept": "application/dicom+json"}).json()
    assert any(s["0020000D"]["Value"][0] == study_uid for s in qido)

    # C-GET from the worker pulls it out of the proxy cache (built-in SCP)
    cg = requests.post(worker_url + "/modalities/proxy/query",
                       json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid}})
    cg.raise_for_status()
    qid = cg.json()["ID"]
    requests.post(worker_url + "/queries/%s/retrieve" % qid,
                  json={"TargetAet": "WORKER", "RetrieveMethod": "C-GET", "Synchronous": True},
                  timeout=120).raise_for_status()
    on_worker = requests.post(worker_url + "/tools/find",
                              json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid}}).json()
    assert len(on_worker) == 1
```

- [ ] **Step 2: Run — expect PASS**

Run: `cd staging && pytest -q test_cache_paths.py`
Expected: `1 passed`. If C-GET fails, record it: the spec flags Orthanc C-GET-SCP availability as a staging risk — capture the Orthanc version/error in `staging/README` and fall back to documenting C-GET as cache-only-best-effort.

- [ ] **Step 3: Tear down and commit**

```bash
git add staging/test_cache_paths.py
git commit -m "test: staging C-MOVE-to-self cache, DICOMweb and C-GET paths"
cd staging && docker compose down -v
```

---

### Task 14: Eviction script + systemd timer

**Files:**
- Create: `deploy/evict.py`
- Create: `deploy/orthanc-proxy-evict.service`
- Create: `deploy/orthanc-proxy-evict.timer`
- Test: `tests/test_evict.py`

**Interfaces:**
- Consumes: `proxy_core.expired_studies`.
- Produces: `evict.py` with `select_and_delete(base_url, now, ttl_seconds, max_storage_mb, http=requests) -> deleted_ids`; a `.service`/`.timer` pair (`OnUnitActiveSec=5min`).

- [ ] **Step 1: Write the failing test** (inject a fake http client; no network)

```python
import datetime
import importlib

evict = importlib.import_module("evict")


class FakeHTTP:
    def __init__(self, studies, stats):
        self._studies = studies
        self._stats = stats
        self.deleted = []

    def get(self, url, timeout=10):
        class R:
            def __init__(self, payload):
                self._p = payload
            def raise_for_status(self):
                pass
            def json(self):
                return self._p
        if url.endswith("/studies?expand"):
            return R(self._studies)
        if url.endswith("/statistics"):
            return R(self._stats)
        raise AssertionError(url)

    def delete(self, url, timeout=10):
        self.deleted.append(url.rsplit("/", 1)[1])
        class R:
            def raise_for_status(self):
                pass
        return R()


def test_select_and_delete_removes_only_expired():
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    http = FakeHTTP(
        studies=[{"ID": "old", "LastUpdate": "20260624T112000"},
                 {"ID": "fresh", "LastUpdate": "20260624T115900"}],
        stats={"TotalDiskSizeMB": 100})
    deleted = evict.select_and_delete("http://x", now, 1200, 14336, http=http)
    assert deleted == ["old"]
    assert http.deleted == ["old"]
```

- [ ] **Step 2: Run — expect FAIL**

Run: `pytest tests/test_evict.py -q`

- [ ] **Step 3: Implement `deploy/evict.py`**

```python
#!/usr/bin/env python3
"""TTL eviction + storage-fill logging for the clarinet-pacs-proxy cache.

Run by orthanc-proxy-evict.timer every 5 minutes. Deletes studies whose LastUpdate
is older than TTL_SECONDS, and logs a WARN when storage fill >= WARN_FILL of the max."""

import os
import sys
import logging
import datetime

import requests

sys.path.insert(0, os.environ.get("PROXY_CORE_DIR", "/opt/orthanc/plugins"))
import proxy_core as core

BASE_URL = os.environ.get("ORTHANC_URL", "http://127.0.0.1:8042")
TTL_SECONDS = int(os.environ.get("TTL_SECONDS", "1200"))
MAX_STORAGE_MB = int(os.environ.get("MAX_STORAGE_MB", "14336"))
WARN_FILL = float(os.environ.get("WARN_FILL", "0.8"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("evict")


def select_and_delete(base_url, now, ttl_seconds, max_storage_mb, http=requests):
    r = http.get(base_url + "/studies?expand", timeout=10)
    r.raise_for_status()
    expired = core.expired_studies(r.json(), now, ttl_seconds)
    for sid in expired:
        http.delete(base_url + "/studies/" + sid, timeout=10).raise_for_status()
    log.info("evicted %d expired studies", len(expired))

    s = http.get(base_url + "/statistics", timeout=10)
    s.raise_for_status()
    used = float(s.json().get("TotalDiskSizeMB", 0))
    fill = used / max_storage_mb if max_storage_mb else 0.0
    if fill >= WARN_FILL:
        log.warning("storage fill %.0f%% (%.0f / %d MB)", fill * 100, used, max_storage_mb)
    return expired


def main():
    select_and_delete(BASE_URL, datetime.datetime.now(), TTL_SECONDS, MAX_STORAGE_MB)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run — expect PASS**

Run: `pytest tests/test_evict.py -q`

- [ ] **Step 5: Create the systemd units**

`deploy/orthanc-proxy-evict.service`:
```ini
[Unit]
Description=clarinet-pacs-proxy cache TTL eviction
After=orthanc-proxy.service

[Service]
Type=oneshot
User=orthanc
Environment=ORTHANC_URL=http://127.0.0.1:8042
Environment=PROXY_CORE_DIR=/opt/orthanc/plugins
Environment=TTL_SECONDS=1200
Environment=MAX_STORAGE_MB=14336
ExecStart=/usr/bin/python3 /opt/orthanc/deploy/evict.py
```

`deploy/orthanc-proxy-evict.timer`:
```ini
[Unit]
Description=Run clarinet-pacs-proxy eviction every 5 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 6: Validate unit syntax**

Run: `systemd-analyze verify deploy/orthanc-proxy-evict.service deploy/orthanc-proxy-evict.timer 2>&1 | grep -vE 'orthanc-proxy.service' || true`
Expected: no fatal parse errors (a warning that `orthanc-proxy.service` is missing is fine on a dev box).

- [ ] **Step 7: Commit**

```bash
git add deploy/evict.py deploy/orthanc-proxy-evict.service deploy/orthanc-proxy-evict.timer tests/test_evict.py
git commit -m "feat: TTL eviction script and systemd timer"
```

---

### Task 15: LSB install script + Orthanc systemd unit + LUKS runbook

**Files:**
- Create: `deploy/install.sh`
- Create: `deploy/orthanc-proxy.service`
- Create: `deploy/luks-setup.md`

**Interfaces:**
- Produces: `install.sh` that downloads Orthanc 1.12.11 + DicomWeb 1.23 + the Python plugin matching the host ABI into `/opt/orthanc`; a systemd unit ordered after the LUKS mount; a LUKS runbook.

- [ ] **Step 1: Create `deploy/install.sh`** (idempotent; `DRYRUN=1` prints URLs without downloading)

```bash
#!/usr/bin/env bash
set -euo pipefail

VER="1.12.11"
DICOMWEB_VER="1.23"
DEST="${DEST:-/opt/orthanc}"
BASE="https://orthanc.uclouvain.be/downloads/linux-standard-base"

py_subdir() {
  local codename pyver
  codename="$(. /etc/os-release && echo "${VERSION_CODENAME:-unknown}")"
  pyver="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
  case "$codename" in
    bookworm) echo "debian-bookworm-python-3.11" ;;
    bullseye) echo "debian-bullseye-python-3.9" ;;
    trixie)   echo "debian-trixie-python-3.13" ;;
    *) echo "ERROR: unknown distro '$codename' (python $pyver); pick the matching" \
            "orthanc-python LSB subdir manually from $BASE/orthanc-python/" >&2; return 1 ;;
  esac
}

main() {
  local sub py_url orthanc_url dicomweb_url
  sub="$(py_subdir)"
  orthanc_url="$BASE/orthanc/$VER/Orthanc"
  dicomweb_url="$BASE/orthanc-dicomweb/$DICOMWEB_VER/libOrthancDicomWeb.so"
  py_url="$BASE/orthanc-python/$sub/mainline/libOrthancPython.so"

  echo "Orthanc:  $orthanc_url"
  echo "DicomWeb: $dicomweb_url"
  echo "Python:   $py_url"
  if [ "${DRYRUN:-0}" = "1" ]; then return 0; fi

  install -d "$DEST/bin" "$DEST/plugins" "$DEST/deploy"
  curl -fsSL "$orthanc_url"  -o "$DEST/bin/Orthanc"
  curl -fsSL "$dicomweb_url" -o "$DEST/plugins/libOrthancDicomWeb.so"
  curl -fsSL "$py_url"       -o "$DEST/plugins/libOrthancPython.so"
  chmod +x "$DEST/bin/Orthanc"
  install -m 0644 plugin/clarinet_proxy.py plugin/proxy_core.py "$DEST/plugins/"
  install -m 0755 deploy/evict.py "$DEST/deploy/"
  echo "Installed to $DEST. Place etc/*.json in /etc/orthanc-proxy/ and enable the systemd units."
}

main "$@"
```

- [ ] **Step 2: Create `deploy/orthanc-proxy.service`** (modelled on the Debian unit; ordered after the LUKS mount of `/var/lib/orthanc-proxy`)

```ini
[Unit]
Description=clarinet-pacs-proxy (Orthanc DICOM/DICOMweb proxy)
Documentation=https://orthanc.uclouvain.be/book/
After=network-online.target
Wants=network-online.target
RequiresMountsFor=/var/lib/orthanc-proxy

[Service]
User=orthanc
Group=orthanc
ExecStart=/opt/orthanc/bin/Orthanc /etc/orthanc-proxy/
Restart=on-failure
TimeoutSec=600

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 3: Create `deploy/luks-setup.md`**

````markdown
# Encrypted SSD storage (LUKS) for the proxy cache

The proxy keeps transit PHI on an SSD encrypted at rest. Both `StorageDirectory`
and `IndexDirectory` live on the same encrypted volume so they stay consistent
across reboots, and Orthanc starts only after the volume is unlocked and mounted.

## One-time setup

```bash
# 1. Create the LUKS container on the SSD partition (DESTROYS data on it)
cryptsetup luksFormat /dev/sdX1

# 2. Add a keyfile so the volume unlocks unattended at boot
dd if=/dev/urandom of=/etc/orthanc-proxy.key bs=4096 count=1
chmod 0400 /etc/orthanc-proxy.key
cryptsetup luksAddKey /dev/sdX1 /etc/orthanc-proxy.key

# 3. Open, format, mount
cryptsetup open --key-file /etc/orthanc-proxy.key /dev/sdX1 orthanc-proxy
mkfs.ext4 /dev/mapper/orthanc-proxy
mkdir -p /var/lib/orthanc-proxy
mount /dev/mapper/orthanc-proxy /var/lib/orthanc-proxy
mkdir -p /var/lib/orthanc-proxy/storage /var/lib/orthanc-proxy/db
chown -R orthanc:orthanc /var/lib/orthanc-proxy
```

## Auto-unlock at boot

`/etc/crypttab`:
```
orthanc-proxy  /dev/sdX1  /etc/orthanc-proxy.key  luks
```

`/etc/fstab`:
```
/dev/mapper/orthanc-proxy  /var/lib/orthanc-proxy  ext4  defaults  0  2
```

The `RequiresMountsFor=/var/lib/orthanc-proxy` line in `orthanc-proxy.service`
makes systemd wait for the mount (which waits for the crypttab unlock) before
starting Orthanc — so the index and storage are never accessed unencrypted or
out of sync.

> Use `/dev/disk/by-uuid/...` instead of `/dev/sdX1` in production.
````

- [ ] **Step 4: Verify the script and unit**

Run:
```bash
shellcheck deploy/install.sh
DRYRUN=1 bash deploy/install.sh
systemd-analyze verify deploy/orthanc-proxy.service 2>&1 | grep -vE 'Wants|RequiresMountsFor' || true
```
Expected: shellcheck clean; dry-run prints three reachable-looking URLs; no fatal unit parse error.

- [ ] **Step 5: Commit**

```bash
git add deploy/install.sh deploy/orthanc-proxy.service deploy/luks-setup.md
git commit -m "feat: LSB install script, Orthanc systemd unit, LUKS runbook"
```

---

### Task 16: README (topology + runbook)

**Files:**
- Create: `README.md`

**Interfaces:**
- Produces: operator-facing documentation: purpose, ASCII topology, and the runbook (register `CLARINETPROXY` at the PACS, configure downstream workers, install, operate).

- [ ] **Step 1: Write `README.md`**

````markdown
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
````

- [ ] **Step 2: Verify it renders / no broken fences**

Run: `python3 -c "import pathlib,sys; t=pathlib.Path('README.md').read_text(); sys.exit(0 if t.count('\`\`\`')%2==0 else 1)"`
Expected: exit 0 (balanced code fences).

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README with topology and operator runbook"
```

---

## Self-Review

**Spec coverage** (each spec section → task):
- §2 architecture / §4.1 callbacks → Tasks 7, 8 (legacy `RegisterFindCallback` + `RegisterMoveCallback2`).
- §4.2 REST (query/answers/move/store/tools-find/jobs) → Tasks 7, 8.
- §5 OnFind (charset, try/finally) → Tasks 2, 7. MoveDriver (TargetAET-only, async get_size, per-instance apply, full-hierarchy find) → Tasks 3, 4, 8.
- §6 config (5 files, security flags, modality permissions, DicomWeb PublicRoot, no `Host`) → Task 9.
- §7 storage/eviction (TTL timer 5min/1200s, Recycle backstop) → Tasks 5, 14.
- §8 security (localhost HTTP, firewall, AlwaysAllow false) → Task 9 (config) + Task 16 (firewall runbook).
- §9 deploy (LSB+ABI install, systemd, LUKS) → Tasks 14, 15.
- §10 deliverables / §11 staging plan (C-FIND, C-MOVE worker, C-MOVE self, DICOMweb, C-GET) → Tasks 10–13.
- §12 risks → exercised on staging (charset Task 11; C-GET Task 13; move timing Tasks 12–13).
- §13 downstream contract → README Task 16.

**Placeholder scan:** `worker_X` / `WORKER_X` / `10.0.0.x` are explicit deploy-time templates (README Task 16 instructs replacement), not plan placeholders. No `TBD`/`TODO`/"handle edge cases".

**Type consistency:** `proxy_core` names (`build_find_request`, `pin_charset`, `parse_move_request`, `resolve_destination`, `find_alias_for_aet`, `local_find_bodies`, `count_query_bodies`, `select_unforwarded`, `is_expired`, `expired_studies`) are defined in Tasks 2–5 and consumed unchanged in Tasks 7, 8, 14. `MoveDriver` attributes (`level`, `uids`, `mode`, `worker`, `expected`, `move_job`, `forwarded`) defined in Task 8 and asserted by its tests. `FakeOrthanc` API (Task 6) matches its use in Tasks 7, 8.
