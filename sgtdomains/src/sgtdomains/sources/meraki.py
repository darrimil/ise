"""Meraki Dashboard API v1.

Devices     : /organizations/{id}/devices/statuses           (lanIp = management IP of MR/MS/MX)
MX subnets  : /networks/{id}/appliance/vlans                  (subnet per VLAN; 400/404 when VLANs disabled)
MS L3 (opt) : /devices/{serial}/switch/routing/interfaces     (one call per switch -> set switch_l3: true; rate limit is ~10 rps/org)
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..http import ApiClient
from ..models import Device, SubnetRecord, normalize_prefix
from . import Source, secret

PRODUCT_KIND = {"wireless": "ap", "switch": "switch", "appliance": "firewall", "camera": "other"}


class MerakiSource(Source):
    def __init__(self, cfg, client=None):
        self.name = cfg.get("name", "meraki")
        self.org_ids = [str(o) for o in cfg.get("org_ids", [])]
        self.switch_l3 = cfg.get("switch_l3", False)
        self.workers = cfg.get("workers", 8)
        self.client = client or ApiClient(
            cfg.get("url", "https://api.meraki.com/api/v1"), timeout=cfg.get("timeout", 60),
            headers={"X-Cisco-Meraki-API-Key": secret(cfg, "api_key")})

    def _paged(self, path, **params):
        """Meraki paginates with RFC5988 Link headers."""
        url, params = path, dict(params, perPage=1000)
        while url:
            r = self.client.request("GET", url, params=params)
            if r.status_code in (400, 404):
                return
            r.raise_for_status()
            data = r.json()
            yield from (data if isinstance(data, list) else data.get("items", []))
            url, params = r.links.get("next", {}).get("url"), None

    def collect(self):
        orgs = self.org_ids or [o["id"] for o in self._paged("/organizations")]
        devices, subnets = {}, []
        for org in orgs:
            nets = {n["id"]: n for n in self._paged(f"/organizations/{org}/networks")}
            for d in self._paged(f"/organizations/{org}/devices/statuses"):
                site = nets.get(d.get("networkId"), {}).get("name", "")
                devices[d["serial"]] = Device(
                    id=d["serial"], name=d.get("name") or d["serial"], mgmt_ip=d.get("lanIp") or "",
                    kind=PRODUCT_KIND.get(d.get("productType"), "other"), site=site, source=self.name,
                    model=d.get("model", ""))
            for nid, net in nets.items():
                if "appliance" not in net.get("productTypes", []):
                    continue
                for v in self._paged(f"/networks/{nid}/appliance/vlans"):
                    if v.get("subnet"):
                        subnets.append(SubnetRecord(
                            prefix=normalize_prefix(v["subnet"]), source=self.name, site=net.get("name", ""),
                            kind="vlan", vlan=str(v.get("id", "")), label=v.get("name", "")))
            if self.switch_l3:
                switches = [d for d in devices.values() if d.kind == "switch"]
                with ThreadPoolExecutor(self.workers) as ex:
                    for dev, rows in zip(switches, ex.map(
                            lambda s: list(self._paged(f"/devices/{s.id}/switch/routing/interfaces")), switches)):
                        for i in rows:
                            if i.get("subnet"):
                                subnets.append(SubnetRecord(
                                    prefix=normalize_prefix(i["subnet"]), source=self.name, device_id=dev.id,
                                    device_name=dev.name, site=dev.site, kind="svi",
                                    vlan=str(i.get("vlanId", "")), label=i.get("name", "")))
        return list(devices.values()), subnets
