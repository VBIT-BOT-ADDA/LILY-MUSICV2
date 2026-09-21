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
from config import ARC_API_URL, ARC_API_KEY


# ============================================================
# DOWNLOAD CONTEXT
# ============================================================

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


# ============================================================
# HELPERS
# ============================================================

def time_to_seconds(value):
    if not value:
        return 0

    try:
        stringt = str(value)
        return sum(
            int(x) * 60 ** i
            for i, x in enumerate(reversed(stringt.split(":")))
        )
    except Exception:
        return 0


def _extract_video_id(link: str) -> str:
    if not link:
        return ""

    link = str(link).strip()

    if "v=" in link:
        return link.split("v=", 1)[1].split("&", 1)[0]

    if "youtu.be/" in link:
        return (
            link.split("youtu.be/", 1)[1]
            .split("?", 1)[0]
            .split("&", 1)[0]
        )

    return link


# ============================================================
# ARC DOWNLOAD API
# ============================================================

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
    retries: int = 15,
    interval: int = 3,
) -> str | None:

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

                        data = await resp.json(
                            content_type=None
                        )

                        job = data.get("job", {}) or {}

                        if job.get("status") == "done":
                            return (
                                job.get("result") or {}
                            ).get("cdn")

                        if job.get("status") == "error":
                            return None

                await asyncio.sleep(interval)

    except Exception:
        return None

    return None


async def _arc_get_cdn(
    video_id: str,
    is_video: bool,
) -> str | None:

    data = await _arc_request_download(
        video_id,
        is_video,
    )

    if not data:
        return None

    job_id = data.get("job_id")

    if not job_id:
        return (
            data.get("result") or {}
        ).get("cdn")

    return await _arc_poll_job(job_id)


