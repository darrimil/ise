"""Flow ingestion. Streams CSV (Secure Network Analytics export, nfdump -o csv, or any collector) and keeps only
per-endpoint-IP totals, so memory scales with *active hosts*, not flow records.
For multi-billion-flow estates pre-aggregate in the collector (SNA host/subnet report, nfdump -s ip/flows) and feed that."""
from __future__ import annotations

import csv
from collections import Counter

ALIASES = {
    "src": ("src", "source", "src_ip", "source_ip", "srcaddr", "sa", "subject_ip_address", "source ip", "src ip"),
    "dst": ("dst", "destination", "dst_ip", "dest_ip", "destination_ip", "dstaddr", "da", "peer_ip_address",
            "destination ip", "dst ip"),
    "bytes": ("bytes", "byt", "total_bytes", "subject_bytes", "octets"),
    "flows": ("flows", "fl", "flow_count", "count"),
}


class FlowAggregator:
    def __init__(self):
        self.ip_flows = Counter()
        self.ip_bytes = Counter()
        self.records = 0

    def add(self, ip, flows=1, nbytes=0):
        self.ip_flows[ip] += flows
        self.ip_bytes[ip] += nbytes

    def read_csv(self, path, columns=None):
        cols = columns or {}
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rd = csv.reader(fh)
            header = [h.strip().lower() for h in next(rd)]

            def find(field):
                want = [cols[field].lower()] if cols.get(field) else ALIASES[field]
                return next((header.index(a) for a in want if a in header), None)

            src, dst, byt, fl = find("src"), find("dst"), find("bytes"), find("flows")
            if src is None and dst is None:
                raise SystemExit(f"{path}: no source/destination IP column found in {header}; set flows.columns")
            for row in rd:
                try:
                    f = int(float(row[fl])) if fl is not None and row[fl] else 1
                    b = int(float(row[byt])) if byt is not None and row[byt] else 0
                except (ValueError, IndexError):
                    continue
                self.records += 1
                for idx in (src, dst):
                    if idx is not None and idx < len(row) and row[idx]:
                        self.add(row[idx].strip(), f, b)
