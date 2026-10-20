"""Publish posts (optionally with the video) on X through the v2 API."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import requests
from requests_oauthlib import OAuth1

from ..config import Config

log = logging.getLogger(__name__)

API = "https://api.x.com/2"
CHUNK = 4 * 1024 * 1024


class XPublisher:
    def __init__(self, cfg: Config, session: requests.Session | None = None):
        self.auth = OAuth1(cfg.x_api_key, cfg.x_api_secret, cfg.x_access_token, cfg.x_access_secret)
        self.http = session or requests.Session()

    def _call(self, method: str, path: str, **kwargs) -> dict:
        resp = self.http.request(method, f"{API}{path}", auth=self.auth, timeout=60, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"X API {method} {path} -> {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}

    def upload_video(self, path: Path) -> str:
        size = path.stat().st_size
        init = self._call(
            "POST", "/media/upload/initialize",
            json={"media_type": "video/mp4", "total_bytes": size, "media_category": "tweet_video"},
        )
        media_id = init["data"]["id"]
        with path.open("rb") as fh:
            index = 0
            while chunk := fh.read(CHUNK):
                self._call(
                    "POST", f"/media/upload/{media_id}/append",
                    files={"media": ("video.mp4", chunk, "application/octet-stream")},
                    data={"segment_index": index},
                )
                index += 1
        info = self._call("POST", f"/media/upload/{media_id}/finalize")["data"].get("processing_info")
        while info and info.get("state") in {"pending", "in_progress"}:
            time.sleep(info.get("check_after_secs", 5))
            status = self._call("GET", "/media/upload", params={"command": "STATUS", "media_id": media_id})
            info = status["data"].get("processing_info")
        if info and info.get("state") == "failed":
            raise RuntimeError(f"X video processing failed: {info}")
        return media_id

    def post(self, text: str, video: Path | None = None, quote_id: str | None = None) -> str:
        body: dict = {"text": text}
        if quote_id:
            body["quote_tweet_id"] = quote_id
        if video:
            try:
                body["media"] = {"media_ids": [self.upload_video(video)]}
            except Exception as exc:  # a failed upload should not cost us the post
                log.warning("Video upload to X failed, posting text only: %s", exc)
        tweet_id = self._call("POST", "/tweets", json=body)["data"]["id"]
        log.info("Posted on X: %s", tweet_id)
        return tweet_id
