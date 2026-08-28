import asyncio
import logging
import os
import random
import re
import subprocess
import shutil
import textwrap
import time
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont
from telegram import Message, Update
from telegram.constants import ChatAction
from telegram.error import NetworkError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import yt_dlp


load_dotenv()

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

URL_RE = re.compile(
    r"https?://(?:(?:www|m)\.)?(?:x\.com|twitter\.com|instagram\.com|threads\.com|threads\.net|youtube\.com)/[^\s]+|https?://youtu\.be/[^\s]+",
    re.IGNORECASE,
)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
MEME_IMAGE_SIZE = (1080, 1080)
DEFAULT_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)

QUOTE_LIBRARY = [
    {"text": "Small steps still move the work forward.", "author": "Unknown"},
    {"text": "Action reduces fear.", "author": "David Joseph Schwartz"},
    {"text": "Done is a strategy when perfect is blocking progress.", "author": "Unknown"},
    {"text": "Consistency beats intensity when the goal is long-term growth.", "author": "Unknown"},
    {"text": "The best way to make progress is to begin.", "author": "Unknown"},
    {"text": "Clarity often arrives after the first draft.", "author": "Unknown"},
    {"text": "A calm plan can beat a brilliant scramble.", "author": "Unknown"},
    {"text": "You do not need more time to start. You need a smaller first step.", "author": "Unknown"},
    {"text": "Keep going. Momentum is built, not found.", "author": "Unknown"},
    {"text": "Progress is usually quiet before it becomes obvious.", "author": "Unknown"},
]


class ConfigError(RuntimeError):
    pass


def get_env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or not value.strip():
        raise ConfigError(f"Missing required environment variable: {name}")
    return value.strip()


BOT_TOKEN = get_env("TELEGRAM_BOT_TOKEN")
DOWNLOAD_DIR = Path(os.getenv("DOWNLOAD_DIR", "./downloads")).expanduser().resolve()
MAX_DOWNLOAD_MB = int(os.getenv("MAX_DOWNLOAD_MB", "1900"))
MAX_DOWNLOAD_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024
TELEGRAM_API_BASE_URL = os.getenv("TELEGRAM_API_BASE_URL")
TELEGRAM_API_BASE_FILE_URL = os.getenv("TELEGRAM_API_BASE_FILE_URL")
TELEGRAM_LOCAL_MODE = os.getenv("TELEGRAM_LOCAL_MODE", "false").lower() in {"1", "true", "yes", "on"}
INSTAGRAM_COOKIES_FILE = os.getenv("INSTAGRAM_COOKIES_FILE")


def prepare_download_dir() -> None:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


def instagram_cookies_status() -> str:
    if not INSTAGRAM_COOKIES_FILE:
        return "not_configured"

    cookie_path = Path(INSTAGRAM_COOKIES_FILE)
    if not cookie_path.exists():
        return "missing"
    if not cookie_path.is_file():
        return "not_a_file"

    try:
        cookie_text = cookie_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return "unreadable"

    if "instagram.com" not in cookie_text:
        return "no_instagram_domain"
    if "sessionid" not in cookie_text:
        return "no_sessionid"
    return "ready"


def has_usable_instagram_cookies() -> bool:
    return instagram_cookies_status() == "ready"


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text)
    if not match:
        return None
    return normalize_url(match.group(0))


def normalize_url(url: str) -> str:
    lower_url = url.lower()
    if "youtube.com/" in lower_url or "youtu.be/" in lower_url:
        return normalize_youtube_url(url)
    if "instagram.com/" in lower_url:
        return normalize_instagram_url(url)
    if "threads.com/" in lower_url or "threads.net/" in lower_url:
        return normalize_threads_url(url)
    return url


def normalize_youtube_url(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.netloc.lower()
    path = parsed.path
    query = parse_qs(parsed.query)

    if host.endswith("youtu.be"):
        video_id = path.strip("/")
        if video_id:
            clean_query = urlencode({"v": video_id})
            return urlunsplit(("https", "www.youtube.com", "/watch", clean_query, ""))

    if "/shorts/" in path:
        parts = [part for part in path.split("/") if part]
        try:
            shorts_index = parts.index("shorts")
        except ValueError:
            shorts_index = -1
        if shorts_index >= 0 and shorts_index + 1 < len(parts):
            video_id = parts[shorts_index + 1]
            clean_query = urlencode({"v": video_id})
            return urlunsplit(("https", "www.youtube.com", "/watch", clean_query, ""))

    if host.endswith("youtube.com"):
        kept_query: dict[str, str] = {}
        if "v" in query and query["v"]:
            kept_query["v"] = query["v"][0]
        if "list" in query and query["list"]:
            kept_query["list"] = query["list"][0]
        clean_query = urlencode(kept_query)
        return urlunsplit(("https", "www.youtube.com", path, clean_query, ""))

    return url


def normalize_threads_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))


