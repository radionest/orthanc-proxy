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
