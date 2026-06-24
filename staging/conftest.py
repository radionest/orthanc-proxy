import time
import requests
import pytest

PACS = "http://localhost:8101"
PROXY = "http://localhost:8102"
WORKER = "http://localhost:8103"


def _ready(url):
    try:
        return requests.get(url + "/system", timeout=2).ok
    except requests.RequestException:
        return False


def wait_ready(timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(_ready(u) for u in (PACS, PROXY, WORKER)):
            return
        time.sleep(2)
    raise RuntimeError("staging nodes not ready")


@pytest.fixture(scope="session", autouse=True)
def _harness():
    wait_ready()


@pytest.fixture
def pacs_url():
    return PACS


@pytest.fixture
def proxy_url():
    return PROXY


@pytest.fixture
def worker_url():
    return WORKER