def normalize_instagram_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit(("https", parsed.netloc.lower(), parsed.path, "", ""))


def get_candidate_urls(url: str) -> list[str]:
    normalized_url = normalize_url(url)
    candidates = [normalized_url]

    parsed = urlsplit(normalized_url)
    if parsed.netloc.endswith("threads.com") or parsed.netloc.endswith("threads.net"):
        parts = [part for part in parsed.path.split("/") if part]
        if "post" in parts:
            post_index = parts.index("post")
            if post_index + 1 < len(parts):
                shortcode = parts[post_index + 1]
                candidates.extend([
                    f"https://www.instagram.com/p/{shortcode}/",
                    f"https://www.instagram.com/reel/{shortcode}/",
                ])
    elif parsed.netloc.endswith("instagram.com"):
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] in {"p", "reel", "reels", "tv"}:
            shortcode = parts[1]
            candidates.extend([
                f"https://www.instagram.com/reel/{shortcode}/",
                f"https://www.instagram.com/p/{shortcode}/",
                f"https://www.instagram.com/tv/{shortcode}/",
            ])

    deduped_candidates: list[str] = []
    for candidate in candidates:
        if candidate not in deduped_candidates:
            deduped_candidates.append(candidate)
    return deduped_candidates


def build_ydl_options(output_template: str, url: str) -> dict[str, Any]:
    options = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "restrictfilenames": True,
        "merge_output_format": "mp4",
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
    }

    parsed = urlsplit(url)
    if parsed.netloc.endswith(("instagram.com", "threads.com", "threads.net")):
        options["http_headers"] = {
            "User-Agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            "Referer": "https://www.instagram.com/",
        }

    if has_usable_instagram_cookies() and parsed.netloc.endswith(("instagram.com", "threads.com", "threads.net")):
        options["cookiefile"] = INSTAGRAM_COOKIES_FILE
    return options


def summarize_download_error(exc: Exception) -> str:
    message = str(exc).strip()
    lowered = message.lower()
    if "login" in lowered or "not granting access" in lowered or "private" in lowered:
        return (
            "Instagram blocked access to that media. If it is public but still fails, "
            "run the bot with an Instagram cookies file via INSTAGRAM_COOKIES_FILE."
        )
    if "no video" in lowered or "no media" in lowered or "empty media response" in lowered:
        cookies_status = instagram_cookies_status()
        if cookies_status != "ready":
            return (
                "Instagram requires authenticated cookies for this reel. The bot does not currently see usable "
                f"Instagram cookies inside the container. Cookie status: {cookies_status}."
            )
        return (
            "Instagram returned an empty media response even with cookies enabled. Check that the reel opens "
            "in the same Instagram account used to export cookies, then update yt-dlp and retry."
        )
    if "unsupported url" in lowered:
        return "The downloader does not support that exact link shape yet."
    if message:
        return f"Downloader error: {message[:220]}"
    return "The downloader failed before returning a detailed error."


def get_format_candidates() -> list[str]:
    return [
        # Prefer Telegram-friendly MP4 when it exists.
        "bestvideo*[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        # Fallback for YouTube Shorts and videos that only expose webm/av1 variants.
        "bestvideo+bestaudio/best",
        # Conservative final fallback.
        "bv*+ba/b",
    ]


def ensure_mobile_compatible_video(file_path: Path) -> Path:
    if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return file_path

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        logger.warning("ffmpeg is not installed; skipping mobile compatibility conversion for %s", file_path)
        return file_path

    converted_path = file_path.with_name(f"{file_path.stem}-mobile.mp4")
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(file_path),
        "-movflags",
        "+faststart",
        "-pix_fmt",
        "yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-profile:v",
        "high",
        "-level",
        "4.1",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(converted_path),
    ]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        file_path.unlink(missing_ok=True)
        logger.info("Converted %s to mobile-compatible MP4 %s", file_path, converted_path)
        return converted_path
    except subprocess.CalledProcessError as exc:
        logger.warning("ffmpeg conversion failed for %s: %s", file_path, exc.stderr or exc)
        converted_path.unlink(missing_ok=True)
        return file_path


