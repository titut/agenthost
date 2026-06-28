#!/usr/bin/env python3
"""
Gmail API — One-Time Auth Setup Script
=======================================

This script walks you through Google OAuth2 to get a refresh token.
Run it ONCE on your local machine (it opens a browser).

Prerequisites:
  1. A Google Cloud Project with the Gmail API enabled
  2. An OAuth 2.0 Desktop Client credential (download credentials.json)
  3. Python 3.7+ with these packages installed:
     pip install --upgrade google-api-python-client google-auth-httplib2 google-auth-oauthlib

Usage:
  python setup_gmail_auth.py

It will:
  - Open your browser to log into Google
  - Ask you to grant gmail.modify permissions
  - Print the environment variables to set
"""

import os
import sys
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_FILE = "credentials.json"


def main():
    # Check credentials file exists
    if not os.path.exists(CREDENTIALS_FILE):
        print(f"❌ File not found: {CREDENTIALS_FILE}")
        print()
        print("To get it:")
        print("  1. Go to https://console.cloud.google.com/apis/credentials")
        print("  2. Create an OAuth 2.0 Client ID (type: Desktop app)")
        print("  3. Download the JSON file and save it as 'credentials.json'")
        print(f"     in this directory ({os.path.dirname(os.path.abspath(__file__))})")
        sys.exit(1)

    # Validate the credentials file
    try:
        with open(CREDENTIALS_FILE) as f:
            creds_data = json.load(f)
        if "installed" not in creds_data:
            print("❌ Invalid credentials.json format.")
            print("   Make sure you downloaded a 'Desktop app' OAuth client.")
            print("   The file should contain an 'installed' key.")
            sys.exit(1)
    except json.JSONDecodeError:
        print("❌ credentials.json is not valid JSON.")
        sys.exit(1)

    print("🔐 Starting OAuth2 flow...")
    print(f"   Scopes: {', '.join(SCOPES)}")
    print("   Your browser will open. Log in and grant permission.")
    print()

    try:
        flow = InstalledAppFlow.from_client_secrets_file(
            CREDENTIALS_FILE, SCOPES
        )
        creds = flow.run_local_server(port=0)
    except Exception as e:
        print(f"❌ OAuth flow failed: {e}")
        print()
        print("Troubleshooting:")
        print("  - Make sure your browser is working")
        print("  - Check that gmail.modify scope is listed in your")
        print("    OAuth consent screen (APIs & Services → OAuth consent screen)")
        print("  - Add your email as a Test User if the app is in testing mode")
        sys.exit(1)

    print()
    print("=" * 60)
    print("✅ SUCCESS! Set these environment variables:")
    print("=" * 60)
    print()
    print(f"  export GMAIL_CLIENT_ID='{creds.client_id}'")
    print(f"  export GMAIL_CLIENT_SECRET='{creds_data['installed']['client_secret']}'")
    print(f"  export GMAIL_REFRESH_TOKEN='{creds.refresh_token}'")
    print()
    print("=" * 60)
    print("📌 Recommended: Add these to your .bashrc, .zshrc, or .env file")
    print("   so they're available every time you run the agent.")
    print("=" * 60)


if __name__ == "__main__":
    main()
