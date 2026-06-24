import json
import glob
import os

ETC = os.path.join(os.path.dirname(__file__), "..", "etc")


def load(name):
    with open(os.path.join(ETC, name)) as f:
        return json.load(f)


def test_all_config_files_are_valid_json():
    files = glob.glob(os.path.join(ETC, "*.json"))
    assert len(files) == 5
    for f in files:
        with open(f) as fh:
            json.load(fh)


def test_core_security_invariants():
    core = load("10-core.json")
    assert core["DicomAet"] == "CLARINETPROXY"
    assert core["HttpBindAddresses"] == ["127.0.0.1"]
    assert core["RemoteAccessAllowed"] is False
    assert core["MaximumStorageSize"] == 14336
    assert core["MaximumStorageMode"] == "Recycle"
    assert core["MaximumStorageCacheSize"] == 512
    assert core["StableAge"] == 20

    sec = load("20-security.json")
    assert sec["DicomCheckCalledAet"] is True
    assert sec["DicomCheckModalityHost"] is True
    for k in ("DicomAlwaysAllowEcho", "DicomAlwaysAllowStore",
              "DicomAlwaysAllowFind", "DicomAlwaysAllowMove", "DicomAlwaysAllowGet"):
        assert sec[k] is False


def test_modalities_and_dicomweb():
    mods = load("30-modalities.json")["DicomModalities"]
    assert mods["pacs"]["AllowStore"] is True            # accept C-STORE-back on move-to-self
    worker = mods["worker_X"]
    assert worker["AllowFind"] and worker["AllowMove"] and worker["AllowGet"]
    assert worker["AllowStore"] is False

    dw = load("40-dicomweb.json")["DicomWeb"]
    assert dw["Enable"] is True
    assert dw["Root"] == "/dicom-web/"
    assert dw["PublicRoot"] == "/pacs-web/"
    assert "Host" not in dw                               # deprecated; must not be set
