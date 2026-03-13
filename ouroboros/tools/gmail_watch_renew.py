"""
Gmail Watch Renew tool — checks watch expiration and renews if needed.
Called daily by background consciousness.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

DATA_DIR = Path(os.environ.get("OUROBOROS_DATA", "/opt/ouroboros_data"))
TOKEN_FILE = DATA_DIR / "gmail_watch_token.json"
WATCH_STATE_FILE = DATA_DIR / "gmail_watch_state.json"
PROJECT_ID = "my-project-35724"
TOPIC_FULL = f"projects/{PROJECT_ID}/topics/gmail-push-notifications"

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from googleapiclient.discovery import build

    if not TOKEN_FILE.exists():
        raise RuntimeError(f"Watch token not found: {TOKEN_FILE}")

    token_data = json.loads(TOKEN_FILE.read_text())
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id"),
        client_secret=token_data.get("client_secret"),
        scopes=token_data.get("scopes", GMAIL_SCOPES),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        token_data["token"] = creds.token
        if creds.expiry:
            token_data["expiry"] = creds.expiry.isoformat()
        TOKEN_FILE.write_text(json.dumps(token_data, indent=2))
    return build("gmail", "v1", credentials=creds)


def _check_and_renew(force: bool = False) -> dict[str, Any]:
    """Check expiration and renew if within 24h or forced."""
    if not WATCH_STATE_FILE.exists():
        return {"status": "no_state", "message": "Watch not set up yet. Run scripts/gmail_watch_setup.py first."}

    state = json.loads(WATCH_STATE_FILE.read_text())
    expiration_ms = state.get("expiration_ms", 0)

    if expiration_ms:
        expiry_dt = datetime.fromtimestamp(expiration_ms / 1000, tz=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        hours_left = (expiry_dt - now).total_seconds() / 3600

        if not force and hours_left > 24:
            return {
                "status": "ok",
                "hours_left": round(hours_left, 1),
                "expiry": expiry_dt.isoformat(),
                "message": f"Watch valid for {hours_left:.1f}h, renewal not needed.",
            }

    # Renew
    log.info("Renewing Gmail watch...")
    service = _get_gmail_service()
    result = service.users().watch(
        userId="me",
        body={
            "topicName": TOPIC_FULL,
            "labelIds": ["INBOX"],
        }
    ).execute()

    history_id = result.get("historyId", "")
    expiration = int(result.get("expiration", 0))
    expiry_iso = datetime.fromtimestamp(expiration / 1000, tz=timezone.utc).isoformat() if expiration else ""

    state.update({
        "last_history_id": history_id,
        "expiration_ms": expiration,
        "expiration_iso": expiry_iso,
    })
    WATCH_STATE_FILE.write_text(json.dumps(state, indent=2))

    return {
        "status": "renewed",
        "expiry": expiry_iso,
        "historyId": history_id,
        "message": f"Watch renewed. Expires: {expiry_iso}",
    }


def get_tools():
    return [
        {
            "name": "gmail_watch_renew",
            "description": "Check Gmail push watch expiration and renew if expiring within 24h. Run daily.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Force renewal even if not expiring soon",
                    }
                },
                "required": [],
            },
            "handler": lambda args, ctx: _check_and_renew(force=args.get("force", False)),
        }
    ]
