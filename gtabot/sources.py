"""Extraction: pull GTA VI news and community posts from RSS feeds."""
from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import requests

log = logging.getLogger(__name__)

USER_AGENT = "gta6-content-bot/0.1 (+https://github.com)"
RELEVANT = re.compile(r"\b(gta\s?(6|vi)|grand theft auto\s?(6|vi)|leonida|vice city|rockstar)\b", re.I)
TAGS = re.compile(r"<[^>]+>")


TWEET = re.compile(r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})/status/(\d+)")
CHANNEL_ID = re.compile(r'"(?:channelId|externalId)":"(UC[\w-]{22})"')


@dataclass
class Item:
    id: str
    title: str
    summary: str
    url: str
    source: str
    published: datetime | None
    kind: str = "news"  # news | tweet | youtube
    tweet_id: str | None = None
    author: str | None = None

    def to_prompt(self) -> str:
        when = self.published.strftime("%Y-%m-%d %H:%M UTC") if self.published else "fecha desconocida"
        extra = {"tweet": f"Post de X de @{self.author} (se citará)", "youtube": f"Video de YouTube de {self.author}"}
        kind = extra.get(self.kind, "Noticia")
        return f"[{self.id}] ({kind}) {self.title}\nFuente: {self.source} · {when}\nURL: {self.url}\n{self.summary}"


def _clean(text: str, limit: int = 600) -> str:
    text = html.unescape(TAGS.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _item_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}|{title.lower()}".encode()).hexdigest()[:12]


def parse_feed(raw: bytes | str, feed_url: str) -> list[Item]:
    parsed = feedparser.parse(raw)
    source = _clean(parsed.feed.get("title", feed_url), 80)
    items = []
    for entry in parsed.entries:
        title = _clean(entry.get("title", ""), 200)
        summary = _clean(entry.get("summary", ""))
        if not title or not RELEVANT.search(f"{title} {summary}"):
            continue
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
        url = entry.get("link", "")
        item = Item(_item_id(url, title), title, summary, url, source, published)
        # posts on X shared in news or on Reddit become quote candidates
        raw = f"{url} {entry.get('summary', '')} {' '.join(l.get('href', '') for l in entry.get('links', []))}"
        if tweet := TWEET.search(raw):
            item.kind, item.author, item.tweet_id = "tweet", tweet.group(1), tweet.group(2)
            item.id = f"x{tweet.group(2)}"
            item.url = f"https://x.com/{tweet.group(1)}/status/{tweet.group(2)}"
        elif entry.get("yt_videoid"):
            item.kind, item.author, item.id = "youtube", entry.get("author", source), f"yt{entry.yt_videoid}"
        items.append(item)
    return items


def youtube_feed_url(handle: str, timeout: int = 20) -> str | None:
    """Turn a channel handle like @RockstarGames into its public RSS feed."""
    try:
        page = requests.get(f"https://www.youtube.com/{handle}", headers={"User-Agent": USER_AGENT,
                            "Accept-Language": "en"}, timeout=timeout, cookies={"CONSENT": "YES+1"})
        page.raise_for_status()
    except requests.RequestException as exc:
        log.warning("Could not resolve YouTube channel %s: %s", handle, exc)
        return None
    match = CHANNEL_ID.search(page.text)
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={match.group(1)}" if match else None


def fetch_items(feeds: list[str], channels: list[str] = (), timeout: int = 20) -> list[Item]:
    items: dict[str, Item] = {}
    feeds = list(feeds) + [u for u in (youtube_feed_url(c) for c in channels) if u]
    for url in feeds:
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Feed failed %s: %s", url, exc)
            continue
        for item in parse_feed(resp.content, url):
            items.setdefault(item.id, item)
    oldest = datetime.min.replace(tzinfo=timezone.utc)
    return sorted(items.values(), key=lambda i: i.published or oldest, reverse=True)
