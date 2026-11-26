"""CSV inventory. Accepts any mix of these columns (case-insensitive, extras ignored):

  subnet | (ip + mask|prefix_len)   -> a subnet record
  ip only                           -> /32 management record
  hostname|name, site, kind|type, vlan, label|vlan_name, device
"""
from __future__ import annotations

import csv
import glob

from ..models import Device, SubnetRecord, network_from_ip_mask, normalize_prefix
from . import Source


def _g(row, *keys):
    for k in keys:
        v = row.get(k)
        if v not in (None, ""):
            return v.strip()
    return ""


class CsvSource(Source):
    def __init__(self, cfg):
        self.name = cfg.get("name", "csv")
        self.paths = sorted(glob.glob(cfg["path"]))
        if not self.paths:
            raise SystemExit(f"csv source {self.name}: no files match {cfg['path']}")
        self.skipped = 0

    def collect(self):
        devices, subnets = {}, []
        for path in self.paths:
            with open(path, newline="", encoding="utf-8-sig") as fh:
                for raw in csv.DictReader(fh):
                    row = {(k or "").strip().lower(): v for k, v in raw.items()}
                    try:
                        rec = self._row(row)
                    except ValueError:
                        self.skipped += 1
                        continue
                    subnets.append(rec)
                    if rec.device_name and rec.device_name not in devices:
                        devices[rec.device_name] = Device(
                            id=rec.device_name, name=rec.device_name, site=rec.site, source=self.name,
                            mgmt_ip=_g(row, "ip", "mgmt_ip") if rec.kind == "mgmt" else "")
        return list(devices.values()), subnets

    def _row(self, row) -> SubnetRecord:
        name = _g(row, "hostname", "device", "name")
        subnet = _g(row, "subnet", "prefix", "cidr")
        ip = _g(row, "ip", "mgmt_ip", "ip_address")
        mask = _g(row, "mask", "prefix_len", "netmask")
        if subnet:
            prefix, kind = normalize_prefix(subnet), _g(row, "kind") or "subnet"
        elif ip and mask:
            prefix, kind = network_from_ip_mask(ip, mask), _g(row, "kind") or "subnet"
        elif ip:
            prefix, kind = normalize_prefix(ip + "/32"), "mgmt"
        else:
            raise ValueError("no subnet or ip")
        return SubnetRecord(prefix=prefix, source=self.name, device_id=name, device_name=name,
                            site=_g(row, "site", "location"), kind=kind, vlan=_g(row, "vlan", "vlan_id"),
                            label=_g(row, "label", "vlan_name", "description"))
