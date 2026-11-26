"""Catalyst Center (DNA Center) Intent API.

Devices : GET /dna/intent/api/v1/network-device   (offset is 1-based, limit<=500)
Subnets : GET /dna/intent/api/v1/interface        (all interfaces w/ ipv4Address+ipv4Mask, incl. Vlan SVIs)
Site    : GET /dna/intent/api/v1/site + /membership/{siteId}   (best effort)

At 30k devices the interface table can exceed 1M rows, so pages are fetched in parallel.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import requests

from ..http import ApiClient
from ..models import Device, SubnetRecord, network_from_ip_mask
from . import Source, secret

FAMILY_KIND = (("access point", "ap"), ("unified ap", "ap"), ("wireless controller", "wlc"),
               ("switch", "switch"), ("router", "router"), ("firewall", "firewall"))


def device_kind(family: str) -> str:
    f = (family or "").lower()
    return next((k for needle, k in FAMILY_KIND if needle in f), "other")


class CatalystCenterSource(Source):
    def __init__(self, cfg, client=None):
        self.name = cfg.get("name", "catalyst_center")
        self.cfg = cfg
        self.workers = cfg.get("workers", 8)
        self.limit = 500
        self.use_sites = cfg.get("site_membership", True)
        self.client = client or ApiClient(cfg["url"], verify=cfg.get("verify", True), timeout=cfg.get("timeout", 60))
        self._injected = client is not None

    def _login(self):
        if self._injected:
            return
        r = self.client.request("POST", "/dna/system/api/v1/auth/token",
                                auth=requests.auth.HTTPBasicAuth(self.cfg["username"], secret(self.cfg, "password")))
        r.raise_for_status()
        self.client.session.headers["X-Auth-Token"] = r.json()["Token"]

    def _get(self, path, **params):
        r = self.client.request("GET", path, params=params or None)
        if r.status_code == 401:  # token lifetime is 1h; long collections outlive it
            self._login()
            r = self.client.request("GET", path, params=params or None)
        r.raise_for_status()
        return r.json()

    def _paged(self, path):
        total = int(self._get(f"{path}/count")["response"])
        offsets = range(1, total + 1, self.limit)
        with ThreadPoolExecutor(self.workers) as ex:
            for page in ex.map(lambda o: self._get(path, offset=o, limit=self.limit)["response"], offsets):
                yield from page

    def _site_map(self):
        out = {}
        try:
            sites = self._get("/dna/intent/api/v1/site")["response"]
            # most specific (longest hierarchy) wins when a device is a member of nested sites
            for s in sorted(sites, key=lambda s: len(s.get("siteNameHierarchy", ""))):
                m = self._get(f"/dna/intent/api/v1/membership/{s['id']}")
                for grp in m.get("device", []):
                    for d in grp.get("response", []):
                        out[d["instanceUuid"]] = s.get("siteNameHierarchy", "")
        except requests.RequestException:
            pass
        return out

    def collect(self):
        self._login()
        sites = self._site_map() if self.use_sites else {}
        devices = {}
        for d in self._paged("/dna/intent/api/v1/network-device"):
            devices[d["id"]] = Device(id=d["id"], name=d.get("hostname") or d["id"],
                                      mgmt_ip=d.get("managementIpAddress") or "", kind=device_kind(d.get("family")),
                                      site=sites.get(d["id"], ""), source=self.name, model=d.get("platformId") or "")
        subnets = []
        for i in self._paged("/dna/intent/api/v1/interface"):
            ip, mask = i.get("ipv4Address"), i.get("ipv4Mask")
            port = i.get("portName") or ""
            if not ip or not mask or port.lower().startswith(("loopback", "mgmt", "gigabitethernet0/0")):
                continue
            dev = devices.get(i.get("deviceId"))
            try:
                prefix = network_from_ip_mask(ip, mask)
            except ValueError:
                continue
            subnets.append(SubnetRecord(
                prefix=prefix, source=self.name, device_id=i.get("deviceId", ""),
                device_name=dev.name if dev else "", site=dev.site if dev else "",
                kind="svi" if port.lower().startswith("vlan") else "routed",
                vlan=str(i.get("vlanId") or ""), label=port))
        return list(devices.values()), subnets
