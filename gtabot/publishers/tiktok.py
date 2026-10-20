"""Publish videos on TikTok through the Content Posting API (Direct Post)."""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path

import requests

from ..config import Config

log = logging.getLogger(__name__)

API = "https://open.tiktokapis.com/v2"
MAX_SINGLE_CHUNK = 64 * 1024 * 1024
CHUNK = 10 * 1024 * 1024


class TikTokPublisher:
    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.cfg = cfg
        self.http = session or requests.Session()
        self.access_token: str | None = None

    # Auth --------------------------------------------------------------
    def refresh(self) -> None:
        resp = self.http.post(
            f"{API}/oauth/token/",
            data={
                "client_key": self.cfg.tiktok_client_key,
                "client_secret": self.cfg.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.cfg.tiktok_refresh_token,
            },
            timeout=30,
        )
        data = resp.json()
        if resp.status_code >= 400 or "access_token" not in data:
            raise RuntimeError(f"TikTok token refresh failed: {data}")
        self.access_token = data["access_token"]
        new_refresh = data.get("refresh_token")
        if new_refresh and new_refresh != self.cfg.tiktok_refresh_token:
            self.cfg.tiktok_refresh_token = new_refresh
            store_refresh_token(self.cfg, new_refresh, self.http)

    def _call(self, path: str, body: dict) -> dict:
        resp = self.http.post(
            f"{API}{path}",
            json=body,
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json; charset=UTF-8"},
            timeout=60,
        )
        data = resp.json()
        if resp.status_code >= 400 or data.get("error", {}).get("code", "ok") != "ok":
            raise RuntimeError(f"TikTok {path} -> {resp.status_code}: {data}")
        return data["data"]

    # Posting -----------------------------------------------------------
    def privacy_level(self) -> str:
        options = self._call("/post/publish/creator_info/query/", {}).get("privacy_level_options", [])
        if self.cfg.tiktok_privacy in options:
            return self.cfg.tiktok_privacy
        log.warning("Privacy %s not allowed (%s); using SELF_ONLY", self.cfg.tiktok_privacy, options)
        return "SELF_ONLY"

    def post(self, video: Path, caption: str) -> str:
        if not self.access_token:
            self.refresh()
        size = video.stat().st_size
        if size <= MAX_SINGLE_CHUNK:
            chunk_size, chunks = size, 1
        else:
            chunk_size, chunks = CHUNK, size // CHUNK  # last chunk absorbs the remainder
        init = self._call("/post/publish/video/init/", {
            "post_info": {
                "title": caption[:2200],
                "privacy_level": self.privacy_level(),
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
                "is_aigc": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunks,
            },
        })
        publish_id, upload_url = init["publish_id"], init["upload_url"]
        with video.open("rb") as fh:
            for i in range(chunks):
                start = i * chunk_size
                data = fh.read() if i == chunks - 1 else fh.read(chunk_size)
                end = start + len(data) - 1
                resp = self.http.put(
                    upload_url,
                    data=data,
                    headers={"Content-Type": "video/mp4", "Content-Range": f"bytes {start}-{end}/{size}"},
                    timeout=300,
                )
                if resp.status_code >= 400:
                    raise RuntimeError(f"TikTok chunk upload failed: {resp.status_code} {resp.text[:300]}")
        self.wait(publish_id)
        log.info("Posted on TikTok: %s", publish_id)
        return publish_id

    def wait(self, publish_id: str, timeout: int = 300) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self._call("/post/publish/status/fetch/", {"publish_id": publish_id})
            state = status.get("status")
            if state == "PUBLISH_COMPLETE":
                return
            if state == "FAILED":
                raise RuntimeError(f"TikTok publish failed: {status}")
            time.sleep(10)
        log.warning("TikTok still processing %s; it will finish on TikTok's side", publish_id)


def store_refresh_token(cfg: Config, token: str, http: requests.Session) -> None:
    """Save a rotated refresh token into the repo's Actions secrets, so the next run can use it."""
    if not (cfg.github_token and cfg.github_repo):
        log.warning("TikTok refresh token rotated but GH_SECRETS_TOKEN is not set; update the secret by hand")
        return
    from nacl import encoding, public

    headers = {"Authorization": f"Bearer {cfg.github_token}", "Accept": "application/vnd.github+json"}
    base = f"https://api.github.com/repos/{cfg.github_repo}/actions/secrets"
    key = http.get(f"{base}/public-key", headers=headers, timeout=30).json()
    sealed = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    encrypted = base64.b64encode(sealed.encrypt(token.encode())).decode()
    resp = http.put(
        f"{base}/TIKTOK_REFRESH_TOKEN",
        json={"encrypted_value": encrypted, "key_id": key["key_id"]},
        headers=headers,
        timeout=30,
    )
    if resp.status_code >= 400:
        log.error("Could not store the new TikTok refresh token: %s", resp.text[:300])
    else:
        log.info("Stored rotated TikTok refresh token in repo secrets")
