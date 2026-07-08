from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from miner_tracker.paths import config_path, home_dir


@lru_cache(maxsize=1)
def load_config() -> dict:
    return yaml.safe_load(config_path().read_text(encoding="utf-8")) or {}


def filings_dir() -> Path:
    raw = load_config().get("filings_dir", "")
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = home_dir() / p
    return p


def companies() -> list[dict]:
    return load_config().get("companies", [])


def extraction_settings() -> dict:
    return load_config().get("extraction", {})
