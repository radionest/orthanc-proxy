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
