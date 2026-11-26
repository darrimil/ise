"""ISE network-device inventory via ERS (port 9060, JSON).

List : GET /ers/config/networkdevice?size=100&page=N    (ERS max page size is 100)
Get  : GET /ers/config/networkdevice/{id}                (no bulk-read in ERS -> one call per device, threaded)

NetworkDeviceIPList[] holds ip+mask. Masks of /32 are device addresses ('mgmt'); wider masks are real subnets.
Site and type are derived from the NetworkDeviceGroupList ('Location#All Locations#X', 'Device Type#...#Y').
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from ..http import ApiClient
from ..models import Device, SubnetRecord, network_from_ip_mask
from . import Source, secret

JSON = {"Accept": "application/json", "Content-Type": "application/json"}


def ise_client(cfg) -> ApiClient:
    return ApiClient(cfg["url"], headers=JSON, auth=(cfg["username"], secret(cfg, "password")),
                     verify=cfg.get("verify", True), timeout=cfg.get("timeout", 30))


def list_resources(client, path, page_size=100):
    page = 1
    while True:
        res = client.get_json(path, params={"size": page_size, "page": page})["SearchResult"]
        yield from res.get("resources", [])
        if not res.get("nextPage"):
            return
        page += 1


class IseSource(Source):
    def __init__(self, cfg, client=None):
        self.name = cfg.get("name", "ise")
        self.workers = cfg.get("workers", 16)
        self.client = client or ise_client(cfg)

    def collect(self):
        refs = list(list_resources(self.client, "/ers/config/networkdevice"))
        with ThreadPoolExecutor(self.workers) as ex:
            details = list(ex.map(lambda r: self.client.get_json(f"/ers/config/networkdevice/{r['id']}")["NetworkDevice"], refs))
        devices, subnets = [], []
        for nd in details:
            groups = {g.split("#")[0]: g.split("#")[-1] for g in nd.get("NetworkDeviceGroupList", [])}
            dtype = groups.get("Device Type", "").lower()
            kind = "ap" if "wireless" in dtype or "ap" == dtype else "switch" if "switch" in dtype else "other"
            ips = nd.get("NetworkDeviceIPList", [])
            devices.append(Device(id=nd["id"], name=nd["name"], kind=kind, site=groups.get("Location", ""),
                                  source=self.name, mgmt_ip=ips[0]["ipaddress"] if ips else ""))
            for e in ips:
                try:
                    prefix = network_from_ip_mask(e["ipaddress"], e.get("mask", 32))
                except (ValueError, KeyError):
                    continue
                subnets.append(SubnetRecord(prefix=prefix, source=self.name, device_id=nd["id"], device_name=nd["name"],
                                            site=groups.get("Location", ""),
                                            kind="mgmt" if prefix.endswith("/32") else "subnet"))
        return devices, subnets
