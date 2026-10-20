"""One bot run.

1. X: pick the best new post/video/news about GTA VI and quote or share it with a comment.
2. Video: find a Creative Commons (CC BY) GTA VI video on YouTube, cut a vertical clip with
   the author's credit and publish it on TikTok and X.

Run with ``python -m gtabot``. GitHub Actions calls this on a schedule.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import ccvideo
from .config import Config
from .publishers.tiktok import TikTokPublisher
from .publishers.x import XPublisher
from .sources import Item, fetch_items
from .state import State
from .writer import XPost, write_caption, write_post

log = logging.getLogger("gtabot")
TWEET_LIMIT = 280
URL_LENGTH = 23  # X counts every link as 23 characters


def compose_tweet(post: XPost, item: Item | None) -> tuple[str, str | None]:
    """Text and quote id. Posts on X are quoted; anything else gets its link appended."""
    text = post.text.strip()
    if item and item.tweet_id:
        return text, item.tweet_id
    if item and item.url and len(text) + 1 + URL_LENGTH <= TWEET_LIMIT:
        text = f"{text}\n{item.url}"
    return text, None


def share_on_x(cfg: Config, state: State, run_dir: Path) -> dict:
    items = [i for i in fetch_items(cfg.feeds, cfg.youtube_channels) if not state.is_seen(i.id)]
    log.info("%d new items (%d posts on X, %d YouTube videos)", len(items),
             sum(i.kind == "tweet" for i in items), sum(i.kind == "youtube" for i in items))
    post = write_post(cfg, items, state.recent_topics())
    for item in items:  # everything the writer saw counts as used
        state.mark_seen(item.id)
    if not post.publish:
        return {"x": "skipped"}
    item = next((i for i in items if i.id == post.item_id), None)
    text, quote_id = compose_tweet(post, item)
    (run_dir / "x_post.json").write_text(json.dumps({"text": text, "quote": quote_id}, ensure_ascii=False), encoding="utf-8")
    result = {"topic": post.topic, "x_source": item.url if item else None}
    if cfg.dry_run or not cfg.x_enabled:
        log.info("[dry-run] X post (quote=%s):\n%s", quote_id, text)
    else:
        result["x_id"] = XPublisher(cfg).post(text, quote_id=quote_id)
    return result


def share_video(cfg: Config, state: State, run_dir: Path) -> dict:
    seen = set(state.data["seen"])
    video = ccvideo.find(cfg.cc_queries, seen)
    if not video:
        log.info("No new Creative Commons video found")
        return {"video": "none"}
    state.mark_seen(f"cc{video.id}")
    caption = write_caption(cfg, video, state.recent_topics())
    if not caption.publish:
        log.info("Writer rejected video %s", video.url)
        return {"video": "rejected"}
    source = ccvideo.download(video, run_dir)
    final = ccvideo.make_vertical(source, run_dir / "video.mp4", caption.headline, video.credit)
    source.unlink(missing_ok=True)

    tiktok_caption = f"{caption.tiktok_caption}\n\n{video.credit}"
    x_text = f"{caption.x_text}\n🎥 {video.author} (CC BY) {video.url}"
    (run_dir / "video.json").write_text(json.dumps({"tiktok": tiktok_caption, "x": x_text, "source": video.url},
                                                   ensure_ascii=False, indent=2), encoding="utf-8")
    result = {"video": True, "video_source": video.url}
    if cfg.dry_run or not cfg.tiktok_enabled:
        log.info("[dry-run] TikTok video %s:\n%s", final, tiktok_caption)
    else:
        try:
            result["tiktok_id"] = TikTokPublisher(cfg).post(final, tiktok_caption)
        except Exception:
            log.exception("TikTok publish failed")
            result["tiktok_error"] = True
    if cfg.dry_run or not cfg.x_enabled:
        log.info("[dry-run] X video post:\n%s", x_text)
    else:
        result["x_video_id"] = XPublisher(cfg).post(x_text, video=final)
    return result


def run(cfg: Config, *, force_video: bool = False, video_only: bool = False) -> dict:
    state = State(cfg.state_path)
    run_dir = cfg.output_dir / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    result: dict = {"status": "ok"}

    if not video_only and (cfg.dry_run or state.count_since("x_id") < cfg.max_posts_per_day):
        try:
            result.update(share_on_x(cfg, state, run_dir))
        except Exception:
            log.exception("X share failed")
            result["x_error"] = True

    if force_video or state.count_since("video") < cfg.max_videos_per_day:
        try:
            result.update(share_video(cfg, state, run_dir))
        except Exception:
            log.exception("Video share failed")
            result["video_error"] = True

    if not cfg.dry_run:
        state.record(result)
    state.save()
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GTA VI content bot")
    parser.add_argument("--dry-run", action="store_true", help="never publish")
    parser.add_argument("--live", action="store_true", help="publish (overrides DRY_RUN env)")
    parser.add_argument("--force-video", action="store_true", help="ignore the daily video limit")
    parser.add_argument("--video-only", action="store_true", help="skip the X share step")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config()
    if args.dry_run:
        cfg.dry_run = True
    if args.live:
        cfg.dry_run = False
    log.info("Mode: %s | X: %s | TikTok: %s | Claude: %s",
             "DRY-RUN" if cfg.dry_run else "LIVE", cfg.x_enabled, cfg.tiktok_enabled, bool(cfg.anthropic_api_key))
    result = run(cfg, force_video=args.force_video, video_only=args.video_only)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
