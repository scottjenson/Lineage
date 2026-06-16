#!/usr/bin/env python3
"""Shared API-key resolution for Context Trace.

One resolver used by every script, so keys work in all three environments:
  1. interactive terminal (env vars present),
  2. plain scripts (env vars may be absent),
  3. the macOS Quick Action (neither shell config nor exported env is reliable).

Resolution order for a key:
  1. environment variable of the same name,
  2. config.json sitting next to this file: { "GEMINI_API_KEY": "...", ... }.

config.json is gitignored — it holds secrets and must never be committed.
"""

import json
import os
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def _load_config():
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def get_key(name):
    """Return the API key `name`, or None if it can't be found anywhere."""
    val = os.environ.get(name)
    if val:
        return val.strip()
    return _load_config().get(name)


def require_key(name):
    """Like get_key, but exits with a clear message if the key is missing."""
    key = get_key(name)
    if not key:
        raise SystemExit(
            f"Missing {name}. Set it as an environment variable, or add it to "
            f"{CONFIG_PATH.name} (next to the scripts) as {{\"{name}\": \"...\"}}."
        )
    return key
