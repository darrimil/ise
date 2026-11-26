from __future__ import annotations

import csv
import json
import os
from concurrent.futures import ThreadPoolExecutor

import click
import yaml

from . import correlate as corr, domains, ise_push, sources
from .flows import FlowAggregator


def _wd(cfg):
    p = cfg.get("workdir", "./work")
    os.makedirs(p, exist_ok=True)
    return p


def _load(path):
    with open(path) as fh:
        return yaml.safe_load(fh)


def _jsonl_read(path):
    with open(path) as fh:
        return [json.loads(line) for line in fh]


def _jsonl_write(path, rows):
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _csv_write(path, rows, fields):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


@click.group()
@click.option("-c", "--config", "config", default="config.yaml", show_default=True)
@click.pass_context
def main(ctx, config):
    """Learn subnets -> correlate with flows -> plan -> push SGT domain filters to ISE."""
    ctx.obj = _load(config)


@main.command()
@click.option("--only", multiple=True, help="source name(s) to run")
@click.pass_obj
def collect(cfg, only):
    """Pull devices + subnets from every configured source."""
    srcs = [sources.build(s) for s in cfg["sources"]]
    srcs = [s for s in srcs if not only or s.name in only]
    with ThreadPoolExecutor(len(srcs) or 1) as ex:
        results = list(ex.map(lambda s: s.collect(), srcs))
    devs, subs = [], []
    for s, (d, n) in zip(srcs, results):
        click.echo(f"{s.name}: {len(d)} devices, {len(n)} subnet records")
        devs += [x.to_dict() for x in d]
        subs += [x.to_dict() for x in n]
    wd = _wd(cfg)
    _jsonl_write(f"{wd}/devices.jsonl", devs)
    _jsonl_write(f"{wd}/subnets.jsonl", subs)
    click.echo(f"{len({r['prefix'] for r in subs})} unique prefixes -> {wd}/subnets.jsonl")


@main.command("correlate")
@click.argument("flow_files", nargs=-1, required=True, type=click.Path(exists=True))
@click.pass_obj
def correlate_cmd(cfg, flow_files):
    """Match learned subnets to flow CSVs; report active / idle / observed gaps."""
    wd, fc = _wd(cfg), cfg.get("flows", {})
    agg = FlowAggregator()
    for f in flow_files:
        agg.read_csv(f, fc.get("columns"))
    learned = {r["prefix"] for r in _jsonl_read(f"{wd}/subnets.jsonl")}
    stats, gaps = corr.correlate(learned, agg, fc.get("internal_ranges", corr.DEFAULT_INTERNAL),
                                 fc.get("gap_prefixlen", 24), fc.get("min_flows", 1))
    fields = ["prefix", "status", "flows", "bytes", "hosts"]
    _csv_write(f"{wd}/correlation.csv", [s.to_dict() for s in stats], fields)
    _csv_write(f"{wd}/observed_gaps.csv", [s.to_dict() for s in gaps], fields)
    _jsonl_write(f"{wd}/correlation.jsonl", [s.to_dict() for s in stats])
    act = sum(s.status == "active" for s in stats)
    click.echo(f"{agg.records} flow records, {len(agg.ip_flows)} endpoints | learned {len(stats)}: "
               f"{act} active, {len(stats) - act} idle | {len(gaps)} observed gaps -> {wd}/")


@main.command()
@click.pass_obj
def plan(cfg):
    """Build domain filters (domains.json / domain_filters.csv / unassigned.csv)."""
    wd = _wd(cfg)
    recs = _jsonl_read(f"{wd}/subnets.jsonl")
    cpath = f"{wd}/correlation.jsonl"
    status = {r["prefix"]: r["status"] for r in _jsonl_read(cpath)} if os.path.exists(cpath) else {}
    if not status:
        click.echo("no correlation data: including every learned client subnet")
    filters, unassigned = domains.plan(recs, status, cfg.get("domains", {}))
    with open(f"{wd}/domains.json", "w") as fh:
        json.dump([f.to_dict() for f in filters], fh, indent=2)
    _csv_write(f"{wd}/domain_filters.csv",
               [{"domain": f.domain, "sgt": f.sgt, "subnet": s} for f in filters for s in f.subnets],
               ["domain", "sgt", "subnet"])
    _csv_write(f"{wd}/unassigned.csv", unassigned, ["prefix", "site", "label", "vlan", "source", "kind", "devices"])
    click.echo(f"{len(filters)} domain filters, {sum(len(f.subnets) for f in filters)} subnets; "
               f"{len(unassigned)} subnets with no SGT rule -> {wd}/unassigned.csv")


@main.command()
@click.option("--live", is_flag=True, help="actually write to ISE (default is dry-run)")
@click.option("--prune", is_flag=True, help="delete filters in ISE that are not in the plan")
@click.pass_obj
def push(cfg, live, prune):
    """Diff plan against ISE and create missing SXP domains/filters."""
    from .sources.ise import ise_client
    wd = _wd(cfg)
    filters = [domains.DomainFilter(**d) for d in json.load(open(f"{wd}/domains.json"))]
    result = ise_push.apply(ise_client(cfg["ise"]), filters, live=live, prune=prune)
    click.echo(("APPLIED" if live else "DRY-RUN") + ": " + ", ".join(f"{k}={len(v)}" for k, v in result.items()))
    with open(f"{wd}/push_{'live' if live else 'dryrun'}.json", "w") as fh:
        json.dump(result, fh, indent=2, default=list)


if __name__ == "__main__":
    main()
