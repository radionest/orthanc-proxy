import json
import os

CFG = os.path.join(os.path.dirname(__file__), "..", "staging", "vm-net", "config")
ALLOW_FLAGS = [
    "DicomAlwaysAllowEcho",
    "DicomAlwaysAllowStore",
    "DicomAlwaysAllowFind",
    "DicomAlwaysAllowMove",
    "DicomAlwaysAllowGet",
]


def _load(name):
    with open(os.path.join(CFG, name), encoding="utf-8") as f:
        return json.load(f)


def test_pacs_knows_only_proxy_and_denies_all_defaults():
    pacs = _load("pacs.json")
    assert pacs["DicomAet"] == "HOSPITALPACS"
    assert pacs["DicomCheckCalledAet"] is True
    for flag in ALLOW_FLAGS:
        assert pacs[flag] is False, flag
    assert list(pacs["DicomModalities"].keys()) == ["proxy"]
    assert pacs["DicomModalities"]["proxy"]["AET"] == "CLARINETPROXY"
    assert pacs["DefaultEncoding"] == "Utf8"


def test_proxy_knows_pacs_and_both_clients():
    proxy = _load("proxy.json")
    assert proxy["DicomAet"] == "CLARINETPROXY"
    for flag in ALLOW_FLAGS:
        assert proxy[flag] is False, flag
    assert set(proxy["DicomModalities"].keys()) == {"pacs", "clienta", "clientb"}
    assert proxy["DicomModalities"]["pacs"]["AET"] == "HOSPITALPACS"
    assert proxy["DicomModalities"]["clienta"]["AET"] == "CLIENTA"
    assert proxy["DicomModalities"]["clientb"]["AET"] == "CLIENTB"
    assert proxy["PythonScript"] == "/opt/clarinet/clarinet_proxy.py"
    assert proxy["DefaultEncoding"] == "Utf8"
