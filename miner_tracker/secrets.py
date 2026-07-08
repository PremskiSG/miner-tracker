"""Tiny secrets loader: env var wins, else git-ignored secrets.yaml at home_dir().

secrets.yaml shape:

    anthropic:
      api_key: "sk-ant-..."
"""
from __future__ import annotations

import logging
import os

import yaml

from miner_tracker.paths import secrets_path

logger = logging.getLogger("miner_tracker.secrets")


def get_secret(section: str, key: str, env_var: str | None = None) -> str | None:
    if env_var:
        val = os.environ.get(env_var)
        if val:
            return val
    path = secrets_path()
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as e:
        logger.warning("could not read %s: %s", path, e)
        return None
    return (data.get(section) or {}).get(key) or None


def anthropic_api_key() -> str | None:
    return get_secret("anthropic", "api_key", env_var="ANTHROPIC_API_KEY")
