"""
Gmail Push Notification Polling Server — pulls from Pub/Sub subscription.

Uses Pub/Sub pull (not push) to avoid HTTPS certificate requirements.
Polls every 30 seconds, decodes Gmail history notifications, fetches
email metadata via Gmail API, and sends Telegram notifications.

Run via systemd (gmail-push.service).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_DIR = Path("/opt/ouroboros_repo")
sys.path.insert(0, str(REPO_DIR))

import requests as _requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("gmail_push")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("OUROBOROS_DATA", "/opt/ouroboros_data"))
TOKEN_FILE = DATA_DIR / "gmail_watch_token.json"
WATCH_STATE_FILE = DATA_DIR / "gmail_watch_state.json"
STATUS_FILE = DATA_DIR / "gmail_push_status.json"
ENV_FILE = REPO_DIR / ".env"

PROJECT_ID = "my-project-35724"
SUBSCRIPTION_PATH = f"projects/{PROJECT_ID}/subscriptions/gmail-push-sub"
POLL_INTERVAL = 30  # seconds

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_env() -> dict:
    env = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def _get_telegram_config() -> tuple[str, int]:
    env = _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or env.get("TELEGRAM_BOT_TOKEN", "")
    owner_id_str = os.environ.get("OWNER_ID", "")
    if not owner_id_str:
        try:
            state_path = Path(os.environ.get("DRIVE_ROOT", "/var/ouroboros/data")) / "state" / "state.json"
            state = json.loads(state_path.read_text())
            owner_id_str = str(state.get("owner_id", ""))
        except Exception:
            pass
    owner_id = int(owner_id_str) if owner_id_str.isdigit() else 0
    return token, owner_id


def _send_telegram(text: str) -> bool:
    token, owner_id = _get_telegram_config()
    if not token or not owner_id:
        log.error("Telegram not configured (token=%s, owner_id=%s)", bool(token), owner_id)
        return False
    try:
        r = _requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": owner_id, "text": text, "disable_web_page_preview": True},
            timeout=15,
        )
        data = r.json()
        if data.get("ok"):
            return True
        log.error("Telegram send failed: %s", data)
        return False
    except Exception as e:
        log.error("Telegram send exception: %s", e)
        return False


def _get_creds() -> Credentials:
    """Load and refresh OAuth credentials from TOKEN_FILE."""
    if not TOKEN_FILE.exists():
        raise RuntimeError(f"Gmail watch token not found: {TOKEN_FILE}")
    creds_data = json.loads(TOKEN_FILE.read_text())
    creds = Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
        scopes=creds_data.get("scopes", GMAIL_SCOPES),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
        token_data = {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": list(creds.scopes) if creds.scopes else GMAIL_SCOPES,
            "universe_domain": "googleapis.com",
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
        TOKEN_FILE.write_text(json.dumps(token_data, indent=2))
    return creds


def _get_last_history_id() -> str | None:
    try:
        if WATCH_STATE_FILE.exists():
            state = json.loads(WATCH_STATE_FILE.read_text())
            return state.get("last_history_id")
    except Exception:
        pass
    return None


def _save_history_id(history_id: str) -> None:
    try:
        state = {}
        if WATCH_STATE_FILE.exists():
            state = json.loads(WATCH_STATE_FILE.read_text())
        state["last_history_id"] = history_id
        WATCH_STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.error("Failed to save history_id: %s", e)


def _fetch_new_messages(service, history_id: str) -> list[dict]:
    """Fetch new inbox messages since last historyId. Returns list of {from, subject, date}."""
    last_id = _get_last_history_id()

    if not last_id:
        _save_history_id(history_id)
        log.info("First notification — saving historyId=%s, no alert sent", history_id)
        return []

    try:
        result = service.users().history().list(
            userId="me",
            startHistoryId=last_id,
            historyTypes=["messageAdded"],
        ).execute()
    except Exception as e:
        log.error("history.list() failed: %s", e)
        _save_history_id(history_id)
        return []

    _save_history_id(history_id)

    messages = []
    for record in result.get("history", []):
        for msg_added in record.get("messagesAdded", []):
            msg = msg_added.get("message", {})
            msg_id = msg.get("id")
            if not msg_id:
                continue
            labels = set(msg.get("labelIds", []))
            if labels & {"SENT", "DRAFT", "SPAM", "TRASH"}:
                continue
            if "INBOX" not in labels and "UNREAD" not in labels:
                continue
            try:
                meta = service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                ).execute()
                hdrs = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
                messages.append({
                    "from": hdrs.get("from", "(no sender)"),
                    "subject": hdrs.get("subject", "(no subject)"),
                    "date": hdrs.get("date", ""),
                })
            except Exception as e:
                log.error("Failed to fetch message %s: %s", msg_id, e)
    return messages


# ---------------------------------------------------------------------------
# Pub/Sub pull
# ---------------------------------------------------------------------------

def _pull_and_process() -> int:
    """Pull messages from Pub/Sub, process each, ack. Returns count of historyId events handled."""
    from google.cloud.pubsub_v1 import SubscriberClient

    creds = _get_creds()
    subscriber = SubscriberClient(credentials=creds)
    try:
        response = subscriber.pull(
            request={"subscription": SUBSCRIPTION_PATH, "max_messages": 10},
            timeout=10,
        )
    except Exception as e:
        log.error("Pub/Sub pull failed: %s", e)
        subscriber.close()
        return 0

    received = response.received_messages
    if not received:
        subscriber.close()
        return 0

    ack_ids = []
    gmail_service = None
    processed = 0

    for msg in received:
        ack_ids.append(msg.ack_id)
        try:
            data = base64.b64decode(msg.message.data).decode("utf-8")
            payload = json.loads(data)
            history_id = str(payload.get("historyId", ""))
            email_address = payload.get("emailAddress", "")
            log.info("Pub/Sub message: email=%s historyId=%s", email_address, history_id)

            if history_id:
                if gmail_service is None:
                    gmail_service = build("gmail", "v1", credentials=creds)
                new_messages = _fetch_new_messages(gmail_service, history_id)
                for m in new_messages:
                    text = f"📧 Новое письмо\n📤 От: {m['from']}\n📌 Тема: {m['subject']}"
                    log.info("Sending Telegram: from=%s subject=%s", m["from"], m["subject"])
                    _send_telegram(text)
                processed += 1
        except Exception as e:
            log.error("Error processing Pub/Sub message: %s", e, exc_info=True)

    if ack_ids:
        try:
            subscriber.acknowledge(
                request={"subscription": SUBSCRIPTION_PATH, "ack_ids": ack_ids}
            )
        except Exception as e:
            log.error("Ack failed: %s", e)

    subscriber.close()
    return processed


# ---------------------------------------------------------------------------
# Main polling loop
# ---------------------------------------------------------------------------

def main():
    log.info("Starting Gmail Push Polling Server (poll_interval=%ds)", POLL_INTERVAL)
    log.info("Subscription: %s", SUBSCRIPTION_PATH)

    total_processed = 0

    while True:
        try:
            count = _pull_and_process()
            if count:
                log.info("Processed %d Pub/Sub message(s)", count)
                total_processed += count
        except Exception as e:
            log.error("Poll cycle error: %s", e, exc_info=True)

        try:
            STATUS_FILE.write_text(json.dumps({
                "status": "running",
                "last_poll": datetime.now(timezone.utc).isoformat(),
                "total_processed": total_processed,
                "subscription": SUBSCRIPTION_PATH,
            }, indent=2))
        except Exception:
            pass

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
