"""A YouTube video: its metadata and a transcript with timestamps, so that a
claim drawn from it can point at the moment it was said.

Captions come from YouTube through yt-dlp without downloading the video:
uploaded ones first, then automatic ones. ``--transcribe whisper`` downloads
the audio instead and runs Whisper locally (mlx-whisper on Apple silicon, or
faster-whisper), for videos without captions or when the captions are poor.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from bibliotheke.fetch import FetchError, Fetched, Options, claimed, name_id, source_path
from bibliotheke.fetch import http

kind = "youtube"
type = "video"

model: dict[str, Any] = {
    "nodes": {
        "video": {
            "description": "A YouTube video, such as a podcast episode or a talk.",
            "name": "The video's title.",
            "body": "The description, then notes on what it says.",
            "facts": {
                "url": {"type": "url", "required": True, "description": "The video on YouTube."},
                "youtube_id": {"type": "text", "description": "YouTube's id for the video."},
                "channel": {"type": "text", "description": "The channel that published it."},
                "published": {"type": "time", "description": "When it was published."},
                "duration": {"type": "int", "description": "Length in seconds."},
                "views": {"type": "int", "description": "View count when last fetched."},
                "file": {"type": "file", "description": "The transcript, with a timestamp and link per paragraph."},
            },
        },
    },
    "edges": {},
}

_URL = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#]*&)?v=|shorts/|live/|embed/|v/)|youtu\.be/)([A-Za-z0-9_-]{11})")
PARAGRAPH_SECONDS = 45  # a transcript paragraph covers about this long
WHISPER_MODEL = "mlx-community/whisper-large-v3-turbo"


@dataclass
class Cue:
    start: float  # seconds
    text: str


def key(url: str) -> str | None:
    m = _URL.search(url)
    return m[1] if m else None


def info(video_id: str) -> dict[str, Any]:
    """The video's metadata from yt-dlp, caption tracks included, no download."""
    try:
        import yt_dlp
    except ImportError:
        raise FetchError("fetching from YouTube needs yt-dlp: `pip install 'bibliotheke[fetch]'`") from None
    settings = {"quiet": True, "no_warnings": True, "skip_download": True, "noprogress": True}
    try:
        with yt_dlp.YoutubeDL(settings) as ydl:
            return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except yt_dlp.utils.DownloadError as error:
        raise FetchError(f"yt-dlp: {error}") from None


def caption_track(meta: dict[str, Any], lang: str) -> tuple[str, str] | None:
    """The URL of the json3 caption track to read and what it is: an uploaded
    track in the language, else an automatic one, matching ``en`` before
    ``en-US`` and the like."""
    for field, how in (("subtitles", "captions"), ("automatic_captions", "automatic captions")):
        tracks = meta.get(field) or {}
        names = [lang] + sorted(n for n in tracks if n.startswith(lang + "-") or n == lang + "-orig")
        for name in names:
            for fmt in tracks.get(name) or []:
                if fmt.get("ext") == "json3" and fmt.get("url"):
                    return fmt["url"], how
    return None


def parse_json3(data: dict[str, Any]) -> list[Cue]:
    """Cues from YouTube's json3 caption format: one per event with text."""
    cues = []
    for event in data.get("events") or []:
        text = "".join(seg.get("utf8", "") for seg in event.get("segs") or [])
        text = " ".join(text.split())
        if text:
            cues.append(Cue(event.get("tStartMs", 0) / 1000, text))
    return cues


def captions(meta: dict[str, Any], lang: str) -> tuple[list[Cue], str] | None:
    track = caption_track(meta, lang)
    if track is None:
        return None
    url, how = track
    return parse_json3(json.loads(http.get_text(url))), how


def whisper(video_id: str, cache: Path) -> list[Cue]:
    """Transcribe the audio locally. Downloads to ``cache``, which is not
    part of the graph."""
    audio = download_audio(video_id, cache)
    try:
        import mlx_whisper

        result = mlx_whisper.transcribe(str(audio), path_or_hf_repo=WHISPER_MODEL)
        segments = result["segments"]
    except ImportError:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise FetchError("transcribing needs mlx-whisper (Apple silicon) or faster-whisper: `pip install mlx-whisper`") from None
        found, _ = WhisperModel("large-v3-turbo").transcribe(str(audio))
        segments = [{"start": s.start, "text": s.text} for s in found]
    return [Cue(float(s["start"]), " ".join(str(s["text"]).split())) for s in segments if str(s["text"]).strip()]


