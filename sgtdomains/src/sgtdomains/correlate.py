"""Correlate learned subnets (controllers) with observed flows.

  active        learned and carrying >= min_flows              -> safe to put in a domain filter
  idle          learned, no/low traffic                        -> stale, or sensor can't see it; review
  observed_gap  internal traffic matching no learned subnet    -> inventory gap (unmanaged VLAN, other controller, DC)
"""
from __future__ import annotations

import ipaddress
from collections import defaultdict
from dataclasses import dataclass

from .flows import FlowAggregator
from .prefixes import PrefixIndex

DEFAULT_INTERNAL = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


@dataclass
class Stat:
    prefix: str
    flows: int = 0
    bytes: int = 0
    hosts: int = 0
    status: str = "idle"

    def to_dict(self):
        return self.__dict__.copy()


def correlate(learned, agg: FlowAggregator, internal_ranges=DEFAULT_INTERNAL, gap_prefixlen=24, min_flows=1):
    learned = sorted(set(learned))
    idx = PrefixIndex(learned)
    stats = {p: Stat(p) for p in learned}
    internal = [ipaddress.ip_network(n) for n in internal_ranges]
    gaps = defaultdict(lambda: Stat(""))
    for ip, flows in agg.ip_flows.items():
        nbytes = agg.ip_bytes[ip]
        hit = idx.lookup(ip)
        if hit:
            s = stats[hit]
        else:
            try:
                a = ipaddress.ip_address(ip)
            except ValueError:
                continue
            if not any(a in n for n in internal if n.version == a.version):
                continue
            s = gaps[str(ipaddress.ip_network(f"{ip}/{gap_prefixlen}", strict=False))]
        s.flows += flows
        s.bytes += nbytes
        s.hosts += 1
    for s in stats.values():
        s.status = "active" if s.flows >= min_flows else "idle"
    gap_list = []
    for p, s in sorted(gaps.items(), key=lambda kv: -kv[1].flows):
        s.prefix, s.status = p, "observed_gap"
        gap_list.append(s)
    return list(stats.values()), gap_list
