import json

from fakes import FakeAnswers, FakeOrthanc, FakeQuery


def test_fake_routes_and_records():
    o = FakeOrthanc({("POST", "/x"): {"ID": "q1"}, ("GET", "/y"): [0, 1]})
    assert json.loads(o.RestApiPost("/x", json.dumps({"a": 1}))) == {"ID": "q1"}
    assert json.loads(o.RestApiGet("/y")) == [0, 1]
    o.RestApiDelete("/z")
    assert ("POST", "/x", '{"a": 1}') in o.calls
    assert ("DELETE", "/z", None) in o.calls


def test_fake_query_iteration():
    q = FakeQuery([("QueryRetrieveLevel", "STUDY"), ("PatientID", "7")])
    assert q.GetFindQuerySize() == 2
    assert q.GetFindQueryTagName(1) == "PatientID"
    assert q.GetFindQueryValue(1) == "7"


def test_fake_answers_collect():
    a = FakeAnswers()
    a.FindAddAnswer(b"buf")
    assert a.added == [b"buf"]
