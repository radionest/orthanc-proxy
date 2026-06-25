"""Orthanc entry point: replaces the C-FIND/C-MOVE SCP with a pass-through to the hospital PACS."""

import json
import os
import sys
import time

import orthanc

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proxy_core as core

SELF_AET = core.SELF_AET
UPSTREAM = core.UPSTREAM
ARRIVAL_TIMEOUT = 600.0  # seconds to wait for the upstream pull to deliver all instances
POLL_INTERVAL = 1.0


def _get(uri):
    return json.loads(orthanc.RestApiGet(uri))


def _post(uri, body):
    return json.loads(orthanc.RestApiPost(uri, json.dumps(body)))


def OnFind(answers, query, issuerAet, calledAet):
    tags = [
        (query.GetFindQueryTagName(i), query.GetFindQueryValue(i))
        for i in range(query.GetFindQuerySize())
    ]
    level, q = core.build_find_request(tags)
    qid = _post(f"/modalities/{UPSTREAM}/query", {"Level": level, "Query": q})["ID"]
    try:
        for i in _get(f"/queries/{qid}/answers"):
            content = _get(f"/queries/{qid}/answers/{i}/content?simplify")
            answer = core.pin_charset(content)
            answers.FindAddAnswer(
                orthanc.CreateDicom(json.dumps(answer), None, orthanc.CreateDicomFlags.NONE)
            )
    finally:
        orthanc.RestApiDelete(f"/queries/{qid}")


orthanc.RegisterFindCallback(OnFind)


class MoveDriver:
    def __init__(self, request):
        self.level, self.uids = core.parse_move_request(request)
        modalities = _get("/modalities?expand")
        self.mode, self.worker = core.resolve_destination(
            request.get("TargetAET"), SELF_AET, modalities, UPSTREAM
        )
        self.forwarded = set()
        self.move_job = None
        self.expected = 0

    def _count(self):
        total = 0
        for body in core.count_query_bodies(self.level, self.uids):
            qid = _post(f"/modalities/{UPSTREAM}/query", body)["ID"]
            try:
                total += len(_get(f"/queries/{qid}/answers"))
            finally:
                orthanc.RestApiDelete(f"/queries/{qid}")
        return total

    def get_size(self):
        self.expected = self._count()
        self.move_job = _post(
            f"/modalities/{UPSTREAM}/move",
            {
                "Level": self.level,
                "Resources": self.uids,
                "TargetAet": SELF_AET,
                "Synchronous": False,
            },
        )["ID"]
        return self.expected

    def _local_ids(self):
        ids = []
        for body in core.local_find_bodies(self.level, self.uids):
            ids.extend(r["ID"] for r in _post("/tools/find", body))
        return ids

    def _job_state(self):
        return _get(f"/jobs/{self.move_job}")["State"]

    def _next_arrival(self):
        deadline = time.time() + ARRIVAL_TIMEOUT
        while True:
            oid = core.select_unforwarded(self._local_ids(), self.forwarded)
            if oid is not None:
                return oid
            state = self._job_state()
            if state == "Failure":
                raise RuntimeError(f"upstream C-MOVE job {self.move_job} failed")
            if state == "Success":
                return None  # job done; fewer instances arrived than expected, nothing left
            if time.time() > deadline:
                raise RuntimeError("timed out waiting for instance arrival")
            time.sleep(POLL_INTERVAL)

    def apply(self):
        oid = self._next_arrival()
        if oid is None:
            return orthanc.ErrorCode.SUCCESS  # no more arrivals; surplus sub-op is a no-op
        if self.mode == "forward":
            _post(f"/modalities/{self.worker}/store", {"Resources": [oid], "Synchronous": True})
        self.forwarded.add(oid)
        return orthanc.ErrorCode.SUCCESS

    def free(self):
        pass


orthanc.RegisterMoveCallback2(
    lambda **r: MoveDriver(r), lambda d: d.get_size(), lambda d: d.apply(), lambda d: d.free()
)