async def _save_from_cdn(
    cdn: str,
    file_path: str,
) -> bool:

    if not cdn:
        return False

    try:

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

        async with aiohttp.ClientSession() as session:

            async with session.get(
                cdn,
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:

                if resp.status != 200:
                    return False

                with open(file_path, "wb") as f:

                    async for chunk in resp.content.iter_chunked(
                        131072
                    ):
                        f.write(chunk)

        return True

    except Exception:
        return False


# ============================================================
# DOWNLOAD FUNCTIONS
# ============================================================

async def download_song(link: str) -> str | None:

    video_id = _extract_video_id(link)

    if not video_id or len(video_id) < 3:
        return None

    os.makedirs("downloads", exist_ok=True)

    file_path = os.path.join(
        "downloads",
        f"{video_id}.mp3",
    )

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

        if not cdn:
            return None

        saved = await _save_from_cdn(
            cdn,
            file_path,
        )

        if not saved:
            return None

        if (
            os.path.exists(file_path)
            and os.path.getsize(file_path) > 0
        ):
            return file_path

        return None

    except Exception:

        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

        return None


async def download_video(link: str) -> str | None:

    video_id = _extract_video_id(link)

    if not video_id or len(video_id) < 3:
        return None

    os.makedirs("downloads", exist_ok=True)

    file_path = os.path.join(
        "downloads",
        f"{video_id}.mp4",
    )

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

        if not cdn:
            return None

        saved = await _save_from_cdn(
            cdn,
            file_path,
        )

        if not saved:
            return None

        if (
            os.path.exists(file_path)
            and os.path.getsize(file_path) > 0
        ):
            return file_path

        return None

    except Exception:

        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass

        return None


# ============================================================
# YOUTUBE API
# ============================================================

class YouTubeAPI:

    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="

        self.reg = re.compile(
            r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])"
        )

    # ========================================================
    # EXISTS
    # ========================================================

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
                link or "",
            )
        )

    # ========================================================
    # URL
    # ========================================================

    async def url(
        self,
        message_1: Message,
    ) -> Union[str, None]:

        messages = [message_1]

        if message_1.reply_to_message:
            messages.append(
                message_1.reply_to_message
            )

        for message in messages:

            if message.entities:

                for entity in message.entities:

                    if entity.type == MessageEntityType.URL:

                        text = (
                            message.text
                            or message.caption
                            or ""
                        )

                        return text[
                            entity.offset:
                            entity.offset + entity.length
                        ]

            if message.caption_entities:

                for entity in message.caption_entities:

                    if (
                        entity.type
                        == MessageEntityType.TEXT_LINK
                    ):
                        return entity.url

        return None

    # ========================================================
    # SEARCH
    # ========================================================

    async def search(
        self,
        query: str,
        mystic=None,
        video: Union[bool, str] = None,
    ):
        try:

            results = VideosSearch(
                query,
                limit=1,
            )

            data = await results.next()

            result_list = (
                (data or {}).get("result")
                or []
            )

            if not result_list:
                return None

            result = result_list[0]

            video_id = result.get("id")

            if not video_id:
                return None

            link = (
                result.get("link")
                or self.base + video_id
            )

            if video:
                file_path = await download_video(
                    link
                )
            else:
                file_path = await download_song(
                    link
                )

            return file_path

        except Exception:
            return None

    # ========================================================
    # DETAILS
    # ========================================================

    async def details(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            results = VideosSearch(
                link,
                limit=1,
            )

            data = await results.next()

            result_list = (
                (data or {}).get("result")
                or []
            )

            if not result_list:
                return (
                    None,
                    None,
                    0,
                    None,
                    None,
                )

            result = result_list[0]

            title = result.get("title")
            duration_min = result.get("duration")

            thumbnails = (
                result.get("thumbnails")
                or []
            )

            thumbnail = (
                thumbnails[0]
                .get("url", "")
                .split("?")[0]
                if thumbnails
                else None
            )

            vidid = result.get("id")

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

        except Exception:
            return (
                None,
                None,
                0,
                None,
                None,
            )

    # ========================================================
    # TITLE
    # ========================================================

    async def title(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            results = VideosSearch(
                link,
                limit=1,
            )

            data = await results.next()

            result_list = (
                (data or {}).get("result")
                or []
            )

            if not result_list:
                return None

            return result_list[0].get("title")

        except Exception:
            return None

    # ========================================================
    # DURATION
    # ========================================================

    async def duration(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            results = VideosSearch(
                link,
                limit=1,
            )

            data = await results.next()

            result_list = (
                (data or {}).get("result")
                or []
            )

            if not result_list:
                return None

            return result_list[0].get("duration")

        except Exception:
            return None

    # ========================================================
    # THUMBNAIL
    # ========================================================

    async def thumbnail(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            results = VideosSearch(
                link,
                limit=1,
            )

            data = await results.next()

            result_list = (
                (data or {}).get("result")
                or []
            )

            if not result_list:
                return None

            thumbnails = (
                result_list[0].get("thumbnails")
                or []
            )

            if not thumbnails:
                return None

            return (
                thumbnails[0]
                .get("url", "")
                .split("?")[0]
            )

        except Exception:
            return None

    # ========================================================
    # VIDEO
    # ========================================================

    async def video(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            downloaded_file = await download_video(
                link
            )

            if downloaded_file:
                return 1, downloaded_file

            return 0, "Video download failed"

        except Exception as e:

            return 0, f"Video download error: {e}"

    # ========================================================
    # PLAYLIST
    # ========================================================

    async def playlist(
        self,
        link,
        limit,
        user_id,
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
                (info or {}).get("entries")
                or []
            )

            return [
                item.get("id")
                for item in entries[:limit]
                if (
                    isinstance(item, dict)
                    and item.get("id")
                )
            ]

        except Exception:
            return []

    # ========================================================
    # TRACK
    # ========================================================

    async def track(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            results = VideosSearch(
                link,
                limit=1,
            )

            data = await results.next()

            result_list = (
                (data or {}).get("result")
                or []
            )

            if not result_list:
                return None, None

            result = result_list[0]

            title = result.get("title")
            duration_min = result.get("duration")
            vidid = result.get("id")
            yturl = result.get("link")

            thumbnails = (
                result.get("thumbnails")
                or []
            )

            thumbnail = (
                thumbnails[0]
                .get("url", "")
                .split("?")[0]
                if thumbnails
                else None
            )

            track_details = {
                "title": title,
                "link": yturl,
                "vidid": vidid,
                "duration_min": duration_min,
                "thumb": thumbnail,
            }

            return track_details, vidid

        except Exception:
            return None, None

    # ========================================================
    # SIMILAR CANDIDATES
    # ========================================================

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
                (data or {}).get("result")
                or []
            ):

                try:

                    video_id = result.get("id")

                    if not video_id:
                        continue

                    duration = result.get(
                        "duration"
                    )

                    duration_sec = time_to_seconds(
                        duration
                    )

                    thumbnails = (
                        result.get("thumbnails")
                        or []
                    )

                    thumbnail = (
                        thumbnails[0]
                        .get("url", "")
                        .split("?")[0]
                        if thumbnails
                        else ""
                    )

                    channel = (
                        result.get("channel")
                        or {}
                    )

                    channel_name = (
                        channel.get("name", "")
                        if isinstance(channel, dict)
                        else ""
                    )

                    candidate = type(
                        "YouTubeCandidate",
                        (),
                        {
                            "id": video_id,
                            "title": result.get(
                                "title"
                            ),
                            "url": (
                                result.get("link")
                                or (
                                    self.base
                                    + video_id
                                )
                            ),
                            "duration": duration,
                            "duration_sec": duration_sec,
                            "thumbnail": thumbnail,
                            "channel_name": channel_name,
                            "video": False,
                            "file_path": None,
                        },
                    )()

                    candidates.append(
                        candidate
                    )

                except Exception:
                    continue

        except Exception:
            return []

        return candidates

    # ========================================================
    # RELATED CANDIDATES
    # ========================================================

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

            if not details:
                return []

            title = details[0]

            if not title:
                return []

            return await self.search_similar_candidates(
                title,
                limit=limit,
            )

        except Exception:
            return []

    # ========================================================
    # FORMATS
    # ========================================================

    async def formats(
        self,
        link: str,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            ytdl_opts = {
                "quiet": True,
            }

            with yt_dlp.YoutubeDL(
                ytdl_opts
            ) as ydl:

                info = ydl.extract_info(
                    link,
                    download=False,
                )

                formats_available = []

                for fmt in (
                    info.get("formats", [])
                    if info
                    else []
                ):

                    try:

                        if "dash" in str(
                            fmt.get("format", "")
                        ).lower():
                            continue

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

            return formats_available, link

        except Exception:
            return [], link

    # ========================================================
    # SLIDER
    # ========================================================

    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: Union[bool, str] = None,
    ):

        if videoid:
            link = self.base + link

        if "&" in link:
            link = link.split("&")[0]

        try:

            results = VideosSearch(
                link,
                limit=10,
            )

            data = await results.next()

            result_list = (
                (data or {}).get("result")
                or []
            )

            if not result_list:
                return (
                    None,
                    None,
                    None,
                    None,
                )

            if query_type >= len(result_list):
                query_type = 0

            result = result_list[query_type]

            title = result.get("title")
            duration_min = result.get("duration")
            vidid = result.get("id")

            thumbnails = (
                result.get("thumbnails")
                or []
            )

            thumbnail = (
                thumbnails[0]
                .get("url", "")
                .split("?")[0]
                if thumbnails
                else None
            )

            return (
                title,
                duration_min,
                thumbnail,
                vidid,
            )

        except Exception:
            return (
                None,
                None,
                None,
                None,
            )

    # ========================================================
    # DOWNLOAD
    # ========================================================

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
    ):

        if videoid:
            link = self.base + link

        try:

            if video:
                return await download_video(link)

            return await download_song(link)

        except Exception:
            return None


# ============================================================
# GLOBAL YOUTUBE INSTANCE
# ============================================================

YouTube = YouTubeAPI()
