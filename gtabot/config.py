"""Runtime configuration, read from environment variables.

Every secret is optional: when one is missing the matching step runs in
dry-run mode and writes what it would have published to ``output/``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_FEEDS = [
    # Google News, Spanish and English editions
    "https://news.google.com/rss/search?q=%22GTA+6%22+OR+%22GTA+VI%22&hl=es-419&gl=MX&ceid=MX:es-419",
    "https://news.google.com/rss/search?q=%22GTA+6%22+OR+%22GTA+VI%22+when:2d&hl=en-US&gl=US&ceid=US:en",
    # Community
    "https://www.reddit.com/r/GTA6/top/.rss?t=day",
    "https://www.reddit.com/r/GTA6/new/.rss",
]


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


@dataclass
class Config:
    dry_run: bool = field(default_factory=lambda: _bool("DRY_RUN", True))
    language: str = field(default_factory=lambda: os.getenv("BOT_LANGUAGE", "es"))
    handle: str = field(default_factory=lambda: os.getenv("BOT_HANDLE", "@GTA6Radar"))
    feeds: list[str] = field(
        default_factory=lambda: [f for f in os.getenv("FEEDS", "").split() if f] or DEFAULT_FEEDS
    )

    # Content cadence
    max_videos_per_day: int = field(default_factory=lambda: _int("MAX_VIDEOS_PER_DAY", 3))
    max_posts_per_day: int = field(default_factory=lambda: _int("MAX_POSTS_PER_DAY", 8))
    evergreen_when_no_news: bool = field(default_factory=lambda: _bool("EVERGREEN_WHEN_NO_NEWS", True))

    # Claude
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY"))
    model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-opus-5-5"))

    # Stock footage for video backgrounds (free key at pexels.com/api)
    pexels_api_key: str | None = field(default_factory=lambda: os.getenv("PEXELS_API_KEY"))

    # Voice for TikTok narration (edge-tts voice name)
    voice: str = field(default_factory=lambda: os.getenv("TTS_VOICE", "es-MX-JorgeNeural"))

    # X (OAuth 1.0a user context, app with Read and Write permission)
    x_api_key: str | None = field(default_factory=lambda: os.getenv("X_API_KEY"))
    x_api_secret: str | None = field(default_factory=lambda: os.getenv("X_API_SECRET"))
    x_access_token: str | None = field(default_factory=lambda: os.getenv("X_ACCESS_TOKEN"))
    x_access_secret: str | None = field(default_factory=lambda: os.getenv("X_ACCESS_TOKEN_SECRET"))
    x_attach_video: bool = field(default_factory=lambda: _bool("X_ATTACH_VIDEO", True))

    # TikTok Content Posting API
    tiktok_client_key: str | None = field(default_factory=lambda: os.getenv("TIKTOK_CLIENT_KEY"))
    tiktok_client_secret: str | None = field(default_factory=lambda: os.getenv("TIKTOK_CLIENT_SECRET"))
    tiktok_refresh_token: str | None = field(default_factory=lambda: os.getenv("TIKTOK_REFRESH_TOKEN"))
    # Unaudited TikTok apps may only post SELF_ONLY; switch to PUBLIC_TO_EVERYONE after the audit.
    tiktok_privacy: str = field(default_factory=lambda: os.getenv("TIKTOK_PRIVACY", "SELF_ONLY"))

    # Lets the bot store the rotated TikTok refresh token back into the repo secrets.
    github_token: str | None = field(default_factory=lambda: os.getenv("GH_SECRETS_TOKEN"))
    github_repo: str | None = field(default_factory=lambda: os.getenv("GITHUB_REPOSITORY"))

    state_path: Path = field(default_factory=lambda: Path(os.getenv("STATE_PATH", ROOT / "data" / "state.json")))
    output_dir: Path = field(default_factory=lambda: Path(os.getenv("OUTPUT_DIR", ROOT / "output")))

    @property
    def x_enabled(self) -> bool:
        return all([self.x_api_key, self.x_api_secret, self.x_access_token, self.x_access_secret])

    @property
    def tiktok_enabled(self) -> bool:
        return all([self.tiktok_client_key, self.tiktok_client_secret, self.tiktok_refresh_token])
