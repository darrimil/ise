import json
import os

from click.testing import CliRunner

from sgtdomains import correlate, domains, ise_push
from sgtdomains.cli import main
from sgtdomains.flows import FlowAggregator
from sgtdomains.models import network_from_ip_mask
from sgtdomains.prefixes import PrefixIndex, summarize
from sgtdomains.sources.catalyst_center import CatalystCenterSource, device_kind
from sgtdomains.sources.csv_source import CsvSource
from sgtdomains.sources.ise import IseSource

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


def test_lpm_prefers_most_specific():
    idx = PrefixIndex(["10.0.0.0/8", "10.1.0.0/16", "10.1.2.0/24"])
    assert idx.lookup("10.1.2.9") == "10.1.2.0/24"
    assert idx.lookup("10.1.9.9") == "10.1.0.0/16"
    assert idx.lookup("10.9.9.9") == "10.0.0.0/8"
    assert idx.lookup("11.0.0.1") is None and idx.lookup("garbage") is None


def test_masks_and_summarize():
    assert network_from_ip_mask("10.1.1.1", "255.255.255.0") == "10.1.1.0/24"
    assert network_from_ip_mask("10.1.1.1", "/25") == "10.1.1.0/25"
    assert summarize(["10.0.0.0/24", "10.0.1.0/24", "10.0.0.5/32"]) == ["10.0.0.0/23"]


def test_csv_source_and_pipeline():
    devs, subs = CsvSource({"path": f"{EX}/subnets.csv"}).collect()
    assert len(subs) == 7 and {d.name for d in devs} == {"sw-hq-1", "sw-hq-2", "sw-br1-1"}
    agg = FlowAggregator()
    agg.read_csv(f"{EX}/flows.csv")
    stats, gaps = correlate.correlate({s.prefix for s in subs}, agg)
    by = {s.prefix: s for s in stats}
    assert by["10.10.10.0/24"].status == "active" and by["10.20.40.0/24"].status == "idle"
    assert [g.prefix for g in gaps] == ["10.55.1.0/24"]  # public IPs (8.8.8.8) are not gaps
    status = {s.prefix: s.status for s in stats}
    cfg = {"sgt_rules": [{"match": {"label": "(?i)corp"}, "sgt": "Employees"}, {"match": {"label": "(?i)guest"}, "sgt": "Guests"}]}
    filters, unassigned = domains.plan([s.to_dict() for s in subs], status, cfg)
    got = {(f.domain, f.sgt): f.subnets for f in filters}
    assert got == {("HQ", "Employees"): ["10.10.10.0/24"], ("Branch_1", "Employees"): ["10.20.10.0/24"],
                   ("Branch_1", "Guests"): ["10.20.30.0/24"]}
    assert [u["prefix"] for u in unassigned] == ["10.10.20.0/24"]  # voice active but no rule -> reported


class FakeCC:
    """Minimal Catalyst Center stand-in."""
    def __init__(self):
        self.data = {
            "/dna/intent/api/v1/network-device": [{"id": "d1", "hostname": "sw1", "family": "Switches and Hubs", "managementIpAddress": "10.0.0.1"}],
            "/dna/intent/api/v1/interface": [
                {"deviceId": "d1", "portName": "Vlan10", "ipv4Address": "10.1.10.1", "ipv4Mask": "255.255.255.0", "vlanId": "10"},
                {"deviceId": "d1", "portName": "Loopback0", "ipv4Address": "1.1.1.1", "ipv4Mask": "255.255.255.255"},
                {"deviceId": "d1", "portName": "Gi1/0/1", "ipv4Address": None}]}

    def request(self, method, path, params=None, **kw):
        base = path.removesuffix("/count")
        rows = self.data.get(base)
        body = {"response": len(rows)} if path.endswith("/count") else {"response": rows}
        return type("R", (), {"status_code": 200 if rows is not None else 404, "json": lambda s: body,
                              "raise_for_status": lambda s: None})()


def test_catalyst_center_parsing():
    src = CatalystCenterSource({"site_membership": False}, client=FakeCC())
    devs, subs = src.collect()
    assert devs[0].kind == "switch" and device_kind("Unified AP") == "ap"
    assert [(s.prefix, s.kind, s.vlan) for s in subs] == [("10.1.10.0/24", "svi", "10")]


class FakeIse:
    def get_json(self, path, params=None):
        if path.endswith("networkdevice"):
            return {"SearchResult": {"resources": [{"id": "1", "name": "sw"}]}}
        return {"NetworkDevice": {"id": "1", "name": "sw", "NetworkDeviceIPList": [{"ipaddress": "10.9.0.5", "mask": 32}, {"ipaddress": "10.9.1.1", "mask": 24}],
                                  "NetworkDeviceGroupList": ["Location#All Locations#HQ", "Device Type#All Device Types#Switch"]}}


def test_ise_source():
    devs, subs = IseSource({}, client=FakeIse()).collect()
    assert devs[0].site == "HQ" and devs[0].kind == "switch"
    assert {(s.prefix, s.kind) for s in subs} == {("10.9.0.5/32", "mgmt"), ("10.9.1.0/24", "subnet")}


def test_push_diff():
    f = [domains.DomainFilter("HQ", "Employees", ["10.1.0.0/24", "10.2.0.0/24"])]
    d = ise_push.diff(f, {"HQ": "x"}, {("HQ", "Employees", "10.1.0.0/24"): "i", ("HQ", "Old", "10.9.0.0/24"): "j"})
    assert d["create_domains"] == [] and d["create_filters"] == [("HQ", "Employees", "10.2.0.0/24")]
    assert d["stale_filters"] == [("HQ", "Old", "10.9.0.0/24")]


def test_cli_end_to_end(tmp_path):
    cfg = open(f"{EX}/config.yaml").read().replace("./work", str(tmp_path)).replace("examples/subnets.csv", f"{EX}/subnets.csv")
    (tmp_path / "c.yaml").write_text(cfg)
    r = CliRunner()
    base = ["-c", str(tmp_path / "c.yaml")]
    for args in (["collect"], ["correlate", f"{EX}/flows.csv"], ["plan"]):
        res = r.invoke(main, base + args)
        assert res.exit_code == 0, res.output
    assert json.load(open(tmp_path / "domains.json"))
