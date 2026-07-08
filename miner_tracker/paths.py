"""Filesystem anchors. Stateful files (config.yaml, data/, secrets.yaml) live under
home_dir(), which defaults to the project root. Set MINER_TRACKER_HOME to relocate.
"""
from __future__ import annotations

import os
from pathlib import Path


def home_dir() -> Path:
    env = os.environ.get("MINER_TRACKER_HOME")
    if env:
        return Path(env).expanduser()
    # miner_tracker/miner_tracker/paths.py -> project root
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return home_dir() / "config.yaml"


def data_dir() -> Path:
    return home_dir() / "data"


def db_path() -> Path:
    return data_dir() / "miner_tracker.db"


def secrets_path() -> Path:
    return home_dir() / "secrets.yaml"
