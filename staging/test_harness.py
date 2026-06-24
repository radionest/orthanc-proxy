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
