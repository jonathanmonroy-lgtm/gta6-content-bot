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


@dataclass
class Item:
    id: str
    title: str
    summary: str
    url: str
    source: str
    published: datetime | None

    def to_prompt(self) -> str:
        when = self.published.strftime("%Y-%m-%d %H:%M UTC") if self.published else "fecha desconocida"
        return f"[{self.id}] {self.title}\nFuente: {self.source} · {when}\nURL: {self.url}\n{self.summary}"


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
        items.append(Item(_item_id(url, title), title, summary, url, source, published))
    return items


def fetch_items(feeds: list[str], timeout: int = 20) -> list[Item]:
    items: dict[str, Item] = {}
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
