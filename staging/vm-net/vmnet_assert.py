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
        fails.append(
            "S1: clientA got %d of %d for %s" % (received_count(clienta, "s1", study), n, study)
        )
    if _studies_in(clientb, "s1"):
        fails.append(
            "S1: clientB received %s in s1 (expected nothing)" % _studies_in(clientb, "s1")
        )
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
        fails.append(
            "S6: TTL eviction did not reduce study count (%r -> %r)"
            % (proxy.get("studies_before_evict"), proxy.get("studies_after_evict"))
        )
    if not proxy.get("fill_warn_logged"):
        fails.append("S6: storage-fill WARN was not logged")
    return fails
