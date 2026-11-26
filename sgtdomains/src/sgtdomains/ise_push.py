"""Push domains + filters to ISE.

!! The SXP domain / domain-filter ERS resource names and payload shapes below are best-effort and NOT yet verified
!! against a live ISE. Check them with the ISE ERS SDK (https://<ise>:9060/ers/sdk -> SXP*), then adjust the
!! RESOURCES / payload builders. Default is DRY-RUN: nothing is written without --live.

Idempotent: reads existing objects, creates only what is missing, deletes stale ones only with prune=True.
"""
from __future__ import annotations

from .sources.ise import list_resources

RESOURCES = {"domain": "/ers/config/sxpvpns", "filter": "/ers/config/sxpdomainfilter"}


def domain_payload(name):
    return {"SXPVPN": {"sxpVpnName": name}}


def filter_payload(domain, sgt, subnet):
    return {"SxpDomainFilter": {"sxpDomain": domain, "sgt": sgt, "subnet": subnet}}


def desired(filters):
    domains = sorted({f.domain for f in filters})
    entries = sorted({(f.domain, f.sgt, s) for f in filters for s in f.subnets})
    return domains, entries


def existing(client):
    doms = {r["name"]: r["id"] for r in list_resources(client, RESOURCES["domain"])}
    ents = {}
    for r in list_resources(client, RESOURCES["filter"]):
        d = client.get_json(f"{RESOURCES['filter']}/{r['id']}")
        body = next(iter(d.values()))
        ents[(body.get("sxpDomain"), body.get("sgt"), body.get("subnet"))] = r["id"]
    return doms, ents


def diff(filters, have_domains, have_entries):
    want_d, want_e = desired(filters)
    return {"create_domains": [d for d in want_d if d not in have_domains],
            "create_filters": [e for e in want_e if e not in have_entries],
            "stale_filters": [e for e in have_entries if e not in set(want_e)]}


def apply(client, filters, live=False, prune=False):
    have_d, have_e = existing(client)
    d = diff(filters, have_d, have_e)
    if not live:
        return d
    for name in d["create_domains"]:
        client.request("POST", RESOURCES["domain"], json=domain_payload(name)).raise_for_status()
    for dom, sgt, sub in d["create_filters"]:
        client.request("POST", RESOURCES["filter"], json=filter_payload(dom, sgt, sub)).raise_for_status()
    if prune:
        for e in d["stale_filters"]:
            client.request("DELETE", f"{RESOURCES['filter']}/{have_e[e]}").raise_for_status()
    return d
