#!/usr/bin/env python3
"""
Runner for Gmail Push Polling Server — used by systemd.
Loads .env then starts the Pub/Sub pull loop.
"""

import os
import sys
from pathlib import Path

REPO_DIR = Path("/opt/ouroboros_repo")
sys.path.insert(0, str(REPO_DIR))

# Load .env
env_file = REPO_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v

from ouroboros.tools.gmail_push_server import main

if __name__ == "__main__":
    main()