def download_media(url: str) -> dict[str, Any]:
    prepare_download_dir()
    download_root = DOWNLOAD_DIR / f"job_{time.time_ns()}"
    download_root.mkdir(parents=True, exist_ok=True)
    output_template = str(download_root / "%(title).80s-%(id)s.%(ext)s")
    last_error: Exception | None = None
    candidate_urls = get_candidate_urls(url)

    try:
        info: dict[str, Any] | None = None
        filepath: str | None = None

        for candidate_url in candidate_urls:
            for format_selector in get_format_candidates():
                options = build_ydl_options(output_template, candidate_url)
                options["format"] = format_selector
                try:
                    with yt_dlp.YoutubeDL(options) as ydl:
                        info = ydl.extract_info(candidate_url, download=True)
                        if info is None:
                            raise RuntimeError("yt-dlp did not return metadata.")

                        if "requested_downloads" in info and info["requested_downloads"]:
                            filepath = info["requested_downloads"][0]["filepath"]
                        else:
                            filepath = ydl.prepare_filename(info)
                    logger.info(
                        "Downloaded %s using candidate %s and format selector %s",
                        url,
                        candidate_url,
                        format_selector,
                    )
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Download attempt failed for %s using candidate %s and format %s: %s",
                        url,
                        candidate_url,
                        format_selector,
                        exc,
                    )
            if info is not None and filepath is not None:
                break

        if info is None or filepath is None:
            if last_error is not None:
                raise last_error
            raise RuntimeError("No download format worked for this URL.")

        file_path = Path(filepath)
        if not file_path.exists():
            candidates = sorted(download_root.glob("*"))
            if not candidates:
                raise FileNotFoundError("Downloader finished, but no file was created.")
            file_path = candidates[0]

        file_path = ensure_mobile_compatible_video(file_path)

        file_size = file_path.stat().st_size
        if file_size > MAX_DOWNLOAD_BYTES:
            raise ValueError(
                f"Downloaded file is {file_size / (1024 * 1024):.1f} MB, "
                f"which is bigger than the {MAX_DOWNLOAD_MB} MB bot limit."
            )

        return {
            "path": file_path,
            "title": info.get("title") or file_path.stem,
            "uploader": info.get("uploader") or info.get("channel"),
            "ext": file_path.suffix.lower(),
            "webpage_url": info.get("webpage_url") or url,
            "width": info.get("width"),
            "height": info.get("height"),
            "duration": info.get("duration"),
        }
    except Exception:
        shutil.rmtree(download_root, ignore_errors=True)
        raise


def cleanup_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    shutil.rmtree(path.parent, ignore_errors=True)


def build_caption(media: dict[str, Any]) -> str:
    title = media["title"]
    uploader = media.get("uploader")
    if uploader:
        return f"{title}\nSource: {uploader}"
    return title


def looks_like_animation(media: dict[str, Any]) -> bool:
    return media["ext"] == ".gif"


def quote_of_the_day() -> dict[str, str]:
    index = date.today().toordinal() % len(QUOTE_LIBRARY)
    return QUOTE_LIBRARY[index]


def random_quote() -> dict[str, str]:
    return random.choice(QUOTE_LIBRARY)


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for font_path in DEFAULT_FONT_CANDIDATES:
        if Path(font_path).exists():
            return ImageFont.truetype(font_path, size=size)
    return ImageFont.load_default()


