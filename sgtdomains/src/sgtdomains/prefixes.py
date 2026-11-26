"""Longest-prefix-match index. One dict per prefix length: lookup cost is O(#distinct lengths), not O(#prefixes),
which keeps 100k+ prefixes x millions of flow endpoints cheap in pure Python."""
from __future__ import annotations

import ipaddress
from typing import Iterable, Optional


class PrefixIndex:
    def __init__(self, prefixes: Iterable[str]):
        self._tables = {4: {}, 6: {}}
        for p in prefixes:
            n = ipaddress.ip_network(p, strict=False)
            self._tables[n.version].setdefault(n.prefixlen, {})[int(n.network_address)] = str(n)
        self._order = {v: sorted(t, reverse=True) for v, t in self._tables.items()}

    def lookup(self, ip: str) -> Optional[str]:
        try:
            a = ipaddress.ip_address(ip.strip())
        except ValueError:
            return None
        bits, ai = a.max_prefixlen, int(a)
        table = self._tables[a.version]
        for plen in self._order[a.version]:
            shift = bits - plen
            hit = table[plen].get((ai >> shift) << shift)
            if hit:
                return hit
        return None


def summarize(prefixes: Iterable[str]) -> list:
    """Collapse adjacent/contained prefixes (keeps ISE filter lists short)."""
    nets = [ipaddress.ip_network(p, strict=False) for p in prefixes]
    out = []
    for version in (4, 6):
        group = [n for n in nets if n.version == version]
        out += [str(n) for n in ipaddress.collapse_addresses(group)]
    return out
