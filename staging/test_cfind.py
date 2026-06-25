import requests
from fixtures import upload_cyrillic_study


def test_cfind_forwarded_and_charset_preserved(pacs_url, proxy_url, worker_url):
    study_uid = upload_cyrillic_study(pacs_url)

    # worker issues C-FIND to the proxy; proxy forwards to the PACS
    q = requests.post(
        worker_url + "/modalities/proxy/query",
        json={"Level": "Study", "Query": {"StudyInstanceUID": study_uid, "PatientName": ""}},
    )
    q.raise_for_status()
    query_id = q.json()["ID"]

    answers = requests.get(worker_url + f"/queries/{query_id}/answers").json()
    assert len(answers) == 1

    content = requests.get(worker_url + f"/queries/{query_id}/answers/0/content?simplify").json()
    assert content["StudyInstanceUID"] == study_uid
    assert content["PatientName"] == "Иванов^Иван"  # Cyrillic survived the proxy
