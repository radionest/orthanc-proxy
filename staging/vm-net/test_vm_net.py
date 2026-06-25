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
            pytest.fail(f"missing {need}.json in {DATA} (collected: {sorted(r)})")
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
