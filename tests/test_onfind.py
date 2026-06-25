import importlib
import json
import sys

import pytest
from fakes import FakeAnswers, FakeOrthanc, FakeQuery


def load_proxy(routes):
    fake = FakeOrthanc(routes)
    sys.modules["orthanc"] = fake
    sys.modules.pop("clarinet_proxy", None)
    cp = importlib.import_module("clarinet_proxy")
    return cp, fake


def test_onfind_forwards_query_and_pins_charset():
    routes = {
        ("POST", "/modalities/pacs/query"): {"ID": "q1"},
        ("GET", "/queries/q1/answers"): [0],
        ("GET", "/queries/q1/answers/0/content?simplify"): {"PatientName": "Иванов"},
    }
    cp, fake = load_proxy(routes)
    answers = FakeAnswers()
    query = FakeQuery([("QueryRetrieveLevel", "STUDY"), ("PatientName", "")])
    cp.OnFind(answers, query, "WORKER", "CLARINETPROXY")

    # forwarded the right query upstream
    posted = [b for (m, u, b) in fake.calls if u == "/modalities/pacs/query"][0]
    assert json.loads(posted) == {
        "Level": "STUDY",
        "Query": {"PatientName": "", "SpecificCharacterSet": "ISO_IR 192"},
    }
    # answer carries pinned charset + Cyrillic intact
    assert json.loads(answers.added[0].decode("utf-8")) == {
        "PatientName": "Иванов",
        "SpecificCharacterSet": "ISO_IR 192",
    }
    # query handle released
    assert ("DELETE", "/queries/q1", None) in fake.calls


def test_onfind_releases_query_handle_on_error():
    def boom(uri, body):
        raise RuntimeError("answers fetch failed")

    routes = {
        ("POST", "/modalities/pacs/query"): {"ID": "q1"},
        ("GET", "/queries/q1/answers"): boom,
    }
    cp, fake = load_proxy(routes)
    with pytest.raises(RuntimeError):
        cp.OnFind(FakeAnswers(), FakeQuery([("PatientName", "")]), "W", "CLARINETPROXY")
    assert ("DELETE", "/queries/q1", None) in fake.calls
