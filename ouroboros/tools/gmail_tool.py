"""Gmail metadata reader tool.

Reads ONLY From, Subject, Date headers — no body, no attachments.
Uses gmail.metadata scope which physically cannot access message content.
"""

import json
import os
from typing import Any

CREDENTIALS_PATH = "/opt/ouroboros_data/gmail_credentials.json"
TOKEN_PATH = "/opt/ouroboros_data/gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.metadata"]


def _get_service():
    """Build Gmail API service with auto-refresh."""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        raise RuntimeError(
            "Gmail dependencies not installed. Run: "
            "pip install google-auth google-auth-oauthlib google-api-python-client"
        )

    if not os.path.exists(TOKEN_PATH):
        raise RuntimeError(
            f"Gmail token not found at {TOKEN_PATH}. "
            "Run scripts/gmail_auth.py to authorize."
        )

    if not os.path.exists(CREDENTIALS_PATH):
        raise RuntimeError(
            f"Gmail credentials not found at {CREDENTIALS_PATH}."
        )

    with open(TOKEN_PATH) as f:
        token_data = json.load(f)

    with open(CREDENTIALS_PATH) as f:
        creds_info = json.load(f)["installed"]

    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=creds_info["token_uri"],
        client_id=creds_info["client_id"],
        client_secret=creds_info["client_secret"],
        scopes=SCOPES,
    )

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Save refreshed token
        updated = {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds_info["token_uri"],
            "client_id": creds_info["client_id"],
            "client_secret": creds_info["client_secret"],
        }
        with open(TOKEN_PATH, "w") as f:
            json.dump(updated, f, indent=2)

    return build("gmail", "v1", credentials=creds)


def _gmail_check(ctx, max_results: int = 10, label: str = "UNREAD") -> str:
    """Check Gmail inbox — returns sender and subject only (no message body).

    Uses gmail.metadata scope: message content is physically inaccessible.

    Args:
        max_results: Maximum number of messages to return (default 10, max 50)
        label: Gmail label to filter by. Options: UNREAD, INBOX, SENT, etc.
               Note: gmail.metadata scope does not support text search queries.
    """
    max_results = min(max_results, 50)

    try:
        service = _get_service()
    except RuntimeError as e:
        return f"⚠️ Gmail error: {e}"

    try:
        result = service.users().messages().list(
            userId="me",
            maxResults=max_results,
            labelIds=[label.upper()],
        ).execute()
    except Exception as e:
        return f"⚠️ Gmail API error (list): {type(e).__name__}: {e}"

    messages = result.get("messages", [])
    if not messages:
        return f"No emails found with label: {label}"

    emails = []
    for msg in messages:
        try:
            m = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()
            headers = {h["name"]: h["value"] for h in m["payload"]["headers"]}
            emails.append({
                "id": msg["id"],
                "from": headers.get("From", ""),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
            })
        except Exception as e:
            emails.append({"id": msg["id"], "error": str(e)})

    lines = [f"Found {len(emails)} email(s) [label: {label}] (body not accessible — gmail.metadata scope):"]
    for i, em in enumerate(emails, 1):
        if "error" in em:
            lines.append(f"\n{i}. [error fetching {em['id']}: {em['error']}]")
        else:
            lines.append(f"\n{i}. From: {em['from']}")
            lines.append(f"   Subject: {em['subject']}")
            if em["date"]:
                lines.append(f"   Date: {em['date']}")

    return "\n".join(lines)


def get_tools():
    from ouroboros.tools.types import ToolEntry
    return [
        ToolEntry(
            name="gmail_check",
            schema={
                "name": "gmail_check",
                "description": (
                    "Check Maxim's Gmail inbox. Returns ONLY sender (From), subject, and date. "
                    "Message body and attachments are physically inaccessible (gmail.metadata scope). "
                    "Use to monitor important emails and notify Maxim about relevant ones. "
                    "Label options: UNREAD (default), INBOX, SENT."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_results": {
                            "type": "integer",
                            "description": "Number of messages to fetch (default 10, max 50)",
                            "default": 10,
                        },
                        "label": {
                            "type": "string",
                            "description": "Gmail label: UNREAD, INBOX, SENT (default: UNREAD)",
                            "default": "UNREAD",
                        },
                    },
                    "required": [],
                },
            },
            handler=_gmail_check,
        )
    ]
