import asyncio
import os
import re
from typing import Union

import aiohttp
import yt_dlp
from py_yt import VideosSearch
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message

from ishu import app
from ishu.helpers._dataclass import Track
from config import ARC_API_URL, ARC_API_KEY


# ─────────────────────────────────────────────
# Download context
# ─────────────────────────────────────────────

_dl_context = {}


def set_dl_context(
    chat_id=None,
    chat_title=None,
    title=None,
    video=None,
):
    global _dl_context

    _dl_context = {
        "chat_id": chat_id,
        "chat_title": chat_title,
        "title": title,
        "video": video,
    }

    return _dl_context


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def time_to_seconds(value):
    if value is None:
        return 0

    try:
        value = str(value).strip()

        if not value:
            return 0

        parts = value.split(":")

        if len(parts) == 1:
            return int(parts[0])

        total = 0

        for part in parts:
            total = total * 60 + int(part)

        return total

    except Exception:
        return 0


def _extract_video_id(link: str):
    if not link:
        return None

    link = str(link).strip()

    # youtube.com/watch?v=ID
    match = re.search(
        r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{6,})",
        link,
    )

    if match:
        return match.group(1)

    # If already a YouTube ID
    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", link):
        return link

    return None


def _youtube_url(video_id: str):
    return f"https://www.youtube.com/watch?v={video_id}"


# ─────────────────────────────────────────────
# ARC Downloader
# ─────────────────────────────────────────────

