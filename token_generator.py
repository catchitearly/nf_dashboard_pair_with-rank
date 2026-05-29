"""
token_generator.py
Run this ONCE locally to generate your Fyers refresh_token.
Paste the output into GitHub Secrets as FYERS_REFRESH_TOKEN.

Usage:
    pip install fyers-apiv3 requests
    python token_generator.py

What it does:
    1. Opens Fyers OAuth login URL in your browser.
    2. You log in and Fyers redirects to a callback URL containing an auth_code.
    3. Paste that full callback URL here.
    4. This script exchanges the auth_code for access_token + refresh_token.
    5. Prints both — paste refresh_token into GitHub Secrets.
"""

import hashlib
import os
import sys
import webbrowser

try:
    import requests
except ImportError:
    print("Install requests first: pip install requests fyers-apiv3")
    sys.exit(1)


def main():
    print("=" * 60)
    print("  Fyers Token Generator")
    print("=" * 60)
    print()

    # ── Get credentials ──────────────────────────────────────
    client_id  = input("Enter FYERS_CLIENT_ID (e.g. ABCD12345-100): ").strip()
    secret_key = input("Enter FYERS_SECRET_KEY: ").strip()
    redirect_uri = input("Enter Redirect URI (from Fyers app settings): ").strip()

    if not client_id or not secret_key or not redirect_uri:
        print("All fields required.")
        sys.exit(1)

    # ── Build OAuth URL ──────────────────────────────────────
    app_id_hash = hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()

    auth_url = (
        f"https://api-t1.fyers.in/api/v3/generate-authcode"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&state=sample"
    )

    print()
    print("Opening Fyers login in browser...")
    print(f"URL: {auth_url}")
    print()
    webbrowser.open(auth_url)

    # ── Get auth code from callback URL ──────────────────────
    print("After login, Fyers redirects to your callback URL.")
    print("It looks like: https://your-redirect/?code=XXXXXX&state=sample")
    print()
    callback_url = input("Paste the full callback URL here: ").strip()

    # Extract auth_code from URL
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(callback_url)
    params = parse_qs(parsed.query)
    auth_code = params.get("auth_code", params.get("code", [None]))[0]

    if not auth_code:
        print("Could not extract auth_code from URL.")
        print("Make sure you copy the full redirect URL including ?code=...")
        sys.exit(1)

    print(f"\nExtracted auth_code: {auth_code[:20]}...")

    # ── Exchange auth_code for tokens ─────────────────────────
    payload = {
        "grant_type":  "authorization_code",
        "appIdHash":   app_id_hash,
        "code":        auth_code,
    }

    resp = requests.post(
        "https://api-t1.fyers.in/api/v3/validate-authcode",
        json=payload,
        timeout=15,
    )
    data = resp.json()

    if data.get("s") != "ok":
        print(f"\nToken exchange failed: {data}")
        sys.exit(1)

    access_token  = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")

    print()
    print("=" * 60)
    print("  SUCCESS — Copy these to GitHub Secrets")
    print("=" * 60)
    print()
    print(f"FYERS_ACCESS_TOKEN  (today only):")
    print(f"  {access_token}")
    print()
    print(f"FYERS_REFRESH_TOKEN  (set once, valid for several days):")
    print(f"  {refresh_token}")
    print()
    print("In GitHub:")
    print("  Settings → Secrets and variables → Actions → New repository secret")
    print()
    print("The token_refresh.yml workflow will use FYERS_REFRESH_TOKEN daily")
    print("to auto-generate a fresh FYERS_ACCESS_TOKEN before market open.")


if __name__ == "__main__":
    main()
