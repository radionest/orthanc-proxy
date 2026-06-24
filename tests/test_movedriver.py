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