def stroke_width_for(font_size: int) -> int:
    return max(2, font_size // 14)


def fit_font_size(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    initial_size: int,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = initial_size
    while size >= 24:
        font = load_font(size)
        spacing = max(8, size // 5)
        bbox = draw.multiline_textbbox(
            (0, 0),
            text,
            font=font,
            spacing=spacing,
            stroke_width=stroke_width_for(size),
        )
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= max_width and height <= max_height:
            return font
        size -= 4
    return load_font(24)


def wrap_text(draw: ImageDraw.ImageDraw, text: str, max_width: int, initial_size: int) -> str:
    words = text.split()
    if not words:
        return ""

    for width in range(min(len(words), 12), 0, -1):
        wrapped = textwrap.fill(text, width=width)
        font = load_font(initial_size)
        bbox = draw.multiline_textbbox(
            (0, 0),
            wrapped,
            font=font,
            spacing=max(8, initial_size // 5),
            stroke_width=stroke_width_for(initial_size),
        )
        if (bbox[2] - bbox[0]) <= max_width:
            return wrapped
    return text


def parse_meme_text(raw_text: str) -> tuple[str, str]:
    cleaned = raw_text.strip()
    if not cleaned:
        raise ValueError("Use /meme top text | bottom text")

    if "|" in cleaned:
        top_text, bottom_text = cleaned.split("|", maxsplit=1)
        return top_text.strip(), bottom_text.strip()

    return cleaned, ""


def create_gradient_background(size: tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGB", size)
    pixels = image.load()

    for y in range(height):
        blend = y / max(1, height - 1)
        r = int(255 * (1 - blend) + 43 * blend)
        g = int(196 * (1 - blend) + 26 * blend)
        b = int(87 * (1 - blend) + 81 * blend)
        for x in range(width):
            pixels[x, y] = (r, g, b)

    return image


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    image_width: int,
    y: int,
    max_width: int,
    max_height: int,
) -> None:
    if not text:
        return

    wrapped = wrap_text(draw, text.upper(), max_width=max_width, initial_size=96)
    font = fit_font_size(draw, wrapped, max_width=max_width, max_height=max_height, initial_size=96)
    font_size = getattr(font, "size", 24)
    spacing = max(8, font_size // 5)
    bbox = draw.multiline_textbbox(
        (0, 0),
        wrapped,
        font=font,
        spacing=spacing,
        align="center",
        stroke_width=stroke_width_for(font_size),
    )
    text_width = bbox[2] - bbox[0]
    x = (image_width - text_width) / 2
    draw.multiline_text(
        (x, y),
        wrapped,
        font=font,
        fill="white",
        align="center",
        spacing=spacing,
        stroke_width=stroke_width_for(font_size),
        stroke_fill="black",
    )


def add_meme_text(image: Image.Image, top_text: str, bottom_text: str) -> Image.Image:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    horizontal_padding = int(width * 0.08)
    text_width = width - (horizontal_padding * 2)
    text_box_height = int(height * 0.22)

    draw_centered_text(draw, top_text, width, int(height * 0.04), text_width, text_box_height)
    if bottom_text:
        draw_centered_text(draw, bottom_text, width, int(height * 0.74), text_width, text_box_height)
    return image


async def download_replied_photo(message: Message) -> Path:
    if message.reply_to_message is None or message.reply_to_message.photo is None:
        raise ValueError("Reply to a photo with /meme top text | bottom text to turn it into a meme.")

    prepare_download_dir()
    job_dir = DOWNLOAD_DIR / f"job_{time.time_ns()}"
    job_dir.mkdir(parents=True, exist_ok=True)
    photo = message.reply_to_message.photo[-1]
    telegram_file = await photo.get_file()
    destination = job_dir / "source.jpg"
    await telegram_file.download_to_drive(destination)
    return destination


def generate_meme_image(top_text: str, bottom_text: str, source_path: Path | None = None) -> Path:
    prepare_download_dir()
    job_dir = DOWNLOAD_DIR / f"job_{time.time_ns()}"
    job_dir.mkdir(parents=True, exist_ok=True)
    output_path = job_dir / "meme.jpg"

    if source_path is not None:
        image = Image.open(source_path).convert("RGB")
        image = image.resize(MEME_IMAGE_SIZE)
    else:
        image = create_gradient_background(MEME_IMAGE_SIZE)

    image = add_meme_text(image, top_text, bottom_text)
    image.save(output_path, format="JPEG", quality=92)
    return output_path


async def send_downloaded_media(message: Message, media: dict[str, Any]) -> None:
    file_path: Path = media["path"]
    caption = build_caption(media)
    with file_path.open("rb") as media_file:
        if looks_like_animation(media):
            await message.reply_animation(animation=media_file, caption=caption)
        elif media["ext"] in VIDEO_EXTENSIONS:
            await message.reply_video(
                video=media_file,
                caption=caption,
                supports_streaming=True,
                width=media.get("width"),
                height=media.get("height"),
                duration=media.get("duration"),
            )
        else:
            await message.reply_document(document=media_file, caption=caption)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "I can help with a few things:\n\n"
        "/download <url> - download media from X, Instagram, Threads, or YouTube\n"
        "/quote - quote of the day\n"
        "/quote_random - random quote\n"
        "/meme top text | bottom text - create a meme image\n\n"
        "You can also just paste a public X, Instagram, Threads, or YouTube link and I will try to download it."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(
        "Commands:\n"
        "/download <url>\n"
        "/quote\n"
        "/quote_random\n"
        "/meme top text | bottom text\n\n"
        "Tips:\n"
        "- Reply to a photo with /meme top | bottom to add classic meme text\n"
        "- If you do not reply to a photo, /meme will create a poster-style meme card\n"
        "- You can send an x.com, twitter.com, instagram.com, threads.com, threads.net, youtube.com, or youtu.be link directly without /download\n"
        "- Instagram downloads work best for public posts and reels\n"
        "- Threads downloads work best for public posts with video\n"
        "- YouTube Shorts and normal YouTube videos are supported"
    )


async def quote_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    quote = quote_of_the_day()
    await update.message.reply_text(f'Quote of the day:\n"{quote["text"]}"\n- {quote["author"]}')


async def quote_random_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    quote = random_quote()
    await update.message.reply_text(f'Random quote:\n"{quote["text"]}"\n- {quote["author"]}')


async def process_x_download(message: Message, context: ContextTypes.DEFAULT_TYPE, url: str) -> None:
    await context.bot.send_chat_action(chat_id=message.chat_id, action=ChatAction.UPLOAD_VIDEO)
    status_message = await message.reply_text("Downloading media...")

    media: dict[str, Any] | None = None
    try:
        media = await asyncio.to_thread(download_media, url)
        await send_downloaded_media(message, media)
        await status_message.edit_text("Done.")
    except ValueError as exc:
        await status_message.edit_text(str(exc))
    except Exception as exc:
        logger.exception("Failed to process URL %s", url)
        await status_message.edit_text(
            "I could not download media from that link.\n\n"
            f"{summarize_download_error(exc)}"
        )
        logger.debug("Downloader error: %s", exc)
    finally:
        if media is not None:
            cleanup_path(media["path"])


async def download_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    raw_text = " ".join(context.args).strip()
    url = extract_url(raw_text)
    if not url:
        await update.message.reply_text(
            "Use /download followed by a public x.com, twitter.com, instagram.com, threads.com, threads.net, youtube.com, or youtu.be URL."
        )
        return

    await process_x_download(update.message, context, url)


async def meme_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return

    raw_text = update.message.text.partition(" ")[2]
    try:
        top_text, bottom_text = parse_meme_text(raw_text)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.UPLOAD_PHOTO)
    status_message = await update.message.reply_text("Generating meme...")

    source_path: Path | None = None
    meme_path: Path | None = None
    try:
        if update.message.reply_to_message and update.message.reply_to_message.photo:
            source_path = await download_replied_photo(update.message)
            meme_path = await asyncio.to_thread(generate_meme_image, top_text, bottom_text, source_path)
        else:
            meme_path = await asyncio.to_thread(generate_meme_image, top_text, bottom_text, None)

        with meme_path.open("rb") as image_file:
            await update.message.reply_photo(photo=image_file, caption="Fresh meme delivered.")
        await status_message.edit_text("Done.")
    except Exception:
        logger.exception("Failed to generate meme.")
        await status_message.edit_text("I could not generate that meme. Try shorter text or reply to a valid photo.")
    finally:
        if meme_path is not None:
            cleanup_path(meme_path)
        if source_path is not None:
            cleanup_path(source_path)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return

    url = extract_url(update.message.text)
    if url:
        await process_x_download(update.message, context, url)
        return

    await update.message.reply_text(
        "I did not find a supported link in that message.\n"
        "Try /help to see the available skills."
    )


async def post_init(application: Application) -> None:
    logger.info("Bot is ready.")
    logger.info("Instagram cookies status: %s", instagram_cookies_status())


def build_application() -> Application:
    application_builder = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init)
    if TELEGRAM_API_BASE_URL:
        logger.info("Using Telegram Bot API base URL: %s", TELEGRAM_API_BASE_URL)
        application_builder = application_builder.base_url(TELEGRAM_API_BASE_URL)
    if TELEGRAM_API_BASE_FILE_URL:
        logger.info("Using Telegram Bot API file URL: %s", TELEGRAM_API_BASE_FILE_URL)
        application_builder = application_builder.base_file_url(TELEGRAM_API_BASE_FILE_URL)
    if TELEGRAM_LOCAL_MODE:
        logger.info("Telegram local mode is enabled.")
        application_builder = application_builder.local_mode(True)
    return application_builder.build()


def main() -> None:
    prepare_download_dir()

    # Python 3.14 no longer creates a default event loop for the main thread.
    # python-telegram-bot still expects one during run_polling().
    asyncio.set_event_loop(asyncio.new_event_loop())

    application = build_application()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("download", download_command))
    application.add_handler(CommandHandler("quote", quote_command))
    application.add_handler(CommandHandler("quote_random", quote_random_command))
    application.add_handler(CommandHandler("meme", meme_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Starting bot...")
    try:
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except NetworkError as exc:
        logger.error(
            "Could not connect to Telegram. Check your internet connection, DNS, firewall, VPN, or proxy settings."
        )
        logger.debug("Telegram startup network error: %s", exc)
        raise


if __name__ == "__main__":
    main()
