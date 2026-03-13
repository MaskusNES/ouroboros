"""Gmail metadata reader tool.

Reads ONLY sender (From) and Subject of emails — no body, no attachments.
Uses Gmail API with gmail.metadata scope.

Setup:
1. Place OAuth client credentials at /opt/ouroboros_data/gmail_credentials.json
2. Run scripts/gmail_auth.py once to authorize and create the token
3. Token is stored at /opt/ouroboros_data/gmail_token.json
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CREDENTIALS_PATH = "/opt/ouroboros_data/gmail_credentials.json"
TOKEN_PATH = "/opt/ouroboros_data/gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.metadata"]

REQUIRED_PACKAGES = ["google-auth", "google-auth-oauthlib", "google-api-python-client"]


def _check_dependencies() -> Optional[str]:
    """Return error string if required packages are missing, None if OK."""
    missing = []
    try:
        import google.auth  # noqa: F401
    except ImportError:
        missing.append("google-auth")
    try:
        import google_auth_oauthlib  # noqa: F401
    except ImportError:
        missing.append("google-auth-oauthlib")
    try:
        import googleapiclient  # noqa: F401
    except ImportError:
        missing.append("google-api-python-client")

    if missing:
        pkgs = " ".join(missing)
        return (
            f"⚠️ Gmail tool requires missing packages: {missing}\n"
            f"Install with: pip install {pkgs}"
        )
    return None


def _get_gmail_service():
    """Build and return an authenticated Gmail API service."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not os.path.exists(TOKEN_PATH):
        raise FileNotFoundError(
            f"Gmail token not found at {TOKEN_PATH}. "
            "Run scripts/gmail_auth.py to authorize first."
        )

    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Save refreshed token
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
        else:
            raise ValueError(
                "Gmail token is invalid and cannot be refreshed. "
                "Run scripts/gmail_auth.py to re-authorize."
            )

    return build("gmail", "v1", credentials=creds)


def _gmail_check(ctx, query: str = "is:unread", max_results: int = 20) -> str:
    """List recent emails — From and Subject only, no body."""
    err = _check_dependencies()
    if err:
        return err

    try:
        service = _get_gmail_service()
    except (FileNotFoundError, ValueError) as e:
        return f"⚠️ Gmail auth error: {e}"
    except Exception as e:
        return f"⚠️ Gmail service error: {type(e).__name__}: {e}"

    try:
        result = service.users().messages().list(
            userId="me",
            q=query,
            maxResults=min(max_results, 50),
        ).execute()
    except Exception as e:
        return f"⚠️ Gmail API error (list): {type(e).__name__}: {e}"

    messages = result.get("messages", [])
    if not messages:
        return f"No emails found for query: `{query}`"

    emails = []
    for msg in messages:
        try:
            detail = service.users().messages().get(
                userId="me",
                id=msg["id"],
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            ).execute()

            headers = {
                h["name"]: h["value"]
                for h in detail.get("payload", {}).get("headers", [])
            }
            emails.append({
                "id": msg["id"],
                "from": headers.get("From", "(unknown)"),
                "subject": headers.get("Subject", "(no subject)"),
                "date": headers.get("Date", ""),
            })
        except Exception as e:
            log.warning("Failed to fetch message %s: %s", msg["id"], e)
            continue

    if not emails:
        return "No email metadata could be retrieved."

    lines = [f"📬 Found {len(emails)} email(s) (query: `{query}`):"]
    for i, em in enumerate(emails, 1):
        lines.append(f"\n{i}. **From:** {em['from']}")
        lines.append(f"   **Subject:** {em['subject']}")
        if em["date"]:
            lines.append(f"   **Date:** {em['date']}")

    return "\n".join(lines)


def get_tools():
    from ouroboros.tools.registry import ToolEntry
    return [
        ToolEntry("gmail_check", {
            "name": "gmail_check",
            "description": (
                "List recent emails from Gmail — reads ONLY sender (From) and Subject. "
                "No email body, no attachments. Requires prior OAuth setup via scripts/gmail_auth.py."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Gmail search query (default: 'is:unread'). Examples: 'is:unread', 'from:boss@company.com', 'is:unread newer_than:1d'",
                        "default": "is:unread",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default: 20, max: 50)",
                        "default": 20,
                    },
                },
                "required": [],
            },
        }, _gmail_check),
    ]
