"""SCU client agent: a Storage SCP + scripted C-FIND/C-MOVE/C-STORE scenarios.

Driven entirely by env (see plan Task 4 interfaces). Records every observation
via agent_core and writes RESULT_PATH; coordinates the concurrent phases through
the 9p barrier dir. Runs on Python 3.7 inside the client golden VM."""

import json
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

ROLE = os.environ["ROLE"]  # "clienta" | "clientb"
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


def drain(phase, study, n, timeout=120):
    """Wait until all n instances of `study` for `phase` have been recorded before the
    caller switches phase, so a late sub-operation can never land in the next phase's bucket.
    Record a `drain` event (ok=False on timeout) so a silent shortfall surfaces to the host gate."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        with _lock:
            got = ac.received_count(result, phase, study)
        if got >= n:
            ac.record_event(result, "drain", phase=phase, study=study, got=got, n=n, ok=True)
            return True
        time.sleep(0.5)
    with _lock:
        got = ac.received_count(result, phase, study)
    ac.record_event(result, "drain", phase=phase, study=study, got=got, n=n, ok=False)
    return False


def _qido_studies(study_uid, retries=5):
    """Return the QIDO study list for study_uid (empty list on failure). Retries because
    DICOMweb visibility can lag a successful C-STORE/C-MOVE by a couple of seconds."""
    url = f"http://{PROXY_HOST}:{PROXY_REST}/dicom-web/studies?StudyInstanceUID={study_uid}"
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                if r.status == 200:
                    data = json.loads(r.read())
                    if isinstance(data, list) and data:
                        return data
        except Exception:
            pass
        time.sleep(1)
    return []


def wado_cached(study_uid):
    """WADO-RS retrieve of the study metadata through the proxy (the WADO half of S3)."""
    url = f"http://{PROXY_HOST}:{PROXY_REST}/dicom-web/studies/{study_uid}/metadata"
    ok = False
    for _ in range(5):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                if r.status == 200:
                    data = json.loads(r.read())
                    ok = isinstance(data, list) and len(data) > 0
        except Exception:
            ok = False
        if ok:
            break
        time.sleep(1)
    ac.record_event(result, "wado", study=study_uid, ok=ok)


def start_scp():
    ae = AE(ae_title=SELF_AET)
    ae.supported_contexts = StoragePresentationContexts
    return ae.start_server(
        ("0.0.0.0", SCP_PORT), block=False, evt_handlers=[(evt.EVT_C_STORE, _on_store)]
    )


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
        for _status, ident in assoc.send_c_find(
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
        for status, _ in assoc.send_c_move(
            ds, SELF_AET, StudyRootQueryRetrieveInformationModelMove
        ):
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
        queryable = len(_qido_studies(ds.StudyInstanceUID)) > 0
    ac.record_event(result, "cstore_to_proxy", accepted=accepted, queryable=queryable)


def qido_cached(study_uid):
    ok = len(_qido_studies(study_uid)) > 0
    ac.record_event(result, "qido", study=study_uid, ok=ok)


def probe_rejected(host, port, called_aet, calling_aet, kind):
    ae = AE(ae_title=calling_aet)
    ae.add_requested_context(Verification)
    assoc = ae.associate(host, port, ae_title=called_aet)
    rejected = not assoc.is_established
    if assoc.is_established:
        assoc.release()
    ac.record_event(result, kind, rejected=rejected)


def wait_ready(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == 200:
                    ac.record_event(result, "proxy_ready", waited=True)
                    return True
        except Exception:
            pass
        time.sleep(5)
    ac.record_event(result, "proxy_ready", waited=False)
    return False


def barrier(phase, mine, names):
    """Signal `mine`, wait for all `names`, and record whether the rendezvous succeeded.
    A timeout means the concurrent phase ran non-concurrently — recorded for the host gate."""
    ac.barrier_signal(BARRIER_DIR, mine)
    ok = ac.barrier_wait_all(BARRIER_DIR, names, timeout=1800)
    ac.record_event(result, "barrier", phase=phase, ok=ok)
    return ok


def main():
    server = start_scp()
    try:
        wait_ready(f"http://{PROXY_HOST}:{PROXY_REST}/system", 2400)
        if ROLE == "clienta":
            # S2 negative
            probe_rejected(PACS_HOST, PACS_DICOM, PACS_AET, SELF_AET, "direct_pacs_probe")
            probe_rejected(PROXY_HOST, PROXY_DICOM, PROXY_AET, "GHOST", "spoof_proxy_probe")
            # S1 routing + S3 pass-through (cache warm after the move)
            _current_phase["name"] = "s1"
            cmove("s1", STUDY[1])
            drain("s1", STUDY[1], INSTANCES)
            cfind_cyrillic(STUDY[1], study_plan.CYRILLIC_NAME)
            qido_cached(STUDY[1])
            wado_cached(STUDY[1])
            cstore_to_proxy()
            # S4 different studies (A=study2). Switch phase only after the barrier so a late
            # S1 sub-operation arriving mid-rendezvous cannot be mislabelled into s4_diff.
            barrier("s4_diff", "a_s4", ["a_s4", "b_s4"])
            _current_phase["name"] = "s4_diff"
            cmove("s4_diff", STUDY[2])
            drain("s4_diff", STUDY[2], INSTANCES)
            # S5 same study (both = study1)
            barrier("s5_same", "a_s5", ["a_s5", "b_s5"])
            _current_phase["name"] = "s5_same"
            cmove("s5_same", STUDY[1])
            drain("s5_same", STUDY[1], INSTANCES)
        else:  # clientb
            _current_phase["name"] = "s1"  # idle: SCP up, receives nothing
            time.sleep(1)
            barrier("s4_diff", "b_s4", ["a_s4", "b_s4"])
            _current_phase["name"] = "s4_diff"
            cmove("s4_diff", STUDY[3])
            drain("s4_diff", STUDY[3], INSTANCES)
            barrier("s5_same", "b_s5", ["a_s5", "b_s5"])
            _current_phase["name"] = "s5_same"
            cmove("s5_same", STUDY[1])
            drain("s5_same", STUDY[1], INSTANCES)
        time.sleep(5)  # let final sub-operations land on the SCP
    finally:
        ac.write_result(RESULT_PATH, result)
        ac.barrier_signal(BARRIER_DIR, ROLE + "_phases_done")
        server.shutdown()


if __name__ == "__main__":
    main()
