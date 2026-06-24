import proxy_core


def test_constants_present():
    assert proxy_core.SELF_AET == "CLARINETPROXY"
    assert proxy_core.UPSTREAM == "pacs"
    assert proxy_core.ANSWER_CHARSET == "ISO_IR 192"
    assert proxy_core.LEVEL_KEYS["SERIES"] == ["StudyInstanceUID", "SeriesInstanceUID"]
