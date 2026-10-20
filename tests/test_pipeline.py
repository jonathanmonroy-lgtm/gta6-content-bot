import json
import shutil
from pathlib import Path

import pytest

from gtabot import main as bot
from gtabot.config import Config
from gtabot.publishers.tiktok import TikTokPublisher
from gtabot.publishers.x import XPublisher
from gtabot.sources import parse_feed
from gtabot.state import State
from gtabot.writer import Content, Scene, build_prompt

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Noticias</title>
<item><title>Rockstar muestra nuevo tráiler de GTA 6</title><link>https://ex.com/a</link>
<description>&lt;b&gt;Lucia&lt;/b&gt; y Jason en Leonida</description>
<pubDate>Mon, 20 Oct 2026 10:00:00 GMT</pubDate></item>
<item><title>Receta de pan casero</title><link>https://ex.com/b</link></item>
</channel></rss>"""


def make_cfg(tmp_path: Path, **kw) -> Config:
    cfg = Config()
    cfg.state_path = tmp_path / "state.json"
    cfg.output_dir = tmp_path / "out"
    cfg.anthropic_api_key = None
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def test_parse_feed_keeps_only_relevant_items():
    items = parse_feed(RSS, "https://feed")
    assert [i.title for i in items] == ["Rockstar muestra nuevo tráiler de GTA 6"]
    assert items[0].summary == "Lucia y Jason en Leonida"
    assert items[0].published.year == 2026


def test_state_roundtrip_and_counts(tmp_path):
    s = State(tmp_path / "s.json")
    s.mark_seen("abc")
    s.record({"x_id": "1", "video": True, "topic": "trailer"})
    s.save()
    s2 = State(tmp_path / "s.json")
    assert s2.is_seen("abc")
    assert s2.count_since("x_id") == 1 and s2.count_since("video") == 1
    assert s2.recent_topics() == ["trailer"]


def test_compose_tweet_adds_link_only_when_it_fits():
    c = Content(publish=True, mode="news", source_item_id=None, topic="t", tweet="Hola #GTA6",
                tiktok_caption="", label="NOTICIA", hook="", title="", scenes=[], source_url="https://ex.com/a")
    assert bot.compose_tweet(c).endswith("https://ex.com/a")
    c.tweet = "x" * 270
    assert bot.compose_tweet(c) == "x" * 270


def test_prompt_mentions_evergreen_when_no_items():
    assert "No hay noticias nuevas" in build_prompt([], True)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg missing")
def test_dry_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "fetch_items", lambda feeds: parse_feed(RSS, "https://feed"))
    monkeypatch.setattr("gtabot.video.asyncio.run", _no_network)
    cfg = make_cfg(tmp_path, dry_run=True)
    result = bot.run(cfg)
    assert result["status"] == "ok" and result["video"]
    video = Path(result["dir"]) / "video.mp4"
    assert video.stat().st_size > 10_000
    # the same item is not used twice
    assert bot.run(cfg)["status"] == "skipped"


def _no_network(coro):
    coro.close()
    raise OSError("offline")


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


def test_x_post_with_video(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"0" * 100)
    s = FakeSession({
        "/initialize": FakeResp(payload={"data": {"id": "m1"}}),
        "/append": FakeResp(payload={}),
        "/finalize": FakeResp(payload={"data": {"processing_info": {"state": "succeeded"}}}),
        "/tweets": FakeResp(payload={"data": {"id": "t1"}}),
    })
    cfg = make_cfg(tmp_path, x_api_key="a", x_api_secret="b", x_access_token="c", x_access_secret="d")
    assert XPublisher(cfg, s).post("hola", video) == "t1"
    assert s.calls[-1][2]["json"] == {"text": "hola", "media": {"media_ids": ["m1"]}}


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
    assert init["source_info"]["total_chunk_count"] == 1
    put = next(c for c in s.calls if c[0] == "PUT")[2]
    assert put["headers"]["Content-Range"] == "bytes 0-999/1000"
