"""Reuse existing videos legally: YouTube videos under Creative Commons Attribution (CC BY).

The bot searches YouTube with the Creative Commons filter, double checks the license on
each video page, downloads only the segment it needs, turns it vertical and credits the author.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus

from .config import ROOT

log = logging.getLogger(__name__)

W, H = 1080, 1920
FONTS_DIR = ROOT / "assets" / "fonts"
CC_FILTER = "EgIwAQ%253D%253D"  # YouTube search filter "Creative Commons"
SEARCH_URL = "https://www.youtube.com/results?search_query={query}&sp=" + CC_FILTER
# Re-uploads of Rockstar's own trailers get claimed even when the uploader marks them CC.
SKIP_TITLES = re.compile(
    r"\b(official|oficial|re-?upload|leak(ed)?|filtra\w*)\b|^\W*gta\s?(6|vi)\W*(trailer|tr[aá]iler)\s*#?\d?\W*$", re.I)
RELEVANT = re.compile(r"\b(gta\s?(6|vi)|grand theft auto\s?(6|vi)|leonida)\b", re.I)


@dataclass
class CCVideo:
    id: str
    title: str
    author: str
    url: str
    duration: float
    description: str = ""

    @property
    def credit(self) -> str:
        return f"Video: {self.author} (CC BY) · {self.url}"


def _ydl(opts: dict):
    from yt_dlp import YoutubeDL

    base = {"quiet": True, "no_warnings": True}
    cookies = os.environ.get("YT_COOKIES_FILE")
    if cookies and Path(cookies).is_file():  # YouTube blocks datacenter IPs without a signed-in session
        base["cookiefile"] = cookies
    return YoutubeDL({**base, **opts})


def is_cc_by(info: dict) -> bool:
    return "creative commons" in (info.get("license") or "").lower()


def acceptable(info: dict) -> bool:
    title = info.get("title") or ""
    duration = info.get("duration") or 0
    return (
        is_cc_by(info)
        and RELEVANT.search(f"{title} {info.get('description') or ''}") is not None
        and not SKIP_TITLES.search(title)
        and 20 <= duration <= 1800
        and not info.get("is_live")
    )


def find(queries: list[str], seen: set[str], per_query: int = 15) -> CCVideo | None:
    for query in queries:
        try:
            listing = _ydl({"extract_flat": "in_playlist", "playlistend": per_query}).extract_info(
                SEARCH_URL.format(query=quote_plus(query)), download=False)
        except Exception as exc:  # yt-dlp raises many error types
            log.warning("YouTube search failed for '%s': %s", query, exc)
            continue
        for entry in listing.get("entries") or []:
            vid = entry.get("id")
            if not vid or f"cc{vid}" in seen:
                continue
            try:
                info = _ydl({}).extract_info(f"https://www.youtube.com/watch?v={vid}", download=False)
            except Exception as exc:
                log.warning("Could not read video %s: %s", vid, exc)
                if "not a bot" in str(exc) or "needs to be reloaded" in str(exc):
                    log.error("YouTube is blocking this machine; refresh the YT_COOKIES secret (see README)")
                    return None
                continue
            if acceptable(info):
                log.info("CC BY video found: %s (%s)", info["title"], info.get("channel"))
                return CCVideo(vid, info["title"], info.get("channel") or info.get("uploader") or "autor",
                               f"https://youtu.be/{vid}", float(info["duration"]), (info.get("description") or "")[:800])
            log.info("Skipping %s: license=%r", vid, info.get("license"))
    return None


def segment(duration: float, max_len: float = 58.0) -> tuple[float, float]:
    """Pick the part to reuse: skip intros, keep it under a minute."""
    if duration <= max_len:
        return 0.0, duration
    start = min(duration * 0.15, duration - max_len)
    return start, start + max_len


def download(video: CCVideo, workdir: Path) -> Path:
    from yt_dlp.utils import download_range_func

    start, end = segment(video.duration)
    workdir.mkdir(parents=True, exist_ok=True)
    opts = {
        "format": "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "source.%(ext)s"),
        "download_ranges": download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
    }
    _ydl(opts).download([video.url])
    return next(workdir.glob("source.*"))


def _ass(headline: str, credit: str) -> str:
    def esc(text: str) -> str:
        return text.replace("{", "(").replace("}", ")").replace("\n", " ")

    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Head,Anton,92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,6,3,8,70,70,0,1
Style: Credit,Anton,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&HAA000000,0,0,0,0,100,100,1,0,3,10,0,2,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 1,0:00:00.00,9:00:00.00,Head,,0,0,0,,{{\\pos(540,330)}}{esc(headline.upper())}
Dialogue: 1,0:00:00.00,9:00:00.00,Credit,,0,0,0,,{{\\pos(540,1480)}}{esc(credit)}
"""


def make_vertical(source: Path, out: Path, headline: str, credit: str) -> Path:
    """Blurred full-screen copy behind the original, headline on top, author credit below."""
    ass = out.with_name("overlay.ass")
    ass.write_text(_ass(headline, credit), encoding="utf-8")
    fonts = str(FONTS_DIR).replace(":", "\\:")
    graph = (
        f"[0:v]split[a][b];"
        f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=30:4,eq=brightness=-0.12[bg];"
        f"[b]scale={W}:-2[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,ass={ass.name}:fontsdir={fonts},format=yuv420p[v]"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(source.resolve()),
        "-filter_complex", graph, "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-af", "loudnorm=I=-14:TP=-1.5", "-movflags", "+faststart", out.name,
    ]
    proc = subprocess.run(cmd, cwd=out.parent, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-1500:]}")
    return out
