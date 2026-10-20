import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gtabot import ccvideo
from gtabot import main as bot
from gtabot.config import Config
from gtabot.publishers.tiktok import TikTokPublisher
from gtabot.publishers.x import XPublisher
from gtabot.sources import parse_feed
from gtabot.state import State
from gtabot.writer import XPost

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Noticias</title>
<item><title>Rockstar muestra nuevo tráiler de GTA 6</title><link>https://ex.com/a</link>
<description>&lt;b&gt;Lucia&lt;/b&gt; y Jason en Leonida</description>
<pubDate>Mon, 20 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>GTA 6: Rockstar comparte captura nueva</title><link>https://www.reddit.com/r/GTA6/comments/1</link>
<description>&lt;a href="https://x.com/RockstarGames/status/1850000000000000000"&gt;link&lt;/a&gt;</description></item>
<item><title>Receta de pan casero</title><link>https://ex.com/b</link></item>
</channel></rss>"""

YT = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
<title>Rockstar Games</title><entry><yt:videoId>abc123</yt:videoId><title>Grand Theft Auto VI Trailer 3</title>
<link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/><author><name>Rockstar Games</name></author>
<published>2026-10-20T10:00:00+00:00</published></entry></feed>"""


def make_cfg(tmp_path: Path, **kw) -> Config:
    cfg = Config()
    cfg.state_path = tmp_path / "state.json"
    cfg.output_dir = tmp_path / "out"
    cfg.anthropic_api_key = None
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_parse_feed_finds_news_and_x_posts():
    items = parse_feed(RSS, "https://feed")
    assert [i.kind for i in items] == ["news", "tweet"]
    assert items[0].summary == "Lucia y Jason en Leonida"
    tweet = items[1]
    assert tweet.tweet_id == "1850000000000000000" and tweet.author == "RockstarGames"


def test_parse_youtube_feed():
    [item] = parse_feed(YT, "https://yt")
    assert item.kind == "youtube" and item.id == "ytabc123" and item.author == "Rockstar Games"


def test_state_roundtrip_and_counts(tmp_path):
    s = State(tmp_path / "s.json")
    s.mark_seen("abc")
    s.record({"x_id": "1", "video": True, "topic": "trailer"})
    s.save()
    s2 = State(tmp_path / "s.json")
    assert s2.is_seen("abc")
    assert s2.count_since("x_id") == 1 and s2.count_since("video") == 1
    assert s2.recent_topics() == ["trailer"]


def test_compose_tweet_quotes_x_posts_and_links_the_rest():
    news, tweet = parse_feed(RSS, "https://feed")
    post = XPost(publish=True, item_id=None, topic="t", text="Mira esto #GTA6")
    assert bot.compose_tweet(post, tweet) == ("Mira esto #GTA6", "1850000000000000000")
    assert bot.compose_tweet(post, news) == ("Mira esto #GTA6\nhttps://ex.com/a", None)


def test_cc_filter():
    base = {"title": "GTA 6 análisis de Leonida", "duration": 300, "license": "Creative Commons Attribution license (reuse allowed)"}
    assert ccvideo.acceptable(base)
    assert not ccvideo.acceptable({**base, "license": None})
    assert not ccvideo.acceptable({**base, "title": "GTA 6 Official Trailer"})
    assert not ccvideo.acceptable({**base, "title": "GTA 6 Trailer 2"})
    assert not ccvideo.acceptable({**base, "title": "Minecraft speedrun"})


def test_segment_skips_intro_and_caps_length():
    assert ccvideo.segment(40) == (0.0, 40)
    start, end = ccvideo.segment(600)
    assert start == 90 and end - start == 58


def test_run_dry_shares_on_x_and_skips_video_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "fetch_items", lambda feeds, channels: parse_feed(RSS, "https://feed"))
    monkeypatch.setattr(ccvideo, "find", lambda queries, seen: None)
    cfg = make_cfg(tmp_path, dry_run=True)
    result = bot.run(cfg)
    assert result["video"] == "none" and result["x_source"].startswith("https://x.com/")
    assert bot.run(cfg)["x"] == "skipped"  # nothing new the second time


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_make_vertical(tmp_path):
    src = tmp_path / "source.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=1280x720:d=1",
                    "-f", "lavfi", "-i", "sine=d=1", "-shortest", str(src)], check=True)
    out = ccvideo.make_vertical(src, tmp_path / "video.mp4", "Titular de prueba", "Video: Autor (CC BY) · https://youtu.be/x")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                            "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout.strip()
    assert probe == "1080,1920"


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}
        self.content = json.dumps(self._payload).encode()
        self.text = self.content.decode()

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def request(self, method, url, **kw):
        self.calls.append((method, url, kw))
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected {method} {url}")

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)


def test_x_post_with_video_and_quote(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 100)
    s = FakeSession({
        "/initialize": FakeResp(payload={"data": {"id": "m1"}}),
        "/append": FakeResp(payload={}),
        "/finalize": FakeResp(payload={"data": {"processing_info": {"state": "succeeded"}}}),
        "/tweets": FakeResp(payload={"data": {"id": "t1"}}),
    })
    cfg = make_cfg(tmp_path, x_api_key="a", x_api_secret="b", x_access_token="c", x_access_secret="d")
    x = XPublisher(cfg, s)
    assert x.post("hola", video) == "t1"
    assert s.calls[-1][2]["json"] == {"text": "hola", "media": {"media_ids": ["m1"]}}
    x.post("mira", quote_id="99")
    assert s.calls[-1][2]["json"] == {"text": "mira", "quote_tweet_id": "99"}


def test_tiktok_post_flow(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 1000)
    ok = {"error": {"code": "ok"}}
    s = FakeSession({
        "/oauth/token/": FakeResp(payload={"access_token": "at", "refresh_token": "rt"}),
        "creator_info": FakeResp(payload={**ok, "data": {"privacy_level_options": ["SELF_ONLY"]}}),
        "/video/init/": FakeResp(payload={**ok, "data": {"publish_id": "p1", "upload_url": "https://up/x"}}),
        "https://up/": FakeResp(status=201),
        "/status/fetch/": FakeResp(payload={**ok, "data": {"status": "PUBLISH_COMPLETE"}}),
    })
    cfg = make_cfg(tmp_path, tiktok_client_key="k", tiktok_client_secret="s", tiktok_refresh_token="rt",
                   tiktok_privacy="PUBLIC_TO_EVERYONE")
    assert TikTokPublisher(cfg, s).post(video, "cap #GTA6") == "p1"
    init = next(c for c in s.calls if "/video/init/" in c[1])[2]["json"]
    assert init["post_info"]["privacy_level"] == "SELF_ONLY"
    put = next(c for c in s.calls if c[0] == "PUT")[2]
    assert put["headers"]["Content-Range"] == "bytes 0-999/1000"
