"""Pure proxy logic — no `import orthanc`, fully unit-testable."""

SELF_AET = "CLARINETPROXY"
UPSTREAM = "pacs"
ANSWER_CHARSET = "ISO_IR 192"

# DICOM unique keys required at each Q/R level (PATIENT level is unsupported).
LEVEL_KEYS = {
    "STUDY": ["StudyInstanceUID"],
    "SERIES": ["StudyInstanceUID", "SeriesInstanceUID"],
    "INSTANCE": ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"],
}


def build_find_request(tags):
    """tags: list[(name, value)] from the C-FIND query object.
    Returns (level, query_dict)."""
    level = "STUDY"
    query = {}
    for name, value in tags:
        if name == "QueryRetrieveLevel":
            level = value or "STUDY"
        else:
            query[name] = value
    return level, query


def pin_charset(content):
    """content: simplified tag->value dict (UTF-8) from /answers/{i}/content?simplify.
    Returns a copy with SpecificCharacterSet pinned to ISO_IR 192 so CreateDicom keeps UTF-8."""
    answer = dict(content)
    answer["SpecificCharacterSet"] = ANSWER_CHARSET
    return answer
