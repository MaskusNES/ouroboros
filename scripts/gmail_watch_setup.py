#!/usr/bin/env python3
"""
Gmail Watch Setup — two-step OAuth flow:

Step 1 (no args): Generate auth URL and save state.
  python3 scripts/gmail_watch_setup.py

Step 2 (--complete-auth CODE): Exchange code, set up Pub/Sub pull + watch.
  python3 scripts/gmail_watch_setup.py --complete-auth "CODE_HERE"
"""

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

REPO_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_DIR))

DATA_DIR = Path(os.environ.get("OUROBOROS_DATA", "/opt/ouroboros_data"))
CREDS_FILE = DATA_DIR / "gmail_credentials.json"
TOKEN_FILE = DATA_DIR / "gmail_watch_token.json"
AUTH_STATE_FILE = DATA_DIR / "gmail_watch_auth_state.json"
WATCH_STATE_FILE = DATA_DIR / "gmail_watch_state.json"

PROJECT_ID = "my-project-35724"
TOPIC_NAME = "gmail-push-notifications"
TOPIC_FULL = f"projects/{PROJECT_ID}/topics/{TOPIC_NAME}"
SUBSCRIPTION_NAME = "gmail-push-sub"
SUBSCRIPTION_FULL = f"projects/{PROJECT_ID}/subscriptions/{SUBSCRIPTION_NAME}"
PUBSUB_SA = "serviceAccount:gmail-api-push@system.gserviceaccount.com"

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
REDIRECT_URI = "urn:ietf:wg:oauth:2.0:oob"


def cmd_generate_url():
    """Generate OAuth auth URL and save state for later completion."""
    from google_auth_oauthlib.flow import Flow

    if not CREDS_FILE.exists():
        print(f"ERROR: Credentials not found: {CREDS_FILE}")
        sys.exit(1)

    flow = Flow.from_client_secrets_file(
        str(CREDS_FILE),
        scopes=GMAIL_SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="false",
    )

    auth_state = {
        "client_secrets": json.loads(CREDS_FILE.read_text()),
        "state": state,
        "scopes": GMAIL_SCOPES,
        "redirect_uri": REDIRECT_URI,
    }
    AUTH_STATE_FILE.write_text(json.dumps(auth_state, indent=2))

    print("=" * 60)
    print("Gmail Watch Auth — Step 1 of 2")
    print("=" * 60)
    print(f"\n1. Open this URL in your browser:\n\n{auth_url}\n")
    print("2. Authorize and copy the code shown.")
    print("3. Run:\n")
    print(f"   python3 scripts/gmail_watch_setup.py --complete-auth \"CODE_HERE\"\n")
    print(f"   (or: python3 scripts/gmail_complete_auth.py \"CODE_HERE\")")
    print(f"\nAuth state saved to: {AUTH_STATE_FILE}")


def cmd_complete_auth(code: str):
    """Exchange OAuth code for token, create Pub/Sub pull subscription, register watch."""
    from google_auth_oauthlib.flow import Flow

    if not AUTH_STATE_FILE.exists():
        print(f"ERROR: Auth state not found: {AUTH_STATE_FILE}")
        print("Run without args first to generate the auth URL.")
        sys.exit(1)

    auth_state = json.loads(AUTH_STATE_FILE.read_text())

    flow = Flow.from_client_config(
        auth_state["client_secrets"],
        scopes=auth_state["scopes"],
        state=auth_state["state"],
        redirect_uri=auth_state["redirect_uri"],
    )

    print("Exchanging authorization code for token...")
    flow.fetch_token(code=code)
    creds = flow.credentials

    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else GMAIL_SCOPES,
        "universe_domain": "googleapis.com",
    }
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))
    print(f"✅ Token saved to {TOKEN_FILE}")

    _create_pubsub_topic(creds.token)
    _create_pubsub_pull_subscription(creds.token)
    _register_gmail_watch(creds)

    AUTH_STATE_FILE.unlink(missing_ok=True)
    print("\n✅ Setup complete!")
    print("Start the polling server: sudo systemctl start gmail-push")


def _create_pubsub_topic(access_token: str):
    import requests

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    url = f"https://pubsub.googleapis.com/v1/{TOPIC_FULL}"
    print(f"\nCreating Pub/Sub topic: {TOPIC_FULL}")
    r = requests.put(url, headers=headers, json={})
    if r.status_code in (200, 409):
        print(f"✅ Topic exists/created ({r.status_code})")
    else:
        print(f"⚠️  Topic creation: {r.status_code} {r.text}")

    iam_url = f"https://pubsub.googleapis.com/v1/{TOPIC_FULL}:setIamPolicy"
    policy = {
        "policy": {
            "bindings": [{"role": "roles/pubsub.publisher", "members": [PUBSUB_SA]}]
        }
    }
    r2 = requests.post(iam_url, headers=headers, json=policy)
    if r2.status_code == 200:
        print(f"✅ IAM policy set for {PUBSUB_SA}")
    else:
        print(f"⚠️  IAM policy: {r2.status_code} {r2.text}")


def _create_pubsub_pull_subscription(access_token: str):
    import requests

    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    url = f"https://pubsub.googleapis.com/v1/{SUBSCRIPTION_FULL}"
    body = {
        "topic": TOPIC_FULL,
        "ackDeadlineSeconds": 30,
        # No pushConfig → pull subscription
    }
    print(f"\nCreating Pub/Sub pull subscription: {SUBSCRIPTION_FULL}")
    r = requests.put(url, headers=headers, json=body)
    if r.status_code in (200, 409):
        print(f"✅ Pull subscription exists/created ({r.status_code})")
    else:
        print(f"⚠️  Subscription creation: {r.status_code} {r.text}")


def _register_gmail_watch(creds):
    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=creds)
    print(f"\nRegistering Gmail watch → {TOPIC_FULL}")
    result = service.users().watch(
        userId="me",
        body={"topicName": TOPIC_FULL, "labelIds": ["INBOX"]},
    ).execute()

    history_id = result.get("historyId", "")
    expiration = result.get("expiration", "")
    expiry_iso = (
        datetime.datetime.fromtimestamp(int(expiration) / 1000).isoformat()
        if expiration else ""
    )

    print(f"✅ Gmail watch registered")
    print(f"   historyId: {history_id}")
    print(f"   Expires: {expiry_iso}")

    state = {
        "history_id": history_id,
        "last_history_id": history_id,
        "expiration_ms": int(expiration) if expiration else 0,
        "expiration_iso": expiry_iso,
        "topic": TOPIC_FULL,
        "subscription": SUBSCRIPTION_FULL,
    }
    WATCH_STATE_FILE.write_text(json.dumps(state, indent=2))
    print(f"   State saved to {WATCH_STATE_FILE}")
    return state


def main():
    parser = argparse.ArgumentParser(description="Gmail Watch Setup")
    parser.add_argument(
        "--complete-auth",
        metavar="CODE",
        help="Exchange OAuth code for token and register watch",
    )
    args = parser.parse_args()

    if args.complete_auth:
        cmd_complete_auth(args.complete_auth)
    else:
        cmd_generate_url()


if __name__ == "__main__":
    main()
