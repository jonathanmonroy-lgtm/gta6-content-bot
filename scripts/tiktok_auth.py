"""One-time helper to get the TikTok refresh token the bot needs.

1. python scripts/tiktok_auth.py url   -> open the printed link, log in with the bot's TikTok account
2. TikTok redirects to your redirect URI with ?code=...; copy that code
3. python scripts/tiktok_auth.py token CODE  -> prints the refresh token to save as TIKTOK_REFRESH_TOKEN

Needs TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET and TIKTOK_REDIRECT_URI in the environment.
"""
import os
import secrets
import sys
from urllib.parse import unquote, urlencode

import requests

SCOPES = "user.info.basic,video.publish,video.upload"


def main() -> None:
    key, secret = os.environ["TIKTOK_CLIENT_KEY"], os.environ["TIKTOK_CLIENT_SECRET"]
    redirect = os.environ["TIKTOK_REDIRECT_URI"]
    if sys.argv[1:2] == ["url"]:
        query = urlencode({
            "client_key": key, "scope": SCOPES, "response_type": "code",
            "redirect_uri": redirect, "state": secrets.token_urlsafe(8),
        })
        print(f"https://www.tiktok.com/v2/auth/authorize/?{query}")
    elif sys.argv[1:2] == ["token"] and len(sys.argv) == 3:
        resp = requests.post("https://open.tiktokapis.com/v2/oauth/token/", data={
            "client_key": key, "client_secret": secret, "code": unquote(sys.argv[2]),
            "grant_type": "authorization_code", "redirect_uri": redirect,
        }, timeout=30)
        data = resp.json()
        print("TIKTOK_REFRESH_TOKEN =", data.get("refresh_token") or data)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
