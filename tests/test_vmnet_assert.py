# tests/test_vmnet_assert.py
import vmnet_assert as va

STUDY1 = "1.2.826.0.1.3680043.8.498.1"
STUDY2 = "1.2.826.0.1.3680043.8.498.2"
STUDY3 = "1.2.826.0.1.3680043.8.498.3"


def _client(role, received=None, events=None):
    return {"role": role, "aet": role.upper(), "received": received or {}, "events": events or []}


def test_s1_pass_when_a_gets_all_and_b_gets_none():
    a = _client("clienta", {"s1": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s1": {}})
    assert va.check_s1(a, b, STUDY1, 1000) == []


def test_s1_fails_when_b_also_received():
    a = _client("clienta", {"s1": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s1": {STUDY1: {"count": 5, "from": "CLARINETPROXY"}}})
    assert va.check_s1(a, b, STUDY1, 1000)  # non-empty -> failure


def test_s2_pass_when_both_probes_rejected():
    a = _client(
        "clienta",
        events=[
            {"kind": "direct_pacs_probe", "rejected": True},
            {"kind": "spoof_proxy_probe", "rejected": True},
        ],
    )
    assert va.check_s2(a) == []


def test_s2_fails_when_direct_pacs_accepted():
    a = _client(
        "clienta",
        events=[
            {"kind": "direct_pacs_probe", "rejected": False},
            {"kind": "spoof_proxy_probe", "rejected": True},
        ],
    )
    assert va.check_s2(a)


def test_s3_pass_on_cyrillic_and_qido_and_store():
    a = _client(
        "clienta",
        events=[
            {"kind": "cfind_cyrillic", "name": "Иванов^Пётр", "ok": True},
            {"kind": "qido", "study": STUDY1, "ok": True},
            {"kind": "cstore_to_proxy", "accepted": True, "queryable": True},
        ],
    )
    assert va.check_s3(a) == []


def test_s4_pass_no_cross_contamination():
    a = _client("clienta", {"s4_diff": {STUDY2: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s4_diff": {STUDY3: {"count": 1000, "from": "CLARINETPROXY"}}})
    assert va.check_s4(a, b, STUDY2, STUDY3, 1000) == []


def test_s4_fails_when_a_sees_b_study():
    a = _client(
        "clienta",
        {
            "s4_diff": {
                STUDY2: {"count": 1000, "from": "CLARINETPROXY"},
                STUDY3: {"count": 3, "from": "CLARINETPROXY"},
            }
        },
    )
    b = _client("clientb", {"s4_diff": {STUDY3: {"count": 1000, "from": "CLARINETPROXY"}}})
    assert va.check_s4(a, b, STUDY2, STUDY3, 1000)


def test_s5_pass_when_both_get_full_study():
    a = _client("clienta", {"s5_same": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s5_same": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    assert va.check_s5(a, b, STUDY1, 1000) == []


def test_s3_fails_when_cstore_not_queryable():
    a = _client(
        "clienta",
        events=[
            {"kind": "cfind_cyrillic", "name": "Иванов^Пётр", "ok": True},
            {"kind": "qido", "study": STUDY1, "ok": True},
            {"kind": "cstore_to_proxy", "accepted": True, "queryable": False},
        ],
    )
    assert va.check_s3(a)


def test_s5_fails_when_b_incomplete():
    a = _client("clienta", {"s5_same": {STUDY1: {"count": 1000, "from": "CLARINETPROXY"}}})
    b = _client("clientb", {"s5_same": {STUDY1: {"count": 500, "from": "CLARINETPROXY"}}})
    assert va.check_s5(a, b, STUDY1, 1000)


def test_s6_pass_on_ttl_delete_and_warn():
    proxy = {
        "role": "proxy",
        "studies_before_evict": 3,
        "studies_after_evict": 0,
        "fill_warn_logged": True,
        "pacs_move_jobs_observed": 4,
    }
    assert va.check_s6(proxy) == []


def test_s6_fails_when_nothing_evicted():
    proxy = {
        "role": "proxy",
        "studies_before_evict": 3,
        "studies_after_evict": 3,
        "fill_warn_logged": True,
    }
    assert va.check_s6(proxy)
