"""Turn learned+correlated subnets into SGT/SXP domain filters.

One filter = (domain, sgt, [summarised subnets]).
  domain : grouping key (site / source / vlan label / single)       -> domains.group_by
  sgt    : first matching rule in domains.sgt_rules, else default_sgt  (unmatched subnets are reported, not guessed)
The same prefix is often reported by many devices (SVI on every stack member); we dedupe by prefix and take the
majority site/label.
"""
from __future__ import annotations

import ipaddress
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .models import CLIENT_KINDS
from .prefixes import summarize


@dataclass
class DomainFilter:
    domain: str
    sgt: str
    subnets: list = field(default_factory=list)

    def to_dict(self):
        return self.__dict__.copy()


def _clean(s):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", s).strip("_") or "DEFAULT"


def _rule_matches(rule, rec):
    m = rule.get("match", {})
    if "subnet" in m and not ipaddress.ip_network(rec["prefix"]).subnet_of(ipaddress.ip_network(m["subnet"], strict=False)):
        return False
    if "site" in m and not re.search(m["site"], rec["site"] or ""):
        return False
    if "label" in m and not re.search(m["label"], rec["label"] or ""):
        return False
    if "vlan" in m and str(m["vlan"]) != str(rec["vlan"]):
        return False
    return True


def merge_records(records):
    """Collapse per-device records to one entry per prefix."""
    by = defaultdict(list)
    for r in records:
        by[r["prefix"]].append(r)
    out = {}
    for p, rs in by.items():
        top = lambda k: Counter(r[k] for r in rs if r.get(k)).most_common(1)  # noqa: E731
        pick = lambda k: top(k)[0][0] if top(k) else ""  # noqa: E731
        out[p] = {"prefix": p, "site": pick("site"), "label": pick("label"), "vlan": pick("vlan"),
                  "source": pick("source"), "kind": pick("kind"), "devices": len({r["device_id"] for r in rs if r.get("device_id")})}
    return out


def plan(records, status_by_prefix, cfg):
    kinds = set(cfg.get("include_kinds", CLIENT_KINDS))
    statuses = set(cfg.get("include_status", ["active"])) if status_by_prefix else {"idle", "active"}
    group_by = cfg.get("group_by", "site")
    default_domain, default_sgt = cfg.get("default_domain", "DEFAULT"), cfg.get("default_sgt")
    rules = cfg.get("sgt_rules", [])
    filters, unassigned = defaultdict(list), []
    for p, r in merge_records(records).items():
        if r["kind"] not in kinds:
            continue
        if status_by_prefix and status_by_prefix.get(p, "idle") not in statuses:
            continue
        sgt = next((x["sgt"] for x in rules if _rule_matches(x, r)), default_sgt)
        if not sgt:
            unassigned.append(r)
            continue
        key = {"site": r["site"], "source": r["source"], "label": r["label"], "single": ""}[group_by]
        filters[(_clean(key) if key else default_domain, sgt)].append(p)
    result = [DomainFilter(d, s, summarize(ps)) for (d, s), ps in sorted(filters.items())]
    return result, unassigned
