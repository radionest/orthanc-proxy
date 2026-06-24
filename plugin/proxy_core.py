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


def parse_move_request(request):
    """request: the C-MOVE callback dict. Returns (level, uids) where uids is a list of
    fully-qualified UID dicts, one per requested item. '\\'-separated values expand positionally."""
    level = request["Level"]
    if level not in LEVEL_KEYS:
        raise ValueError("unsupported C-MOVE level %r (PATIENT not supported)" % level)
    keys = LEVEL_KEYS[level]
    split = {k: (request.get(k, "") or "").split("\\") for k in keys}
    n = max((len(v) for v in split.values()), default=1)
    uids = []
    for i in range(n):
        item = {}
        for k in keys:
            vals = split[k]
            item[k] = vals[i] if i < len(vals) else vals[-1]
        uids.append(item)
    return level, uids


def find_alias_for_aet(modalities, aet):
    for alias, entry in modalities.items():
        if entry.get("AET") == aet:
            return alias
    return None


def resolve_destination(target_aet, self_aet, modalities, upstream_alias):
    if not target_aet:
        raise ValueError("malformed C-MOVE-RQ: missing TargetAET")
    if target_aet == self_aet:
        return ("cache", None)
    alias = find_alias_for_aet(modalities, target_aet)
    if alias is None or alias == upstream_alias:
        raise ValueError("unknown move destination AET %r" % target_aet)
    return ("forward", alias)


def local_find_bodies(level, uids):
    keys = LEVEL_KEYS[level]
    return [{"Level": "Instance", "Query": {k: u[k] for k in keys}, "Expand": True} for u in uids]


def count_query_bodies(level, uids):
    keys = LEVEL_KEYS[level]
    return [{"Level": "Instance", "Query": {k: u[k] for k in keys}} for u in uids]


def select_unforwarded(found_ids, forwarded):
    for oid in found_ids:
        if oid not in forwarded:
            return oid
    return None
