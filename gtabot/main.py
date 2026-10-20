"""One bot run: extract -> transform -> create -> publish.

Run with ``python -m gtabot``. GitHub Actions calls this on a schedule.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone

from .config import Config
from .publishers.tiktok import TikTokPublisher
from .publishers.x import XPublisher
from .sources import fetch_items
from .state import State
from .video import build_video
from .writer import Content, generate

log = logging.getLogger("gtabot")
TWEET_LIMIT = 280
URL_LENGTH = 23  # X counts every link as 23 characters


def compose_tweet(content: Content) -> str:
    text = content.tweet.strip()
    if content.source_url and len(text) + 1 + URL_LENGTH <= TWEET_LIMIT:
        text = f"{text}\n{content.source_url}"
    return text


def render_sample(cfg: Config, path: str) -> dict:
    """Render a hand-written script, to preview the video format without publishing."""
    content = Content.model_validate_json(open(path, encoding="utf-8").read())
    run_dir = cfg.output_dir / "sample"
    video = build_video(content, cfg.handle, cfg.voice, run_dir, allow_silent=allow_silent(cfg),
                        pexels_key=cfg.pexels_api_key, gemini_key=cfg.gemini_api_key,
                        use_pollinations=cfg.use_pollinations)
    (run_dir / "content.json").write_text(content.model_dump_json(indent=2), encoding="utf-8")
    return {"status": "sample", "video": str(video)}


def allow_silent(cfg: Config) -> bool:
    # a silent video is fine for local dry runs, never on the real runner
    return cfg.dry_run and not os.getenv("GITHUB_ACTIONS")


def run(cfg: Config, *, force_video: bool = False, ignore_seen: bool = False) -> dict:
    state = State(cfg.state_path)
    posts_today = state.count_since("x_id")
    videos_today = state.count_since("video")
    if posts_today >= cfg.max_posts_per_day and not cfg.dry_run:
        log.info("Daily post limit reached (%d)", posts_today)
        return {"status": "limit"}

    items = [i for i in fetch_items(cfg.feeds) if ignore_seen or not state.is_seen(i.id)]
    log.info("%d new items", len(items))

    content = generate(cfg, items, state.recent_topics())
    for item in items:  # everything we showed the writer counts as used
        state.mark_seen(item.id)
    if not content.publish:
        log.info("Nothing worth publishing this run")
        state.save()
        return {"status": "skipped"}

    run_dir = cfg.output_dir / datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "content.json").write_text(content.model_dump_json(indent=2), encoding="utf-8")

    want_video = bool(content.scenes) and (force_video or videos_today < cfg.max_videos_per_day)
    video = None
    if want_video:
        video = build_video(content, cfg.handle, cfg.voice, run_dir,
                            allow_silent=allow_silent(cfg), pexels_key=cfg.pexels_api_key, gemini_key=cfg.gemini_api_key,
                        use_pollinations=cfg.use_pollinations)

    tweet = compose_tweet(content)
    result = {"status": "ok", "topic": content.topic, "mode": content.mode, "dir": str(run_dir)}

    if cfg.dry_run or not cfg.x_enabled:
        log.info("[dry-run] X post:\n%s", tweet)
    else:
        result["x_id"] = XPublisher(cfg).post(tweet, video if cfg.x_attach_video else None)

    if video:
        result["video"] = True
        if cfg.dry_run or not cfg.tiktok_enabled:
            log.info("[dry-run] TikTok video %s with caption: %s", video, content.tiktok_caption)
        else:
            try:
                result["tiktok_id"] = TikTokPublisher(cfg).post(video, content.tiktok_caption)
            except Exception:
                log.exception("TikTok publish failed")
                result["tiktok_error"] = True

    if not cfg.dry_run:
        state.record({k: v for k, v in result.items() if k != "dir"})
    state.save()
    (run_dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GTA VI content bot")
    parser.add_argument("--dry-run", action="store_true", help="never publish, only render")
    parser.add_argument("--live", action="store_true", help="publish (overrides DRY_RUN env)")
    parser.add_argument("--force-video", action="store_true")
    parser.add_argument("--ignore-seen", action="store_true", help="reuse news already covered (for tests)")
    parser.add_argument("--sample", metavar="JSON", help="only render this script as a video, never publish")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config()
    if args.dry_run:
        cfg.dry_run = True
    if args.live:
        cfg.dry_run = False
    log.info("Mode: %s | X: %s | TikTok: %s | Claude: %s",
             "DRY-RUN" if cfg.dry_run else "LIVE", cfg.x_enabled, cfg.tiktok_enabled, bool(cfg.anthropic_api_key))
    if args.sample:
        result = render_sample(cfg, args.sample)
    else:
        result = run(cfg, force_video=args.force_video, ignore_seen=args.ignore_seen)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
