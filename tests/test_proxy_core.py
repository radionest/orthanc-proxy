import proxy_core


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
