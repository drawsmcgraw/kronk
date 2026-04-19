"""
One-time interactive setup for Withings OAuth2.

Run this script once to authorize Kronk to read your Withings data.
Tokens are saved to /data/withings_tokens.json and reused automatically.

Usage (from the host, with /data mounted):
    python setup_withings_auth.py

What it does:
  1. Reads your Withings client_id and client_secret.
     TODO: pull from Infisical. Currently prompts interactively.
  2. Opens the Withings OAuth consent page in your browser.
  3. Starts a local HTTP server on port 8080 to capture the redirect.
  4. Exchanges the authorization code for access + refresh tokens.
  5. Saves everything to /data/withings_tokens.json.
"""

import json
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

TOKEN_FILE    = Path("/data/withings_tokens.json")
REDIRECT_URI  = "http://localhost:8080/callback"
AUTH_URL      = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL     = "https://wbsapi.withings.net/v2/oauth2"
SCOPE         = "user.metrics"
STATE         = "kronk-setup"

_captured_code: str | None = None


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _captured_code
        parsed  = urlparse(self.path)
        params  = parse_qs(parsed.query)
        code    = params.get("code", [None])[0]
        error   = params.get("error", [None])[0]

        if error:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(f"Authorization failed: {error}".encode())
            return

        if code:
            _captured_code = code
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authorization successful. You can close this tab.")
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"No code in callback. Check redirect URI configuration.")

    def log_message(self, format, *args):
        pass  # suppress server request logs


def _load_client_credentials() -> tuple[str, str]:
    """
    TODO: replace with Infisical fetch once Withings creds are stored there.
    Currently reads from the token file (if already set) or prompts the user.
    """
    if TOKEN_FILE.exists():
        existing = json.loads(TOKEN_FILE.read_text())
        cid = existing.get("client_id", "")
        cs  = existing.get("client_secret", "")
        if cid and cs:
            print(f"Using stored client_id: {cid[:8]}...")
            return cid, cs

    print("Enter your Withings developer app credentials.")
    print("Find them at developer.withings.com → your application.\n")
    client_id     = input("client_id:     ").strip()
    client_secret = input("client_secret: ").strip()
    if not client_id or not client_secret:
        print("ERROR: client_id and client_secret are required.")
        sys.exit(1)
    return client_id, client_secret


def main():
    global _captured_code

    client_id, client_secret = _load_client_credentials()

    # Build authorization URL
    params = {
        "response_type": "code",
        "client_id":     client_id,
        "redirect_uri":  REDIRECT_URI,
        "scope":         SCOPE,
        "state":         STATE,
    }
    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    print("\nOpening Withings authorization page...")
    print(f"If the browser does not open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Start local server to capture the callback
    server = HTTPServer(("localhost", 8080), _CallbackHandler)
    server.timeout = 120
    print("Waiting for authorization (timeout: 120s)...")

    deadline = time.time() + 120
    while _captured_code is None and time.time() < deadline:
        server.handle_request()

    server.server_close()

    if not _captured_code:
        print("ERROR: Timed out waiting for authorization code.")
        sys.exit(1)

    print(f"Authorization code received.")

    # Exchange code for tokens
    print("Exchanging code for tokens...")
    with httpx.Client(timeout=30) as client:
        resp = client.post(
            TOKEN_URL,
            data={
                "action":       "requesttoken",
                "grant_type":   "authorization_code",
                "client_id":    client_id,
                "client_secret": client_secret,
                "code":         _captured_code,
                "redirect_uri": REDIRECT_URI,
            },
        )
        resp.raise_for_status()
        body = resp.json()

    if body.get("status") != 0:
        print(f"ERROR: Token exchange failed: {body}")
        sys.exit(1)

    payload = body["body"]
    tokens = {
        "client_id":     client_id,
        "client_secret": client_secret,
        "access_token":  payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at":    int(time.time()) + int(payload["expires_in"]),
        "userid":        payload.get("userid"),
    }

    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens, indent=2))
    print(f"\nTokens saved to {TOKEN_FILE}")
    print("Withings auth complete. The sync service will refresh tokens automatically.")

    # TODO: write tokens to Infisical instead of (or in addition to) the file.


if __name__ == "__main__":
    main()
