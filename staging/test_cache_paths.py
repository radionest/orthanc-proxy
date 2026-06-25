import pytest
import requests
from fixtures import upload_cyrillic_study


def test_cmove_self_caches_then_dicomweb(pacs_url, proxy_url, worker_url):
    study_uid = upload_cyrillic_study(pacs_url)

    # pre-load the proxy cache via dest=self C-MOVE (no forward)
    requests.post(
        worker_url + "/modalities/proxy/move",
        json={
            "Level": "Study",
            "Resources": [{"StudyInstanceUID": study_uid}],
            "TargetAet": "CLARINETPROXY",
            "Synchronous": True,
        },
        timeout=120,
    ).raise_for_status()

    # DICOMweb (QIDO) over the cache returns the study
    qido = requests.get(
        proxy_url + "/dicom-web/studies",
        params={"StudyInstanceUID": study_uid},
        headers={"Accept": "application/dicom+json"},
    ).json()
    assert any(s["0020000D"]["Value"][0] == study_uid for s in qido)


# C-GET is an optional retrieval path (spec §4). Driving it via Orthanc's /queries/{id}/retrieve
# against the plugin-served proxy is Orthanc-version-dependent: it works on the orthancteam 24.10
# image (Orthanc 1.12.5), but on the LSB 1.12.11 build the C-GET-RQ reaches the proxy with an empty
# StudyInstanceUID ("Cannot determine what resources are requested by C-GET"). The proxy's C-GET SCP
# itself is fine — a downstream that sends a well-formed C-GET-RQ is served from cache; only this
# query-handle driver regresses. Non-strict xfail so the suite stays green on both stacks while
# recording the difference.
@pytest.mark.xfail(
    strict=False,
    reason="C-GET via /queries/retrieve sends an empty UID on Orthanc 1.12.11 (optional path)",
)
def test_cget_from_cache(pacs_url, proxy_url, worker_url):
    study_uid = upload_cyrillic_study(pacs_url)
    requests.post(
        worker_url + "/modalities/proxy/move",
        json={
            "Level": "Study",
            "Resources": [{"StudyInstanceUID": study_uid}],
            "TargetAet": "CLARINETPROXY",
            "Synchronous": True,
        },
        timeout=120,
    ).raise_for_status()

    cg = requests.post(
        worker_url + "/modalities/proxy/query",
        json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid}},
    )
    cg.raise_for_status()
    qid = cg.json()["ID"]
    requests.post(
        worker_url + f"/queries/{qid}/retrieve",
        json={"TargetAet": "WORKER", "RetrieveMethod": "C-GET", "Synchronous": True},
        timeout=120,
    ).raise_for_status()
    on_worker = requests.post(
        worker_url + "/tools/find",
        json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid}},
    ).json()
    assert len(on_worker) == 1
