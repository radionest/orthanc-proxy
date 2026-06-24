"""Orthanc entry point: replaces the C-FIND/C-MOVE SCP with a pass-through to the hospital PACS."""

import os
import sys
import json
import time

import orthanc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proxy_core as core

SELF_AET = core.SELF_AET
UPSTREAM = core.UPSTREAM
ARRIVAL_TIMEOUT = 600.0   # seconds to wait for the upstream pull to deliver all instances
POLL_INTERVAL = 1.0


def _get(uri):
    return json.loads(orthanc.RestApiGet(uri))


def _post(uri, body):
    return json.loads(orthanc.RestApiPost(uri, json.dumps(body)))


def OnFind(answers, query, issuerAet, calledAet):
    tags = [(query.GetFindQueryTagName(i), query.GetFindQueryValue(i))
            for i in range(query.GetFindQuerySize())]
    level, q = core.build_find_request(tags)
    qid = _post("/modalities/%s/query" % UPSTREAM, {"Level": level, "Query": q})["ID"]
    try:
        for i in _get("/queries/%s/answers" % qid):
            content = _get("/queries/%s/answers/%s/content?simplify" % (qid, i))
            answer = core.pin_charset(content)
            answers.FindAddAnswer(orthanc.CreateDicom(
                json.dumps(answer), None, orthanc.CreateDicomFlags.NONE))
    finally:
        orthanc.RestApiDelete("/queries/%s" % qid)


orthanc.RegisterFindCallback(OnFind)
