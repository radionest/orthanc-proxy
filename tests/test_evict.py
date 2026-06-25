import datetime
import importlib

evict = importlib.import_module("evict")


class FakeHTTP:
    def __init__(self, studies, stats):
        self._studies = studies
        self._stats = stats
        self.deleted = []

    def get(self, url, timeout=10):
        class R:
            def __init__(self, payload):
                self._p = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self._p

        if url.endswith("/studies?expand"):
            return R(self._studies)
        if url.endswith("/statistics"):
            return R(self._stats)
        raise AssertionError(url)

    def delete(self, url, timeout=10):
        self.deleted.append(url.rsplit("/", 1)[1])

        class R:
            def raise_for_status(self):
                pass

        return R()


def test_select_and_delete_removes_only_expired():
    now = datetime.datetime(2026, 6, 24, 12, 0, 0)
    http = FakeHTTP(
        studies=[
            {"ID": "old", "LastUpdate": "20260624T112000"},
            {"ID": "fresh", "LastUpdate": "20260624T115900"},
        ],
        stats={"TotalDiskSizeMB": 100},
    )
    deleted = evict.select_and_delete("http://x", now, 1200, 14336, http=http)
    assert deleted == ["old"]
    assert http.deleted == ["old"]
