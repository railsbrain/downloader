# Telegram Multi-Skill Bot

A simple Telegram bot with multiple skills:

- Download media from public `x.com`, `twitter.com`, `instagram.com`, `threads.com`, `threads.net`, `youtube.com`, and `youtu.be` links
- Generate meme images
- Send a quote of the day

## What it does

- Accepts public X/Twitter/Instagram/Threads/YouTube links in chat
- Supports `/download`, `/quote`, `/quote_random`, and `/meme`
- Generates meme images using Pillow
- Sends media back to the user in Telegram
- Cleans up temporary files after sending

## Important notes

- Use this only for content you have permission to download.
- This bot is designed for public posts and reels.
- X "GIFs" are usually served by X as MP4 video files.
- Instagram support depends on what `yt-dlp` can access without login.
- Some Instagram links may require cookies when Instagram blocks anonymous access.
- Threads support depends on what `yt-dlp` can access without login.
- YouTube support includes standard videos and Shorts.
- Telegram upload limits still apply.
- Meme generation works best with short top and bottom text.

## Setup

### 1. Create a Telegram bot

Talk to [@BotFather](https://t.me/BotFather), create a bot, and copy the bot token.

### 2. Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

For best mobile playback compatibility, make sure `ffmpeg` is installed on the machine running the bot. The Docker image already includes it.

### 4. Configure environment variables

```bash
cp .env.example .env
```

Then edit `.env` and set:

```env
TELEGRAM_BOT_TOKEN=your_real_bot_token
DOWNLOAD_DIR=./downloads
MAX_DOWNLOAD_MB=1900
```

Optional Instagram cookies:

```env
HOST_INSTAGRAM_COOKIES_FILE=/absolute/path/on/host/instagram-cookies.txt
INSTAGRAM_COOKIES_FILE=/app/cookies/instagram-cookies.txt
```

Use this only if public Instagram reels fail because Instagram blocks anonymous access. The file should be in Netscape `cookies.txt` format. If your app lives on a NAS, keep the cookie file on a normal local disk path and point `HOST_INSTAGRAM_COOKIES_FILE` at that existing file.

After restart, check whether the container sees the cookies:

```bash
docker compose logs downloader-bot | grep "Instagram cookies status"
```

The status should be `ready`. If it says `no_sessionid`, export cookies again while logged in to Instagram.

You can also verify the mounted cookie file directly:

```bash
docker compose exec downloader-bot sh -lc 'test -f "$INSTAGRAM_COOKIES_FILE" && grep -q sessionid "$INSTAGRAM_COOKIES_FILE" && echo ready || echo not-ready'
```

## Run the bot

```bash
python bot.py
```

## Run with Docker

Build the image:

```bash
docker build -t telegram-multi-skill-bot .
```

Run the container with your bot token:

```bash
docker run -d --rm \
  --env-file .env \
  -v "$(pwd)/downloads:/app/downloads" \
  -v "/absolute/path/on/host/instagram-cookies.txt:/app/cookies/instagram-cookies.txt:ro" \
  telegram-multi-skill-bot
```

## Run With Local Telegram Bot API Server

For larger uploads, run the bot with a local Telegram Bot API server. You need `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from [my.telegram.org](https://my.telegram.org).

Add these values to `.env`:

```env
TELEGRAM_API_ID=your_telegram_api_id
TELEGRAM_API_HASH=your_telegram_api_hash
TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081/bot
TELEGRAM_API_BASE_FILE_URL=http://telegram-bot-api:8081/file/bot
TELEGRAM_LOCAL_MODE=true
MAX_DOWNLOAD_MB=1900
```

Start both services:

```bash
docker compose up -d --build
```

If Compose reports that `TELEGRAM_API_ID` or `TELEGRAM_API_HASH` is missing, add real values from `my.telegram.org` to `.env` first. Placeholder values will not work.

View logs:

```bash
docker compose logs -f downloader-bot
```

Stop everything:

```bash
docker compose down
```

The Compose setup runs `telegram-bot-api` locally on port `8081` and points the downloader bot at it. This lets the bot handle much larger files than the old `45 MB` setting, while still keeping `MAX_DOWNLOAD_MB` configurable.

If you see `Name or service not known` for `telegram-bot-api`, check that both services are running:

```bash
docker compose ps
docker compose logs telegram-bot-api
```

Use `docker compose up -d --build` for local Bot API mode. A standalone `docker run --env-file .env ...` container cannot resolve the internal `telegram-bot-api` hostname unless you create and attach it to the same Docker network manually.

If you prefer passing env vars directly:

```bash
docker run -d --rm \
  -e TELEGRAM_BOT_TOKEN=your_real_bot_token \
  -e MAX_DOWNLOAD_MB=1900 \
  -v "$(pwd)/downloads:/app/downloads" \
  -v "/absolute/path/on/host/instagram-cookies.txt:/app/cookies/instagram-cookies.txt:ro" \
  telegram-multi-skill-bot
```

## How to use

### Download from X, Instagram, Threads, or YouTube

Send a message containing a public X/Twitter/Instagram/Threads/YouTube URL, or use:

```text
/download https://x.com/username/status/1234567890123456789
```

Instagram example:

```text
/download https://www.instagram.com/reel/ABCDEFGHIJK/
```

Threads example:

```text
/download https://www.threads.com/@gren4den/post/DYRf7m-goXs
```

YouTube examples:

```text
/download https://www.youtube.com/watch?v=dQw4w9WgXcQ
/download https://www.youtube.com/shorts/ABCDEFGHIJK
/download https://youtu.be/dQw4w9WgXcQ
```

### Quote of the day

```text
/quote
```

### Random quote

```text
/quote_random
```

### Generate a meme

Create a poster-style meme:

```text
/meme when production breaks | and it's friday evening
```

Or reply to a photo with:

```text
/meme top text | bottom text
```

## Possible improvements

- Add admin-only access
- Support more websites for downloads
- Use an external quote API
- Add AI-powered caption or meme text generation
- Add Docker support for deployment
