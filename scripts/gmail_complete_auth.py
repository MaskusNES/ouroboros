#!/usr/bin/env python3
"""
Complete Gmail OAuth auth flow and register push watch.

Usage:
  python3 scripts/gmail_complete_auth.py "CODE_HERE"

Run scripts/gmail_watch_setup.py first (no args) to generate the auth URL
and save state. Then paste the code from the browser here.

Exchanges the code for a token saved to gmail_watch_token.json,
creates a Pub/Sub pull subscription, and registers gmail.users.watch().
"""

import importlib.util
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/gmail_complete_auth.py \"CODE_HERE\"")
        print("\nFirst run: python3 scripts/gmail_watch_setup.py  (to get the auth URL)")
        sys.exit(1)

    code = sys.argv[1].strip()

    spec = importlib.util.spec_from_file_location(
        "gmail_watch_setup",
        REPO_DIR / "scripts" / "gmail_watch_setup.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.cmd_complete_auth(code)
