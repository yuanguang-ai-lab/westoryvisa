"""V2-specific settings that keep its durable state isolated from V1."""

import os
from dataclasses import replace
from pathlib import Path

from visa_agent.config import load_config


def load_v2_config(environ=None):
    base = load_config(environ)
    environment = os.environ if environ is None else environ
    configured = str(
        environment.get("AGENT_V2_DATA_DIR") or ""
    ).strip()
    data_dir = (
        Path(configured)
        if configured
        else Path(str(base.data_dir) + "-v2")
    )
    return replace(base, data_dir=data_dir)
