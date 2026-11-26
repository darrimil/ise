from __future__ import annotations

import os


class Source:
    """collect() -> (devices, subnets). Sources never raise on a single bad record; they skip and count."""

    name = "source"

    def collect(self):
        raise NotImplementedError


def secret(cfg: dict, key: str) -> str:
    """Credentials come from env vars (`<key>_env`) so config files can be committed."""
    if cfg.get(f"{key}_env"):
        val = os.environ.get(cfg[f"{key}_env"])
        if val is None:
            raise SystemExit(f"environment variable {cfg[f'{key}_env']} is not set")
        return val
    return cfg.get(key, "")


def build(cfg: dict) -> Source:
    from .catalyst_center import CatalystCenterSource
    from .csv_source import CsvSource
    from .ise import IseSource
    from .meraki import MerakiSource

    kinds = {"csv": CsvSource, "catalyst_center": CatalystCenterSource, "meraki": MerakiSource, "ise": IseSource}
    try:
        return kinds[cfg["type"]](cfg)
    except KeyError:
        raise SystemExit(f"unknown/missing source type in {cfg!r}; expected one of {sorted(kinds)}")
