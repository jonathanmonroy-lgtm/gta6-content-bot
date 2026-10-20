"""Backgrounds for each scene, best source first:

1. Pexels stock video (free key; commercial use allowed, no attribution required)
2. Nano Banana / Gemini image (Google AI Studio key)
3. Pollinations image (free, no key)
4. None -> animated gradient

All of them give the Vice City mood (Miami, neon, beaches, cars) without reusing Rockstar material.
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from urllib.parse import quote

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


IMAGE_STYLE = (
    "cinematic vertical photograph, {query}, Miami Florida vibe, dramatic lighting, vibrant pink and teal tones, "
    "shallow depth of field, ultra detailed, 9:16, no text, no logos, no watermark"
)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{prompt}"


class Broll:
    def __init__(self, api_key: str | None, workdir: Path, session: requests.Session | None = None,
                 gemini_key: str | None = None, gemini_model: str = "gemini-2.5-flash-image",
                 use_pollinations: bool = True):
        self.api_key = api_key
        self.gemini_key = gemini_key
        self.gemini_model = gemini_model
        self.use_pollinations = use_pollinations
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

    def visual(self, query: str, min_seconds: float, name: str, seed: int = 0) -> tuple[Path | None, str]:
        """Return (path, "video" | "image"), or (None, "none") for the animated fallback."""
        clip = self.clip(query, min_seconds, name)
        if clip:
            return clip, "video"
        image = self.image(query, name, seed)
        if image:
            return image, "image"
        return None, "none"

    def image(self, query: str, name: str, seed: int = 0) -> Path | None:
        prompt = IMAGE_STYLE.format(query=query)
        path = self.workdir / f"{name}.jpg"
        if self.gemini_key:
            try:
                resp = self.http.post(
                    GEMINI_URL.format(model=self.gemini_model),
                    params={"key": self.gemini_key},
                    json={
                        "contents": [{"parts": [{"text": prompt}]}],
                        "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": "9:16"}},
                    },
                    timeout=120,
                )
                resp.raise_for_status()
                for part in resp.json()["candidates"][0]["content"]["parts"]:
                    if "inlineData" in part:
                        path.write_bytes(base64.b64decode(part["inlineData"]["data"]))
                        log.info("image '%s' -> gemini", query)
                        return path
            except (requests.RequestException, KeyError, IndexError) as exc:
                log.warning("Gemini image failed for '%s': %s", query, exc)
        if self.use_pollinations:
            url = POLLINATIONS_URL.format(prompt=quote(prompt))
            params = {"width": 1080, "height": 1920, "model": "flux", "nologo": "true", "seed": seed}
            for attempt in range(3):
                try:
                    resp = self.http.get(url, params=params, timeout=180)
                    if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image"):
                        path.write_bytes(resp.content)
                        log.info("image '%s' -> pollinations", query)
                        return path
                    log.warning("Pollinations %s for '%s'", resp.status_code, query)
                except requests.RequestException as exc:
                    log.warning("Pollinations failed for '%s': %s", query, exc)
                time.sleep(8 * (attempt + 1))
        return None

    def clip(self, query: str, min_seconds: float, name: str) -> Path | None:
        """Download a stock clip for this query, if a Pexels key is configured."""
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
