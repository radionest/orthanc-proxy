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
        url = f"http://{PROXY_HOST}:{PROXY_REST}/dicom-web/studies?StudyInstanceUID={ds.StudyInstanceUID}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                queryable = b"00080020" in r.read() or r.status == 200
        except Exception:
            queryable = False
    ac.record_event(result, "cstore_to_proxy", accepted=accepted, queryable=queryable)


def qido_cached(study_uid):
    url = f"http://{PROXY_HOST}:{PROXY_REST}/dicom-web/studies?StudyInstanceUID={study_uid}"
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
            _current_phase["name"] = "s1"  # idle: SCP up, receives nothing
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
