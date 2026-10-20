"""Stock b-roll from Pexels (free license, commercial use allowed, no attribution required).

Gives the videos real moving footage (Miami, neon streets, cars, beaches) that fits the
Vice City mood without reusing any Rockstar material.
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API = "https://api.pexels.com/videos/search"
FALLBACK_QUERIES = ["miami night neon", "miami beach aerial", "sports car night city", "palm trees sunset", "neon city street night"]


def _best_file(video: dict) -> dict | None:
    files = [f for f in video.get("video_files", []) if f.get("file_type") == "video/mp4" and f.get("height")]
    portrait = [f for f in files if f["height"] > f["width"] and f["height"] >= 1280]
    pool = portrait or [f for f in files if f["height"] >= 720]
    if not pool:
        return None
    # the smallest file that is still at least 1920 tall, otherwise the tallest
    tall = [f for f in pool if f["height"] >= 1920]
    return min(tall, key=lambda f: f["height"]) if tall else max(pool, key=lambda f: f["height"])


class Broll:
    def __init__(self, api_key: str | None, workdir: Path, session: requests.Session | None = None):
        self.api_key = api_key
        self.workdir = workdir
        self.http = session or requests.Session()
        self.used: set[int] = set()

    def search(self, query: str, min_seconds: float) -> list[dict]:
        resp = self.http.get(
            API,
            params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 15},
            headers={"Authorization": self.api_key},
            timeout=30,
        )
        resp.raise_for_status()
        videos = [v for v in resp.json().get("videos", []) if v["id"] not in self.used]
        long_enough = [v for v in videos if v.get("duration", 0) >= min_seconds]
        return long_enough or videos

    def clip(self, query: str, min_seconds: float, name: str) -> Path | None:
        """Download a clip for this query; None means use the animated fallback background."""
        if not self.api_key:
            return None
        for q in [query, *FALLBACK_QUERIES]:
            try:
                for video in self.search(q, min_seconds):
                    best = _best_file(video)
                    if not best:
                        continue
                    path = self.workdir / f"{name}.mp4"
                    with self.http.get(best["link"], stream=True, timeout=120) as r:
                        r.raise_for_status()
                        with path.open("wb") as fh:
                            for chunk in r.iter_content(1 << 20):
                                fh.write(chunk)
                    self.used.add(video["id"])
                    log.info("b-roll '%s' -> pexels video %s", q, video["id"])
                    return path
            except requests.RequestException as exc:
                log.warning("Pexels search failed for '%s': %s", q, exc)
        return None
