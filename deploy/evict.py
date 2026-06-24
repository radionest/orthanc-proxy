#!/usr/bin/env python3
"""TTL eviction + storage-fill logging for the clarinet-pacs-proxy cache.

Run by orthanc-proxy-evict.timer every 5 minutes. Deletes studies whose LastUpdate
is older than TTL_SECONDS, and logs a WARN when storage fill >= WARN_FILL of the max."""

import os
import sys
import logging
import datetime

import requests

sys.path.insert(0, os.environ.get("PROXY_CORE_DIR", "/opt/orthanc/plugins"))
import proxy_core as core

BASE_URL = os.environ.get("ORTHANC_URL", "http://127.0.0.1:8042")
TTL_SECONDS = int(os.environ.get("TTL_SECONDS", "1200"))
MAX_STORAGE_MB = int(os.environ.get("MAX_STORAGE_MB", "14336"))
WARN_FILL = float(os.environ.get("WARN_FILL", "0.8"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("evict")


def select_and_delete(base_url, now, ttl_seconds, max_storage_mb, http=requests):
    r = http.get(base_url + "/studies?expand", timeout=10)
    r.raise_for_status()
    expired = core.expired_studies(r.json(), now, ttl_seconds)
    for sid in expired:
        http.delete(base_url + "/studies/" + sid, timeout=10).raise_for_status()
    log.info("evicted %d expired studies", len(expired))

    s = http.get(base_url + "/statistics", timeout=10)
    s.raise_for_status()
    used = float(s.json().get("TotalDiskSizeMB", 0))
    fill = used / max_storage_mb if max_storage_mb else 0.0
    if fill >= WARN_FILL:
        log.warning("storage fill %.0f%% (%.0f / %d MB)", fill * 100, used, max_storage_mb)
    return expired


def main():
    select_and_delete(BASE_URL, datetime.datetime.now(), TTL_SECONDS, MAX_STORAGE_MB)


if __name__ == "__main__":
    main()
