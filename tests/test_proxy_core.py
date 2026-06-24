import pytest
import proxy_core

MODS = {
    "pacs":   {"AET": "PACS",   "Host": "10.0.0.1", "Port": 104},
    "worker": {"AET": "WORKER", "Host": "10.0.0.2", "Port": 4242},
}


def test_build_find_request_splits_level_from_query():
    tags = [("QueryRetrieveLevel", "SERIES"),
            ("PatientID", "42"),
            ("StudyInstanceUID", "")]
    level, query = proxy_core.build_find_request(tags)
    assert level == "SERIES"
    assert query == {"PatientID": "42", "StudyInstanceUID": ""}


def test_build_find_request_defaults_to_study():
    level, query = proxy_core.build_find_request([("PatientName", "")])
    assert level == "STUDY"
    assert query == {"PatientName": ""}


def test_pin_charset_forces_utf8_and_copies():
    content = {"PatientName": "Иванов", "SpecificCharacterSet": "ISO_IR 100"}
    answer = proxy_core.pin_charset(content)
    assert answer["SpecificCharacterSet"] == "ISO_IR 192"
    assert answer["PatientName"] == "Иванов"
    assert content["SpecificCharacterSet"] == "ISO_IR 100"  # original untouched


def test_parse_move_request_study_level():
    level, uids = proxy_core.parse_move_request(
        {"Level": "STUDY", "StudyInstanceUID": "1.2.3"})
    assert level == "STUDY"
    assert uids == [{"StudyInstanceUID": "1.2.3"}]


def test_parse_move_request_series_full_hierarchy():
    level, uids = proxy_core.parse_move_request(
        {"Level": "SERIES", "StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.9"})
    assert uids == [{"StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.9"}]


def test_parse_move_request_multi_study_splits_positionally():
    level, uids = proxy_core.parse_move_request(
        {"Level": "STUDY", "StudyInstanceUID": "1.2\\1.3"})
    assert uids == [{"StudyInstanceUID": "1.2"}, {"StudyInstanceUID": "1.3"}]


def test_parse_move_request_rejects_patient_level():
    with pytest.raises(ValueError):
        proxy_core.parse_move_request({"Level": "PATIENT", "PatientID": "x"})


def test_resolve_destination_self_is_cache():
    assert proxy_core.resolve_destination("CLARINETPROXY", "CLARINETPROXY", MODS, "pacs") == ("cache", None)


def test_resolve_destination_worker_is_forward():
    assert proxy_core.resolve_destination("WORKER", "CLARINETPROXY", MODS, "pacs") == ("forward", "worker")


def test_resolve_destination_unknown_raises():
    with pytest.raises(ValueError):
        proxy_core.resolve_destination("GHOST", "CLARINETPROXY", MODS, "pacs")


def test_resolve_destination_upstream_is_not_a_valid_worker():
    with pytest.raises(ValueError):
        proxy_core.resolve_destination("PACS", "CLARINETPROXY", MODS, "pacs")


def test_resolve_destination_missing_target_raises():
    with pytest.raises(ValueError):
        proxy_core.resolve_destination("", "CLARINETPROXY", MODS, "pacs")


def test_local_find_bodies_per_item_full_hierarchy():
    uids = [{"StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.9"}]
    bodies = proxy_core.local_find_bodies("SERIES", uids)
    assert bodies == [{
        "Level": "Instance",
        "Query": {"StudyInstanceUID": "1.2", "SeriesInstanceUID": "1.2.9"},
        "Expand": True,
    }]


def test_count_query_bodies_instance_level():
    uids = [{"StudyInstanceUID": "1.2"}, {"StudyInstanceUID": "1.3"}]
    bodies = proxy_core.count_query_bodies("STUDY", uids)
    assert bodies == [
        {"Level": "Instance", "Query": {"StudyInstanceUID": "1.2"}},
        {"Level": "Instance", "Query": {"StudyInstanceUID": "1.3"}},
    ]


def test_select_unforwarded_skips_forwarded():
    assert proxy_core.select_unforwarded(["a", "b", "c"], {"a", "b"}) == "c"


def test_select_unforwarded_none_left():
    assert proxy_core.select_unforwarded(["a"], {"a"}) is None
