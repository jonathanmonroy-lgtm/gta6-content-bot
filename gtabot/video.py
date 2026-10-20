"""Creation: render a vertical 1080x1920 short in a news-short format.

Format ("radar"):
  * 0-3 s   hook: big headline that pops in over moving footage, plus a NOTICIA/RUMOR badge
  * body    word-by-word captions synced to the narration, one b-roll shot per scene,
            persistent title at the top and a progress bar
  * end     question + follow call to action
Everything stays inside TikTok's safe zone (top 180 px and bottom 420 px are covered by the UI).
Footage comes from Pexels stock (or an animated gradient fallback); nothing from Rockstar.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw

from .broll import Broll
from .config import ROOT
from .writer import Content

log = logging.getLogger(__name__)

W, H, FPS = 1080, 1920, 30
FONTS_DIR = ROOT / "assets" / "fonts"
MUSIC_DIR = ROOT / "assets" / "music"
GAP = 0.12  # breath between scenes
BADGE_COLORS = {  # ASS colours are &HBBGGRR
    "NOTICIA": "&H00A02DFF", "CONFIRMADO": "&H0060C000", "RUMOR": "&H0000A5FF",
    "FILTRACIÓN": "&H003030E0", "DATO": "&H00D1C200", "TEORÍA": "&H00F03A8A",
}
YELLOW, WHITE = "&H004DE1FF", "&H00FFFFFF"


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    audio: Path
    duration: float
    words: list[Word]


# Narration -----------------------------------------------------------------

async def _tts(text: str, voice: str, path: Path) -> list[Word]:
    import edge_tts

    words = []
    comm = edge_tts.Communicate(text, voice, rate="+12%", boundary="WordBoundary")
    with path.open("wb") as fh:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                fh.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append(Word(start, start + chunk["duration"] / 1e7, chunk["text"]))
    return words


def _even_words(text: str, duration: float) -> list[Word]:
    tokens = text.split()
    step = duration / max(1, len(tokens))
    return [Word(i * step, (i + 1) * step, t) for i, t in enumerate(tokens)]


def _attach_punctuation(words: list[Word], text: str) -> list[Word]:
    """edge-tts drops punctuation; put it back so captions read naturally."""
    tokens = text.split()
    if len(tokens) != len(words):
        return words
    return [Word(w.start, w.end, tok) for w, tok in zip(words, tokens)]


def narrate(text: str, voice: str, path: Path, allow_silent: bool) -> Segment:
    try:
        words = asyncio.run(_tts(text, voice, path))
        duration = _duration(path)
        return Segment(path, duration, _attach_punctuation(words, text) or _even_words(text, duration))
    except Exception as exc:  # network or service failure
        if not allow_silent:
            raise
        log.warning("TTS unavailable (%s); using a silent track for the dry run", exc)
        silent = path.with_suffix(".wav")
        duration = max(2.0, len(text.split()) / 2.8)
        _ffmpeg(["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", f"{duration:.2f}", str(silent)])
        return Segment(silent, duration, _even_words(text, duration))


# ffmpeg helpers ------------------------------------------------------------

def _ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-1500:]}")


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


ENCODE = ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-maxrate", "8M", "-bufsize", "16M",
          "-pix_fmt", "yuv420p", "-r", str(FPS)]


def background(source: Path | None, kind: str, seconds: float, index: int, out: Path) -> None:
    grade = "eq=saturation=1.18:contrast=1.06"
    if kind == "video":
        direction = "(iw-ow)*t/{d}" if index % 2 == 0 else "(iw-ow)*(1-t/{d})"
        vf = (
            f"scale={int(W * 1.12)}:{int(H * 1.12)}:force_original_aspect_ratio=increase,"
            f"crop={int(W * 1.12)}:{int(H * 1.12)},"
            f"crop={W}:{H}:x='{direction.format(d=seconds)}':y='(ih-oh)/2',"
            f"{grade},fps=30,setsar=1"
        )
        _ffmpeg(["-stream_loop", "-1", "-i", str(source), "-t", f"{seconds:.3f}", "-vf", vf, "-an", *ENCODE, str(out)])
    elif kind == "image":
        # Ken Burns: slow push in (or out on odd scenes), rendered at 2x to avoid zoompan jitter
        frames = int(seconds * FPS) + 1
        zoom = f"1+0.14*on/{frames}" if index % 2 == 0 else f"1.14-0.14*on/{frames}"
        vf = (
            f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
            f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
            f"{grade},setsar=1"
        )
        _ffmpeg(["-loop", "1", "-i", str(source), "-t", f"{seconds:.3f}", "-vf", vf, *ENCODE, str(out)])
    else:
        src = (
            f"gradients=s={W}x{H}:r={FPS}:n=4:c0=0x14062b:c1=0xff2d95:c2=0x00b8d4:c3=0x3a0ca3:"
            f"speed=0.02:seed={index * 7 + 3}:d={seconds:.3f}"
        )
        _ffmpeg(["-f", "lavfi", "-i", src, "-vf", "vignette=PI/4,setsar=1", *ENCODE, str(out)])


def shade(path: Path) -> None:
    """Dark gradients top and bottom so white text always reads over bright footage."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        top = max(0.0, 1 - y / 760) * 150
        bottom = max(0.0, (y - 950) / (H - 950)) * 190
        draw.line([(0, y), (W, y)], fill=(8, 4, 20, int(35 + top + bottom)))
    img.save(path)


