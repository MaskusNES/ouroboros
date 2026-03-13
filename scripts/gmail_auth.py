#!/usr/bin/env python3
"""Gmail OAuth authorization script.

Run this once to authorize Ouroboros to access Gmail metadata.
It will print an authorization URL, wait for you to paste the code,
then save the token to /opt/ouroboros_data/gmail_token.json.

Usage:
    python3 scripts/gmail_auth.py

Requirements:
    pip install google-auth-oauthlib google-api-python-client
"""

import json
import os
import sys

CREDENTIALS_PATH = "/opt/ouroboros_data/gmail_credentials.json"
TOKEN_PATH = "/opt/ouroboros_data/gmail_token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.metadata"]


def main():
    # Check dependencies
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as e:
        print(f"❌ Missing dependency: {e}")
        print("Install with: pip install google-auth-oauthlib google-api-python-client")
        sys.exit(1)

    # Check credentials file
    if not os.path.exists(CREDENTIALS_PATH):
        print(f"❌ Credentials file not found: {CREDENTIALS_PATH}")
        print()
        print("To get credentials:")
        print("  1. Go to https://console.cloud.google.com/")
        print("  2. Create a project (or select existing)")
        print("  3. Enable Gmail API")
        print("  4. Create OAuth 2.0 credentials (Desktop app)")
        print(f"  5. Download JSON and save to: {CREDENTIALS_PATH}")
        sys.exit(1)

    # Check if token already exists and is valid
    if os.path.exists(TOKEN_PATH):
        print(f"Found existing token at {TOKEN_PATH}")
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
            if creds.valid:
                print("✅ Token is already valid. No re-authorization needed.")
                print("   Delete the token file and re-run if you want to re-authorize.")
                return
            elif creds.expired and creds.refresh_token:
                print("Token expired, attempting refresh...")
                creds.refresh(Request())
                with open(TOKEN_PATH, "w") as f:
                    f.write(creds.to_json())
                print("✅ Token refreshed successfully.")
                return
        except Exception as e:
            print(f"⚠️  Existing token invalid ({e}), re-authorizing...")

    # Start OAuth flow
    print(f"Starting OAuth flow with credentials from: {CREDENTIALS_PATH}")
    print(f"Scope: {SCOPES}")
    print()

    try:
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)

        # Try local server first, fall back to manual copy-paste
        try:
            creds = flow.run_local_server(port=0)
        except Exception:
            print("Local server not available. Using manual code entry.")
            print()
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("=" * 60)
            print("Open this URL in your browser:")
            print()
            print(auth_url)
            print()
            print("=" * 60)
            code = input("Paste the authorization code here: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials

    except Exception as e:
        print(f"❌ Authorization failed: {e}")
        sys.exit(1)

    # Save token
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        f.write(creds.to_json())

    print()
    print(f"✅ Auth successful. Token saved to: {TOKEN_PATH}")
    print()
    print("You can now use gmail_check tool in Ouroboros.")
    print(f"Scope granted: {SCOPES}")


if __name__ == "__main__":
    main()