def download_audio(video_id: str, cache: Path) -> Path:
    try:
        import yt_dlp
    except ImportError:
        raise FetchError("fetching from YouTube needs yt-dlp: `pip install 'bibliotheke[fetch]'`") from None
    cache.mkdir(parents=True, exist_ok=True)
    for found in cache.glob(f"{video_id}.*"):
        return found
    settings = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(cache / f"{video_id}.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(settings) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    except yt_dlp.utils.DownloadError as error:
        raise FetchError(f"yt-dlp: {error}") from None
    for found in cache.glob(f"{video_id}.*"):
        return found
    raise FetchError(f"yt-dlp downloaded nothing for {video_id}")


def stamp(seconds: float) -> str:
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def transcript(video_id: str, meta: dict[str, Any], cues: list[Cue], how: str) -> str:
    """The transcript as Markdown: a header, then a paragraph about every
    ``PARAGRAPH_SECONDS``, each opening with a timestamp that links to that
    moment. Chapters become headings."""
    title = meta.get("title") or video_id
    lines = [f"# {title}", ""]
    channel = meta.get("channel") or meta.get("uploader")
    if channel:
        lines.append(f"- Channel: {channel}")
    if meta.get("upload_date"):
        lines.append(f"- Published: {_date(meta['upload_date'])}")
    if meta.get("duration"):
        lines.append(f"- Duration: {stamp(meta['duration'])}")
    lines += [f"- Source: https://www.youtube.com/watch?v={video_id}", f"- Transcript: {how}", ""]
    chapters = sorted((c for c in meta.get("chapters") or [] if c.get("title")), key=lambda c: c.get("start_time") or 0)
    paragraph: list[str] = []
    opened = 0.0

    def flush() -> None:
        if paragraph:
            lines.append(f"[{stamp(opened)}](https://youtu.be/{video_id}?t={int(opened)}) " + " ".join(paragraph))
            lines.append("")
            paragraph.clear()

    for cue in cues:
        while chapters and cue.start >= (chapters[0].get("start_time") or 0):
            flush()
            lines += [f"## {chapters.pop(0)['title']}", ""]
        if not paragraph:
            opened = cue.start
        elif cue.start - opened >= PARAGRAPH_SECONDS and paragraph[-1].endswith((".", "?", "!")):
            flush()
            opened = cue.start
        paragraph.append(cue.text)
    flush()
    return "\n".join(lines).rstrip() + "\n"


def _date(raw: str) -> str | None:
    """yt-dlp's YYYYMMDD as a date."""
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if raw and len(raw) == 8 and raw.isdigit() else None


def fetch(key: str, options: Options) -> Fetched:
    meta = info(key)
    found = None if options.transcribe == "whisper" else captions(meta, options.lang)
    if found is None:
        if options.transcribe == "captions":
            raise FetchError(f"no {options.lang} captions on {key}; pass --transcribe whisper to transcribe the audio")
        cache = options.cache or Path.home() / ".cache" / "bibliotheke" / "youtube"
        found = whisper(key, cache), "whisper"
    cues, how = found
    rel = source_path(kind, f"{key}.md")
    published = _date(meta.get("upload_date") or "")
    description = (meta.get("description") or "").strip()
    body = f"# {meta.get('title') or key}\n\n"
    if description:
        body += f"## Description\n\n{description}\n\n"
    body += f"Transcript: {rel}\n"
    node = {
        "type": type,
        "id": name_id(meta.get("title") or key),
        "name": meta.get("title") or key,
        "valid_from": published,
        "facts": {
            "url": f"https://www.youtube.com/watch?v={key}",
            "youtube_id": key,
            "channel": meta.get("channel") or meta.get("uploader"),
            "published": published,
            "duration": int(meta["duration"]) if meta.get("duration") else None,
            "views": {"value": int(meta["view_count"]), "valid_from": date.today().isoformat()} if meta.get("view_count") is not None else None,
            "file": rel,
        },
        "body": body,
    }
    links = claimed(re.findall(r"https?://[^\s<>()\"']+", description), (kind, key))
    return Fetched(kind=kind, key=key, node=node, files={rel: transcript(key, meta, cues, how)}, identity=("youtube_id", key), links=links)