# Captions (ASS) --------------------------------------------------------------

def _ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").upper()


def _chunks(words: list[Word], size: int = 3) -> list[list[Word]]:
    chunks, current = [], []
    for w in words:
        current.append(w)
        if len(current) == size or re.search(r"[.,!?;:]$", w.text):
            chunks.append(current)
            current = []
    if current:
        chunks.append(current)
    return chunks


ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Anton,124,{WHITE},{WHITE},&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,7,4,5,90,90,0,1
Style: Hook,Anton,138,{WHITE},{WHITE},&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,9,6,5,70,70,0,1
Style: Title,Anton,80,{WHITE},{WHITE},&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,5,3,8,80,80,0,1
Style: Badge,Anton,62,{WHITE},{WHITE},&H00A02DFF,&H00000000,0,0,0,0,100,100,3,0,3,16,0,8,0,0,0,1
Style: Brand,Anton,46,&H33FFFFFF,{WHITE},&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,3,0,8,0,0,0,1
Style: CTA,Anton,84,{YELLOW},{WHITE},&H00000000,&H80000000,0,0,0,0,100,100,1,0,1,7,4,5,80,80,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_ass(content: Content, segments: list[Segment], handle: str) -> str:
    total = sum(s.duration + GAP for s in segments)
    hook_end = min(3.0, segments[0].duration + GAP)
    badge = BADGE_COLORS.get(content.label, BADGE_COLORS["NOTICIA"])
    events = [
        # brand + badge for the whole video
        f"Dialogue: 2,{_ts(0)},{_ts(total)},Brand,,0,0,0,,{{\\pos(540,205)}}GTA VI RADAR · FAN NO OFICIAL",
        f"Dialogue: 2,{_ts(0)},{_ts(total)},Badge,,0,0,0,,{{\\pos(540,290)\\3c{badge}"
        f"\\fscx60\\fscy60\\t(0,220,\\fscx100\\fscy100)}} {_esc(content.label)} ",
        # hook pops in big, then the title stays small at the top
        f"Dialogue: 3,{_ts(0)},{_ts(hook_end)},Hook,,0,0,0,,{{\\pos(540,720)\\fscx55\\fscy55"
        f"\\t(0,180,\\fscx108\\fscy108)\\t(180,300,\\fscx100\\fscy100)\\fad(0,200)}}{_esc(content.hook)}",
        f"Dialogue: 2,{_ts(hook_end)},{_ts(total)},Title,,0,0,0,,{{\\pos(540,400)\\fad(250,0)}}{_esc(content.title)}",
        f"Dialogue: 2,{_ts(total - 2.6)},{_ts(total)},CTA,,0,0,0,,{{\\pos(540,1420)\\fad(200,0)"
        f"\\fscx70\\fscy70\\t(0,200,\\fscx100\\fscy100)}}SÍGUEME {_esc(handle)}",
    ]
    offset = 0.0
    for seg in segments:
        starts = [w.start for w in seg.words[1:]] + [seg.duration + GAP]
        next_start = {id(w): nxt for w, nxt in zip(seg.words, starts)}
        for chunk in _chunks(seg.words):
            for i, word in enumerate(chunk):
                start = offset + word.start
                hold = word.end + 0.25 if i + 1 == len(chunk) else next_start[id(word)]
                end = offset + min(hold, next_start[id(word)])  # never overlap the next caption
                parts = []
                for j, w in enumerate(chunk):
                    if j == i:
                        parts.append(f"{{\\c{YELLOW}\\fscx112\\fscy112}}{_esc(w.text)}{{\\c{WHITE}\\fscx100\\fscy100}}")
                    else:
                        parts.append(_esc(w.text))
                pop = "\\fscx85\\fscy85\\t(0,90,\\fscx100\\fscy100)" if i == 0 else ""
                events.append(
                    f"Dialogue: 1,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{{\\pos(540,1130){pop}}}{' '.join(parts)}"
                )
        offset += seg.duration + GAP
    return ASS_HEADER + "\n".join(events) + "\n"


