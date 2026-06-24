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
