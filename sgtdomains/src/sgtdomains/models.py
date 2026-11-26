"""Core records passed between pipeline stages. Everything is JSON-lines serialisable."""
from __future__ import annotations

import ipaddress
from dataclasses import asdict, dataclass

# kinds of learned subnet. 'mgmt' (device management /32 etc.) is infrastructure, not a client subnet.
CLIENT_KINDS = ("svi", "vlan", "routed", "subnet")


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    mgmt_ip: str = ""
    kind: str = "other"  # switch | ap | router | wlc | firewall | other
    site: str = ""
    source: str = ""
    model: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class SubnetRecord:
    prefix: str  # normalised CIDR
    source: str
    device_id: str = ""
    device_name: str = ""
    site: str = ""
    kind: str = "subnet"  # svi | vlan | routed | mgmt | subnet
    vlan: str = ""
    label: str = ""  # vlan/interface name, useful for SGT rules

    def to_dict(self):
        return asdict(self)


def normalize_prefix(value: str) -> str:
    return str(ipaddress.ip_network(value.strip(), strict=False))


def network_from_ip_mask(ip: str, mask: str) -> str:
    """mask may be dotted ('255.255.255.0'), '24' or '/24'."""
    mask = str(mask).strip().lstrip("/")
    return str(ipaddress.ip_interface(f"{ip.strip()}/{mask}").network)
