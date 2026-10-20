"""Creation: render a vertical 1080x1920 narrated video from the script.

Visuals are generated from scratch (gradient background + text), so no
Rockstar footage or artwork is reused. Narration comes from edge-tts.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .writer import Content

log = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
PINK, TEAL, NIGHT = (255, 64, 160), (0, 214, 214), (18, 10, 40)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _background() -> Image.Image:
    img = Image.new("RGB", (W, H), NIGHT)
    draw = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        # night sky fading into a pink/teal Vice City sunset
        r = int(NIGHT[0] + (PINK[0] - NIGHT[0]) * t**1.6)
        g = int(NIGHT[1] + (TEAL[1] * 0.35 - NIGHT[1]) * t**2)
        b = int(NIGHT[2] + (PINK[2] - NIGHT[2]) * t**1.2)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    for i, y in enumerate(range(int(H * 0.72), H, 46)):
        draw.line([(0, y), (W, y)], fill=TEAL, width=2 + i // 3)
    return img


def _centered(draw: ImageDraw.ImageDraw, lines: list[str], font, y: int, fill, gap: int = 18) -> int:
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font, stroke_width=4)
        x = (W - (box[2] - box[0])) // 2
        draw.text((x, y), line, font=font, fill=fill, stroke_width=4, stroke_fill=(0, 0, 0))
        y += box[3] - box[1] + gap
    return y


def render_frame(title: str, text: str, handle: str, index: int, total: int, path: Path) -> None:
    img = _background()
    draw = ImageDraw.Draw(img)
    _centered(draw, ["GTA VI · NOTICIAS"], _font(54), 150, TEAL)
    _centered(draw, textwrap.wrap(title.upper(), 18), _font(64), 250, (255, 255, 255))

    body_font = _font(92)
    lines = textwrap.wrap(text, 16)
    line_h = draw.textbbox((0, 0), "Ag", font=body_font)[3] + 22
    _centered(draw, lines, body_font, (H - line_h * len(lines)) // 2, (255, 255, 255), gap=22)

    bar_w = int(W * 0.8 * (index + 1) / total)
    draw.rounded_rectangle([W * 0.1, H - 260, W * 0.1 + bar_w, H - 245], 8, fill=PINK)
    _centered(draw, [handle, "Cuenta de fans · no oficial"], _font(42), H - 220, (255, 255, 255))
    img.save(path)


async def _tts(text: str, voice: str, path: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, voice, rate="+8%").save(str(path))


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def _silence(seconds: float, path: Path) -> None:
    _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", f"{seconds:.2f}", str(path)])


def _ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True)


def narrate(text: str, voice: str, path: Path, allow_silent: bool) -> Path:
    try:
        asyncio.run(_tts(text, voice, path))
        return path
    except Exception as exc:  # network or service failure
        if not allow_silent:
            raise
        log.warning("TTS unavailable (%s); using silent track for the dry run", exc)
        silent = path.with_suffix(".wav")
        _silence(max(2.0, len(text.split()) / 2.6), silent)
        return silent


def build_video(content: Content, handle: str, voice: str, workdir: Path, allow_silent: bool = False) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render videos")
    workdir.mkdir(parents=True, exist_ok=True)
    segments = []
    total = len(content.scenes)
    for i, scene in enumerate(content.scenes):
        frame = workdir / f"scene{i}.png"
        render_frame(content.title, scene.on_screen, handle, i, total, frame)
        audio = narrate(scene.narration, voice, workdir / f"scene{i}.mp3", allow_silent)
        seconds = _duration(audio) + 0.35
        frames = int(seconds * FPS)
        segment = workdir / f"scene{i}.mp4"
        zoom = f"zoompan=z='min(zoom+0.0007,1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS}"
        _ffmpeg([
            "-loop", "1", "-framerate", str(FPS), "-i", str(frame), "-i", str(audio),
            "-vf", f"{zoom},format=yuv420p", "-af", "apad", "-t", f"{seconds:.2f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2", str(segment),
        ])
        segments.append(segment)

    listing = workdir / "segments.txt"
    listing.write_text("".join(f"file '{s.name}'\n" for s in segments), encoding="utf-8")
    final = workdir / "video.mp4"
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(final)])
    log.info("Video rendered: %s (%.1fs)", final, _duration(final))
    return final
