from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter


class ApiClient:
    """requests wrapper with connection pooling and 429/5xx retry (honours Retry-After; Meraki and ISE both throttle)."""

    def __init__(self, base_url, *, headers=None, auth=None, verify=True, timeout=30, retries=5, pool=32):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retries = retries
        self.session = requests.Session()
        self.session.headers.update(headers or {})
        self.session.auth = auth
        self.session.verify = verify
        adapter = HTTPAdapter(pool_connections=pool, pool_maxsize=pool)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def request(self, method, path, **kw):
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        for attempt in range(self.retries + 1):
            r = self.session.request(method, url, timeout=self.timeout, **kw)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt < self.retries:
                    wait = r.headers.get("Retry-After")
                    time.sleep(float(wait) if wait and wait.replace(".", "").isdigit() else 2 ** attempt)
                    continue
            return r
        return r

    def get_json(self, path, **kw):
        r = self.request("GET", path, **kw)
        r.raise_for_status()
        return r.json()