async def _arc_request_download(
    video_id: str,
    is_video: bool,
) -> dict:

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{ARC_API_URL.rstrip('/')}/youtube/v2/download",
                params={
                    "query": video_id,
                    "isVideo": str(is_video).lower(),
                    "api_key": ARC_API_KEY,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:

                if resp.status != 200:
                    return {}

                return await resp.json(content_type=None)

    except Exception:
        return {}


async def _arc_poll_job(
    job_id: str,
    retries: int = 20,
    interval: int = 3,
):

    try:
        async with aiohttp.ClientSession() as session:

            for _ in range(retries):

                async with session.get(
                    f"{ARC_API_URL.rstrip('/')}/youtube/jobStatus",
                    params={
                        "job_id": job_id,
                        "api_key": ARC_API_KEY,
                    },
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:

                    if resp.status == 200:

                        data = await resp.json(content_type=None)

                        job = data.get("job", {})

                        if job.get("status") == "done":
                            return (
                                job.get("result", {})
                                .get("cdn")
                            )

                        if job.get("status") == "error":
                            return None

                await asyncio.sleep(interval)

    except Exception:
        return None

    return None


async def _arc_get_cdn(
    video_id: str,
    is_video: bool,
):

    data = await _arc_request_download(
        video_id,
        is_video,
    )

    if not data:
        return None

    job_id = data.get("job_id")

    if not job_id:
        return (
            data.get("result", {})
            .get("cdn")
        )

    return await _arc_poll_job(job_id)


# ─────────────────────────────────────────────
# Save downloaded CDN file
# ─────────────────────────────────────────────

async def _save_from_cdn(
    cdn: str,
    file_path: str,
):

    try:

        # Telegram CDN
        match = re.match(
            r"https?://(?:t\.me|telegram\.dog)/([^/]+)/(\d+)",
            cdn,
        )

        if match:

            username = match.group(1)
            message_id = int(match.group(2))

            msg = await app.get_messages(
                username,
                message_id,
            )

            if not msg:
                return False

            if not (
                msg.audio
                or msg.video
                or msg.document
            ):
                return False

            downloaded = await app.download_media(
                msg,
                file_name=file_path,
            )

            return bool(downloaded)

        # Normal HTTP CDN
        async with aiohttp.ClientSession() as session:

            async with session.get(
                cdn,
                timeout=aiohttp.ClientTimeout(
                    total=600
                ),
            ) as resp:

                if resp.status != 200:
                    return False

                with open(
                    file_path,
                    "wb",
                ) as f:

                    async for chunk in resp.content.iter_chunked(
                        131072
                    ):
                        f.write(chunk)

        return True

    except Exception:
        return False


# ─────────────────────────────────────────────
# Download using ARC
# ─────────────────────────────────────────────

async def download_song(link: str) -> str:

    video_id = _extract_video_id(link)

    if not video_id:
        return None

    os.makedirs(
        "downloads",
        exist_ok=True,
    )

    file_path = os.path.join(
        "downloads",
        f"{video_id}.mp3",
    )

    # Cache
    if (
        os.path.exists(file_path)
        and os.path.getsize(file_path) > 0
    ):
        return file_path

    try:

        cdn = await _arc_get_cdn(
            video_id,
            is_video=False,
        )

        if cdn:

            success = await _save_from_cdn(
                cdn,
                file_path,
            )

            if (
                success
                and os.path.exists(file_path)
                and os.path.getsize(file_path) > 0
            ):
                return file_path

    except Exception:
        pass

    # ─────────────────────────────────────
    # yt-dlp fallback
    # ─────────────────────────────────────

    try:

        url = _youtube_url(video_id)

        ytdl_opts = {
            "format": "bestaudio/best",
            "outtmpl": file_path,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
            await asyncio.to_thread(
                ydl.download,
                [url],
            )

        if (
            os.path.exists(file_path)
            and os.path.getsize(file_path) > 0
        ):
            return file_path

    except Exception:
        pass

    return None


async def download_video(link: str) -> str:

    video_id = _extract_video_id(link)

    if not video_id:
        return None

    os.makedirs(
        "downloads",
        exist_ok=True,
    )

    file_path = os.path.join(
        "downloads",
        f"{video_id}.mp4",
    )

    # Cache
    if (
        os.path.exists(file_path)
        and os.path.getsize(file_path) > 0
    ):
        return file_path

    try:

        cdn = await _arc_get_cdn(
            video_id,
            is_video=True,
        )

        if cdn:

            success = await _save_from_cdn(
                cdn,
                file_path,
            )

            if (
                success
                and os.path.exists(file_path)
                and os.path.getsize(file_path) > 0
            ):
                return file_path

    except Exception:
        pass

    # ─────────────────────────────────────
    # yt-dlp fallback
    # ─────────────────────────────────────

    try:

        url = _youtube_url(video_id)

        ytdl_opts = {
            "format": "bestvideo+bestaudio/best",
            "outtmpl": file_path,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "merge_output_format": "mp4",
        }

        with yt_dlp.YoutubeDL(ytdl_opts) as ydl:
            await asyncio.to_thread(
                ydl.download,
                [url],
            )

        if (
            os.path.exists(file_path)
            and os.path.getsize(file_path) > 0
        ):
            return file_path

    except Exception:
        pass

    return None


# ─────────────────────────────────────────────
# YouTube API
# ─────────────────────────────────────────────

class YouTubeAPI:

    def __init__(self):

        self.base = (
            "https://www.youtube.com/watch?v="
        )

        self.regex = (
            r"(?:youtube\.com|youtu\.be)"
        )

        self.status = (
            "https://www.youtube.com/oembed?url="
        )

        self.listbase = (
            "https://youtube.com/playlist?list="
        )

        self.reg = re.compile(
            r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
        )

    # ─────────────────────────────────────
    # Search
    # ─────────────────────────────────────

    async def search(
        self,
        query: str,
        mystic=None,
        video: Union[bool, str] = False,
    ):

        if not query:
            return None

        query = str(query).strip()

        if not query:
            return None

        # Direct YouTube URL / ID
        video_id = _extract_video_id(query)

        try:

            # ─────────────────────────────
            # Search YouTube
            # ─────────────────────────────

            search_query = (
                query
                if not video_id
                else self.base + video_id
            )

            results = VideosSearch(
                search_query,
                limit=1,
            )

            data = await results.next()

            items = (
                data or {}
            ).get("result", [])

            if not items:
                return None

            result = items[0]

            vid = result.get("id")

            if not vid:
                return None

            title = result.get(
                "title",
                "Unknown",
            )

            duration = result.get(
                "duration"
            ) or "00:00"

            duration_sec = time_to_seconds(
                duration
            )

            url = (
                result.get("link")
                or self.base + vid
            )

            thumbnails = (
                result.get("thumbnails")
                or []
            )

            thumbnail = None

            if thumbnails:
                thumbnail = (
                    thumbnails[0]
                    .get("url")
                )

                if thumbnail:
                    thumbnail = thumbnail.split("?")[0]

            channel = (
                result.get("channel")
                or {}
            )

            channel_name = (
                channel.get("name")
                if isinstance(channel, dict)
                else None
            )

            track = Track(
                id=vid,
                channel_name=channel_name,
                duration=duration,
                duration_sec=duration_sec,
                title=title,
                url=url,
                thumbnail=thumbnail,
                video=bool(video),
            )

            # Use cached file immediately
            extension = (
                "mp4"
                if video
                else "mp3"
            )

            cached_path = os.path.join(
                "downloads",
                f"{vid}.{extension}",
            )

            if (
                os.path.exists(cached_path)
                and os.path.getsize(cached_path) > 0
            ):
                track.file_path = cached_path

            return track

        except Exception:
            return None

    # ─────────────────────────────────────
    # Exists
    # ─────────────────────────────────────

    async def exists(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        return bool(
            re.search(
                self.regex,
                str(link),
            )
        )

    # ─────────────────────────────────────
    # URL from Telegram message
    # ─────────────────────────────────────

    async def url(
        self,
        message_1: Message,
    ):

        messages = [message_1]

        if message_1.reply_to_message:
            messages.append(
                message_1.reply_to_message
            )

        for message in messages:

            if message.entities:

                for entity in message.entities:

                    if (
                        entity.type
                        == MessageEntityType.URL
                    ):

                        text = (
                            message.text
                            or message.caption
                        )

                        return text[
                            entity.offset:
                            entity.offset
                            + entity.length
                        ]

            elif message.caption_entities:

                for entity in message.caption_entities:

                    if (
                        entity.type
                        == MessageEntityType.TEXT_LINK
                    ):
                        return entity.url

        return None

    # ─────────────────────────────────────
    # Details
    # ─────────────────────────────────────

    async def details(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        video_id = _extract_video_id(link)

        if video_id:
            link = self.base + video_id

        results = VideosSearch(
            link,
            limit=1,
        )

        data = await results.next()

        for result in data.get(
            "result",
            [],
        ):

            title = result["title"]

            duration_min = (
                result.get("duration")
                or "00:00"
            )

            thumbnail = (
                result["thumbnails"][0]["url"]
                .split("?")[0]
            )

            vidid = result["id"]

            duration_sec = time_to_seconds(
                duration_min
            )

            return (
                title,
                duration_min,
                duration_sec,
                thumbnail,
                vidid,
            )

        return (
            None,
            "00:00",
            0,
            None,
            None,
        )

    # ─────────────────────────────────────
    # Title
    # ─────────────────────────────────────

    async def title(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        details = await self.details(
            link,
            videoid,
        )

        return details[0]

    # ─────────────────────────────────────
    # Duration
    # ─────────────────────────────────────

    async def duration(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        details = await self.details(
            link,
            videoid,
        )

        return details[1]

    # ─────────────────────────────────────
    # Thumbnail
    # ─────────────────────────────────────

    async def thumbnail(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        details = await self.details(
            link,
            videoid,
        )

        return details[3]

    # ─────────────────────────────────────
    # Video
    # ─────────────────────────────────────

    async def video(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        downloaded_file = await download_video(
            link
        )

        if downloaded_file:
            return 1, downloaded_file

        return 0, "Video download failed"

    # ─────────────────────────────────────
    # Playlist
    # ─────────────────────────────────────

    async def playlist(
        self,
        link,
        limit,
        user_id=None,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.listbase + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            ytdl_opts = {
                "quiet": True,
                "extract_flat": True,
                "skip_download": True,
            }

            with yt_dlp.YoutubeDL(
                ytdl_opts
            ) as ydl:

                info = ydl.extract_info(
                    link,
                    download=False,
                )

            entries = (
                info or {}
            ).get("entries") or []

            tracks = []

            for item in entries[:limit]:

                if not isinstance(
                    item,
                    dict,
                ):
                    continue

                vid = item.get("id")

                if not vid:
                    continue

                try:

                    track = await self.search(
                        self.base + vid,
                        video=videoid,
                    )

                    if track:
                        tracks.append(track)

                except Exception:
                    continue

            return tracks

        except Exception:
            return []

    # ─────────────────────────────────────
    # Track
    # ─────────────────────────────────────

    async def track(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        result = await self.search(
            link,
            video=bool(videoid),
        )

        if not result:
            return None, None

        return (
            {
                "title": result.title,
                "link": result.url,
                "vidid": result.id,
                "duration_min": result.duration,
                "thumb": result.thumbnail,
            },
            result.id,
        )

    # ─────────────────────────────────────
    # Similar candidates
    # ─────────────────────────────────────

    async def search_similar_candidates(
        self,
        query: str,
        limit: int = 10,
    ):

        candidates = []

        try:

            results = VideosSearch(
                query,
                limit=limit,
            )

            data = await results.next()

            for result in (
                data or {}
            ).get("result", []):

                try:

                    duration = (
                        result.get("duration")
                        or "00:00"
                    )

                    candidates.append(
                        type(
                            "YouTubeCandidate",
                            (),
                            {
                                "id": result.get("id"),
                                "title": result.get("title"),
                                "url": (
                                    result.get("link")
                                    or self.base
                                    + result.get(
                                        "id",
                                        "",
                                    )
                                ),
                                "duration": duration,
                                "duration_sec": time_to_seconds(
                                    duration
                                ),
                                "thumbnail": (
                                    (
                                        result.get(
                                            "thumbnails"
                                        )
                                        or [{}]
                                    )[0]
                                    .get(
                                        "url",
                                        "",
                                    )
                                    .split("?")[0]
                                ),
                                "channel_name": (
                                    (
                                        result.get(
                                            "channel"
                                        )
                                        or {}
                                    ).get(
                                        "name",
                                        "",
                                    )
                                ),
                                "video": False,
                                "file_path": None,
                            },
                        )()
                    )

                except Exception:
                    continue

        except Exception:
            return []

        return candidates

    # ─────────────────────────────────────
    # Related candidates
    # ─────────────────────────────────────

    async def get_related_candidates(
        self,
        video_id: str,
        limit: int = 10,
    ):

        try:

            details = await self.details(
                video_id,
                videoid=True,
            )

            title = details[0]

            if title:
                return await self.search_similar_candidates(
                    title,
                    limit=limit,
                )

        except Exception:
            pass

        return []

    # ─────────────────────────────────────
    # Formats
    # ─────────────────────────────────────

    async def formats(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        video_id = _extract_video_id(link)

        if video_id:
            link = self.base + video_id

        ytdl_opts = {
            "quiet": True,
        }

        formats_available = []

        try:

            with yt_dlp.YoutubeDL(
                ytdl_opts
            ) as ydl:

                data = ydl.extract_info(
                    link,
                    download=False,
                )

            for fmt in data.get(
                "formats",
                [],
            ):

                try:

                    if (
                        "dash"
                        not in str(
                            fmt.get(
                                "format",
                                "",
                            )
                        ).lower()
                    ):

                        formats_available.append(
                            {
                                "format": fmt.get(
                                    "format"
                                ),
                                "filesize": fmt.get(
                                    "filesize"
                                ),
                                "format_id": fmt.get(
                                    "format_id"
                                ),
                                "ext": fmt.get(
                                    "ext"
                                ),
                                "format_note": fmt.get(
                                    "format_note"
                                ),
                                "yturl": link,
                            }
                        )

                except Exception:
                    continue

        except Exception:
            pass

        return (
            formats_available,
            link,
        )

    # ─────────────────────────────────────
    # Slider
    # ─────────────────────────────────────

    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        results = VideosSearch(
            link,
            limit=10,
        )

        data = await results.next()

        result = data.get(
            "result",
            [],
        )

        if not result:
            return None, None, None, None

        item = result[query_type]

        return (
            item["title"],
            item["duration"],
            item["thumbnails"][0]["url"].split("?")[0],
            item["id"],
        )

    # ─────────────────────────────────────
    # Download
    # ─────────────────────────────────────

    async def download(
        self,
        link: str,
        mystic=None,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> str:

        if videoid:
            link = self.base + link

        try:

            if video:
                return await download_video(
                    link
                )

            return await download_song(
                link
            )

        except Exception:
            return None


# ─────────────────────────────────────────────
# Global instance
# ─────────────────────────────────────────────

YouTube = YouTubeAPI()
