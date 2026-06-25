import json


class CreateDicomFlags:
    NONE = 0


class ErrorCode:
    SUCCESS = 0


class FakeAnswers:
    def __init__(self):
        self.added = []

    def FindAddAnswer(self, buf):
        self.added.append(buf)


class FakeQuery:
    def __init__(self, tags):
        self._tags = list(tags)

    def GetFindQuerySize(self):
        return len(self._tags)

    def GetFindQueryTagName(self, i):
        return self._tags[i][0]

    def GetFindQueryValue(self, i):
        return self._tags[i][1]


class FakeOrthanc:
    CreateDicomFlags = CreateDicomFlags
    ErrorCode = ErrorCode

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self.find_cb = None
        self.move_cbs = None

    def _resolve(self, method, uri, body):
        self.calls.append((method, uri, body))
        if (method, uri) not in self.routes:
            raise KeyError(f"no fake route for {method} {uri}")
        val = self.routes[(method, uri)]
        if callable(val):
            val = val(uri, body)
        return val

    def RestApiGet(self, uri):
        return json.dumps(self._resolve("GET", uri, None))

    def RestApiPost(self, uri, body):
        return json.dumps(self._resolve("POST", uri, body))

    def RestApiDelete(self, uri):
        self.calls.append(("DELETE", uri, None))

    def CreateDicom(self, json_str, parent, flags):
        return json_str.encode("utf-8")

    def RegisterFindCallback(self, cb):
        self.find_cb = cb

    def RegisterMoveCallback2(self, create, get_size, apply, free):
        self.move_cbs = (create, get_size, apply, free)

    def LogError(self, *a):
        pass

    def LogWarning(self, *a):
        pass

    def LogInfo(self, *a):
        pass