# Assembly ------------------------------------------------------------------

def build_video(content: Content, handle: str, voice: str, workdir: Path,
                allow_silent: bool = False, pexels_key: str | None = None,
                gemini_key: str | None = None, use_pollinations: bool = True) -> Path:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to render videos")
    workdir.mkdir(parents=True, exist_ok=True)
    broll = Broll(pexels_key, workdir, gemini_key=gemini_key, use_pollinations=use_pollinations)

    segments, backgrounds, audios = [], [], []
    for i, scene in enumerate(content.scenes):
        seg = narrate(scene.narration, voice, workdir / f"voice{i}.mp3", allow_silent)
        seconds = seg.duration + GAP
        segments.append(seg)

        bg = workdir / f"bg{i}.mp4"
        source, kind = broll.visual(scene.broll, seconds, f"clip{i}", seed=random.randint(1, 10**6))
        background(source, kind, seconds, i, bg)
        backgrounds.append(bg)

        wav = workdir / f"voice{i}.wav" if seg.audio.suffix != ".wav" else seg.audio.with_name(f"pad{i}.wav")
        _ffmpeg(["-i", str(seg.audio), "-af", "apad", "-t", f"{seconds:.3f}", "-ar", "44100", "-ac", "2", str(wav)])
        audios.append(wav)

    for name, files in (("bg.txt", backgrounds), ("voice.txt", audios)):
        (workdir / name).write_text("".join(f"file '{f.name}'\n" for f in files), encoding="utf-8")
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(workdir / "bg.txt"), "-c", "copy", str(workdir / "bg.mp4")])
    _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(workdir / "voice.txt"), "-c", "copy", str(workdir / "voice.wav")])

    total = sum(s.duration + GAP for s in segments)
    ass = workdir / "captions.ass"
    ass.write_text(build_ass(content, segments, handle), encoding="utf-8")
    shade(workdir / "shade.png")

    inputs = ["-i", str(workdir / "bg.mp4"), "-i", str(workdir / "shade.png"), "-i", str(workdir / "voice.wav"),
              "-f", "lavfi", "-i", f"color=c=0xff2d95:s={W}x10:r={FPS}:d={total:.3f}"]
    audio = "[2:a]loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
    music = sorted(MUSIC_DIR.glob("*.mp3")) if MUSIC_DIR.exists() else []
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(random.choice(music))]
        audio = ("[4:a]volume=0.10,afade=t=out:st={0:.2f}:d=1.5[m];[2:a][m]amix=inputs=2:duration=first:normalize=0,"
                 "loudnorm=I=-14:TP=-1.5:LRA=11[aout]").format(max(0.0, total - 1.5))

    fonts = str(FONTS_DIR).replace(":", "\\:")
    video = (
        f"[0:v][1:v]overlay=0:0[v1];"
        f"[v1][3:v]overlay=x='(t/{total:.3f}-1)*w':y=0:eval=frame[v2];"
        f"[v2]ass={ass.name}:fontsdir={fonts},format=yuv420p[vout]"
    )
    final = workdir / "video.mp4"
    proc = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
         "-filter_complex", f"{video};{audio}", "-map", "[vout]", "-map", "[aout]", "-t", f"{total:.3f}",
         *ENCODE, "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", final.name],
        cwd=workdir, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-1500:]}")
    for f in workdir.iterdir():  # keep only the final video, the captions and the json files
        if f.suffix in {".wav", ".mp3", ".png", ".jpg", ".txt"} or (f.suffix == ".mp4" and f != final):
            f.unlink()
    log.info("Video rendered: %s (%.1fs)", final, _duration(final))
    return final
