# sgtdomains

Learn which subnets your campus/branch switches and APs actually serve, prove them against observed flows, and turn
the result into ISE SXP/SGT domains and domain filters.

```
 Catalyst Center ─┐                         ┌─ active / idle subnets ─┐
 Meraki Dashboard ┼─ collect ─► subnets ─┬─►│ correlate               ├─► plan ─► push (dry-run) ─► ISE
 ISE NetworkDevice┤  (parallel, paged)   │  └─ observed_gaps.csv ─────┘   sgt_rules   diff + create
 CSV              ┘                      └── flow CSV (SNA / nfdump / any collector)
```

## Quick start (no devices needed)
```bash
pip install -e '.[dev]' && pytest
sgtdomains -c examples/config.yaml collect
sgtdomains -c examples/config.yaml correlate examples/flows.csv
sgtdomains -c examples/config.yaml plan          # -> work/domains.json, domain_filters.csv, unassigned.csv
sgtdomains -c examples/config.yaml push          # dry-run diff against ISE; add --live to write
```

## Stages and outputs (`workdir`)
| command | output | meaning |
|---|---|---|
| `collect` | `devices.jsonl`, `subnets.jsonl` | one record per device-interface/VLAN: prefix, site, kind, vlan, label, source |
| `correlate` | `correlation.csv`, `observed_gaps.csv` | per learned subnet: flows/bytes/hosts, **active** or **idle**; internal traffic with no matching subnet grouped to /24 = **gaps** |
| `plan` | `domains.json`, `domain_filters.csv`, `unassigned.csv` | (domain, SGT, summarised subnets); subnets lacking an SGT rule are listed, never guessed |
| `push` | `push_dryrun.json` | create_domains / create_filters / stale_filters |

## Sources
- **Catalyst Center**: `/network-device` + `/interface` (SVIs and routed ports with ip+mask), site via `/membership`. Pages fetched in parallel.
- **Meraki**: device status (mgmt IPs), MX VLAN subnets; optional per-switch L3 interfaces (`switch_l3`, one call/switch — mind the ~10 rps/org limit).
- **ISE**: ERS `networkdevice` + per-device detail (threaded; ERS has no bulk read). /32 entries are `mgmt`, wider ones are subnets. Site/type from NDGs.
- **CSV**: `subnet` or `ip`+`mask`, plus `hostname, site, kind, vlan, label`.

Management /32s and loopbacks are tagged `mgmt` and excluded from filters (`domains.include_kinds`).
Credentials are read from env vars named in the config (`password_env`, `api_key_env`).

## Scale (20–30k devices)
- Collection is network-bound; API paging is parallel with 429/5xx back-off. Expect minutes, not hours; CC interface table is the largest pull.
- Flow ingest streams and keeps one counter per *active endpoint IP*; longest-prefix match is O(#distinct prefix lengths).
  Measured here: 1.9M endpoints × 135k prefixes ≈ 8 s. For billions of flow records pre-aggregate in the collector (e.g. SNA host/subnet export).
- ISE push is diff-based and idempotent, so reruns only send changes.

## Known gaps / next steps
1. **ISE push payloads are unverified.** The SXP domain / domain-filter ERS resources in `ise_push.py` are best-effort — confirm
   against the lab ISE's ERS SDK (`https://<ise>:9060/ers/sdk`) before `--live`. Dry-run is the default. Also confirm that "SGT domain filter" maps to ISE *SXP Domain Filters* (Work Centers → TrustSec → SXP).
2. Catalyst Center and Meraki parsers are tested against stubs only; field names need a real-tenant check.
3. Parent/child prefixes: flows are attributed to the most specific prefix, so a learned supernet can read *idle* while its children are active.
4. IPv6 is handled by the prefix code but not yet exercised by sources.
5. Direct SNA API (instead of CSV export), scheduled runs/history (snapshot diffs → "subnet appeared/disappeared"), and an SGT-assignment suggester from observed peers.
