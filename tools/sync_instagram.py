"""
Instagram Media Sync & AI Ingestion Engine
==========================================
Automated pipeline to download new reels, stories, and posts for Hassan Ali Farrukh,
extract audio, transcribe speech with Groq Whisper Large-v3, and index everything
into ChromaDB vector database.
"""

import os
import sys
import json
import time
import glob
import argparse
import subprocess
import urllib.request
from pathlib import Path

# Configure UTF-8 for console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure imports from current workspace
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.config import (
    BASE_DIR,
    REELS_DIR,
    AUDIO_DIR,
    TRANSCRIPTS_DIR,
    CHROMA_DB_DIR,
    GROQ_API_KEY
)

# Optional directories
STORIES_DIR = BASE_DIR / "instagram_archive" / "stories"
POSTS_DIR = BASE_DIR / "instagram_archive" / "posts"
URLS_FILE = BASE_DIR / "instagram_archive" / "urls_to_download.txt"
COOKIES_FILE = BASE_DIR / "cookies.txt"

# Ensure all target folders exist
REELS_DIR.mkdir(parents=True, exist_ok=True)
STORIES_DIR.mkdir(parents=True, exist_ok=True)
POSTS_DIR.mkdir(parents=True, exist_ok=True)
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DB_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_USERNAME = "hassanalifarrukh"
DEFAULT_USER_ID = "7410114277"


def get_existing_reel_ids() -> set:
    """Returns a set of numeric media IDs already downloaded in reels/."""
    existing = set()
    for f in glob.glob(str(REELS_DIR / "*.mp4")):
        base = os.path.splitext(os.path.basename(f))[0]
        existing.add(base)
    return existing


def get_existing_story_ids() -> set:
    """Returns a set of story IDs already downloaded in stories/."""
    existing = set()
    for f in glob.glob(str(STORIES_DIR / "*.mp4")) + glob.glob(str(STORIES_DIR / "*.jpg")):
        base = os.path.splitext(os.path.basename(f))[0]
        existing.add(base)
    return existing


def append_urls_to_manifest(new_urls: list):
    """Appends new URLs to urls_to_download.txt avoiding duplicates."""
    if not URLS_FILE.exists():
        existing = set()
    else:
        with open(URLS_FILE, "r", encoding="utf-8") as f:
            existing = set(line.strip() for line in f if line.strip())

    to_add = [u for u in new_urls if u not in existing]
    if to_add:
        with open(URLS_FILE, "a", encoding="utf-8") as f:
            for u in to_add:
                f.write(u + "\n")
        print(f"  [+] Appended {len(to_add)} new URL(s) to {URLS_FILE.name}")


def sync_stories(cookies_path: str = str(COOKIES_FILE),
                 username: str = DEFAULT_USERNAME,
                 user_id: str = DEFAULT_USER_ID) -> list:
    """
    Retrieves and downloads all currently active 24h stories using gallery-dl
    with user-id injection to bypass broken GraphQL profile endpoints.
    """
    print(f"\n==========================================")
    print(f"[*] CHECKING ACTIVE STORIES (@{username})")
    print(f"==========================================")

    if not os.path.exists(cookies_path):
        print(f"  [!] Cookies file not found at: {cookies_path}")
        return []

    from gallery_dl import config
    from gallery_dl.extractor import find

    config.load()
    config.set(('extractor', 'instagram'), 'cookies', cookies_path)

    story_url = f"https://www.instagram.com/stories/{username}/"
    extr = find(story_url)
    if not extr:
        print("  [!] Could not initialize Instagram story extractor.")
        return []

    extr.initialize()
    # Bypass the broken PolarisProfilePageContentQuery by providing the known user ID
    extr.api.user = lambda u: {"id": str(user_id), "username": username}

    try:
        posts = extr.posts()
    except Exception as e:
        print(f"  [!] Failed to query stories: {e}")
        return []

    existing_stories = get_existing_story_ids()
    downloaded = []

    for p in posts:
        items = p.get("items", [])
        print(f"  Found {len(items)} active story item(s) on profile.")
        for idx, item in enumerate(items, 1):
            pk = str(item.get("pk", item.get("id", "")))
            if not pk:
                continue

            if pk in existing_stories:
                print(f"  [{idx}/{len(items)}] Story {pk} already downloaded. Skipping.")
                continue

            taken_at = item.get("taken_at")
            date_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(taken_at)) if taken_at else ""
            print(f"  [{idx}/{len(items)}] Downloading new story {pk} (Posted: {date_str})...")

            # Save metadata
            meta_path = STORIES_DIR / f"{pk}.json"
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(item, mf, indent=2, ensure_ascii=False)

            media_type = item.get("media_type")
            target_path = None

            if media_type == 2 and item.get("video_versions"):
                video_url = item["video_versions"][0]["url"]
                target_path = STORIES_DIR / f"{pk}.mp4"
                req = urllib.request.Request(video_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp, open(target_path, "wb") as out_f:
                    out_f.write(resp.read())
                print(f"    [OK] Saved video story to: {target_path.name}")
            elif media_type == 1 and item.get("image_versions2"):
                img_url = item["image_versions2"]["candidates"][0]["url"]
                target_path = STORIES_DIR / f"{pk}.jpg"
                req = urllib.request.Request(img_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req) as resp, open(target_path, "wb") as out_f:
                    out_f.write(resp.read())
                print(f"    [OK] Saved image story to: {target_path.name}")

            downloaded.append({
                "pk": pk,
                "type": "video" if media_type == 2 else "image",
                "path": str(target_path) if target_path else "",
                "date": date_str
            })

    print(f"  [*] Stories sync complete. {len(downloaded)} new stories downloaded.")
    return downloaded


def ensure_h264(video_path: Path):
    """Ensures downloaded video is 100% Apple iOS Photos compatible (H.264 / AAC / yuv420p)."""
    try:
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt", "-of", "json",
            str(video_path)
        ], capture_output=True, text=True, timeout=10)
        stream_info = json.loads(probe.stdout).get("streams", [{}])[0]
        codec = stream_info.get("codec_name")
        pix_fmt = stream_info.get("pix_fmt")
    except Exception:
        codec, pix_fmt = "", ""

    if codec == "h264" and pix_fmt == "yuv420p":
        return

    temp_out = video_path.with_name(f"temp_h264_{video_path.name}")
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(temp_out)
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode == 0:
        video_path.unlink()
        temp_out.rename(video_path)
        print(f"    [Auto-Fix] Transcoded {video_path.name} to Apple H.264/AAC")
    elif temp_out.exists():
        temp_out.unlink()


def sync_reels(shortcodes: list = None,
               cookies_path: str = str(COOKIES_FILE),
               username: str = DEFAULT_USERNAME) -> list:
    """
    Downloads reels by shortcode or URL into reels directory with metadata.
    Automatically ensures Apple iOS Photos compatible H.264 encoding.
    """
    print(f"\n==========================================")
    print(f"[*] SYNCING REELS (@{username})")
    print(f"==========================================")

    if not shortcodes:
        print("  [*] No explicit shortcodes provided. Reading un-downloaded URLs from manifest...")
        if not URLS_FILE.exists():
            print("  [!] No urls_to_download.txt file found.")
            return []
        with open(URLS_FILE, "r", encoding="utf-8") as f:
            all_urls = [line.strip() for line in f if "/reel/" in line]
    else:
        all_urls = [f"https://www.instagram.com/reel/{sc}/" if not sc.startswith("http") else sc for sc in shortcodes]

    existing_reels = get_existing_reel_ids()
    downloaded = []

    for url in all_urls:
        shortcode = url.strip("/").split("/")[-1]
        print(f"  Checking reel: {shortcode}...")

        # Prefer yt-dlp for reliable H.264 direct stream extraction
        cmd = [
            "yt-dlp",
            url,
            "-S", "vcodec:h264,res,acodec:aac",
            "--write-info-json",
            "-o", str(REELS_DIR / "%(id)s.%(ext)s"),
            "--no-warnings",
            "-q"
        ]
        res = subprocess.run(cmd)
        target_vid = REELS_DIR / f"{shortcode}.mp4"
        if res.returncode == 0 and target_vid.exists():
            ensure_h264(target_vid)
            print(f"    [OK] Downloaded and verified Apple H.264: {shortcode}.mp4")
            downloaded.append(shortcode)
        else:
            # Fallback to gallery-dl if yt-dlp fails
            cmd_g = [
                "gallery-dl",
                "--cookies", cookies_path,
                "--write-metadata",
                "-D", str(REELS_DIR),
                url
            ]
            res_g = subprocess.run(cmd_g, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res_g.returncode == 0:
                print(f"    [OK] Downloaded via gallery-dl")
                if target_vid.exists():
                    ensure_h264(target_vid)
                downloaded.append(shortcode)
            else:
                print(f"    [!] Error downloading {shortcode}")

    print(f"  [*] Reels sync complete. {len(downloaded)} reel(s) processed.")
    return downloaded


def extract_audio(video_file: str, audio_file: str) -> bool:
    """Extracts 16kHz mono MP3 audio from a video file."""
    cmd = [
        "ffmpeg", "-i", video_file,
        "-vn", "-acodec", "libmp3lame",
        "-ar", "16000", "-ac", "1",
        audio_file, "-y", "-loglevel", "error"
    ]
    res = subprocess.run(cmd)
    return res.returncode == 0 and os.path.exists(audio_file) and os.path.getsize(audio_file) > 0


def transcribe_pending_media():
    """Extracts audio and transcribes all un-transcribed reels and stories."""
    print(f"\n==========================================")
    print(f"[*] TRANSCRIBING MEDIA (Groq Whisper Large-v3)")
    print(f"==========================================")

    if not GROQ_API_KEY:
        print("  [!] ERROR: GROQ_API_KEY missing in .env")
        return

    from groq import Groq
    client = Groq(api_key=GROQ_API_KEY)

    # 1. Gather all video files (reels + stories)
    reel_vids = glob.glob(str(REELS_DIR / "*.mp4"))
    story_vids = glob.glob(str(STORIES_DIR / "*.mp4"))
    all_videos = [(v, "reel") for v in reel_vids] + [(v, "story") for v in story_vids]

    print(f"  Total Video Files Checked: {len(all_videos)}")
    transcribed_count = 0

    for video_path, media_type in all_videos:
        base_name = os.path.splitext(os.path.basename(video_path))[0]
        prefix = f"story_{base_name}" if media_type == "story" else base_name
        audio_path = str(AUDIO_DIR / f"{prefix}.mp3")
        transcript_path = str(TRANSCRIPTS_DIR / f"{prefix}.json")
        meta_json_path = str(STORIES_DIR / f"{base_name}.json") if media_type == "story" else f"{video_path}.json"

        # Check if already transcribed
        if os.path.exists(transcript_path):
            continue

        print(f"\n  Processing {media_type.capitalize()}: {base_name}...")

        # Extract caption / date from metadata if available
        caption = ""
        post_date = ""
        info_json_path = str(REELS_DIR / f"{base_name}.info.json")
        if os.path.exists(meta_json_path):
            try:
                with open(meta_json_path, "r", encoding="utf-8") as mj:
                    meta = json.load(mj)
                    caption = meta.get("description", "")
                    post_date = meta.get("date", "")
            except Exception:
                pass
        elif media_type == "reel" and os.path.exists(info_json_path):
            # yt-dlp downloads write <shortcode>.info.json (caption + unix timestamp)
            try:
                with open(info_json_path, "r", encoding="utf-8") as mj:
                    meta = json.load(mj)
                    caption = meta.get("description") or meta.get("title") or ""
                    ts = meta.get("timestamp")
                    post_date = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if ts else ""
            except Exception:
                pass

        # 1. Extract Audio
        if not os.path.exists(audio_path):
            ok = extract_audio(video_path, audio_path)
            if not ok:
                print(f"    [x] No audio stream or extraction failed for: {base_name}")
                record = {
                    "id": base_name,
                    "type": media_type,
                    "date": post_date,
                    "words_count": 0,
                    "full_text": f"[{media_type.capitalize()} without audible speech track]",
                    "segments": []
                }
                with open(transcript_path, "w", encoding="utf-8") as out_f:
                    json.dump(record, out_f, indent=2, ensure_ascii=False)
                continue

        # 2. Transcribe with Groq Whisper
        try:
            with open(audio_path, "rb") as f:
                transcription = client.audio.transcriptions.create(
                    file=(f"{prefix}.mp3", f.read()),
                    model="whisper-large-v3",
                    response_format="verbose_json"
                )

            words_count = len(transcription.text.split())
            record = {
                "reel_id": base_name if media_type == "reel" else None,
                "story_id": base_name if media_type == "story" else None,
                "type": media_type,
                "caption": caption,
                "date": post_date,
                "words_count": words_count,
                "full_text": transcription.text,
                "segments": [
                    {
                        "start": seg.get("start"),
                        "end": seg.get("end"),
                        "text": seg.get("text")
                    } for seg in getattr(transcription, "segments", [])
                ]
            }

            with open(transcript_path, "w", encoding="utf-8") as out_f:
                json.dump(record, out_f, indent=2, ensure_ascii=False)

            transcribed_count += 1
            print(f"    [OK] Transcribed ({words_count} words): {transcription.text[:80]}...")
            time.sleep(0.5)

        except Exception as e:
            print(f"    [!] Groq Whisper error on {base_name}: {e}")
            time.sleep(2)

    print(f"\n  [*] Transcription pass complete. {transcribed_count} new media items transcribed.")


def update_vector_db():
    """Indexes all transcripts and post captions into ChromaDB."""
    print(f"\n==========================================")
    print(f"[*] REFRESHING CHROMADB VECTOR KNOWLEDGE")
    print(f"==========================================")
    try:
        from src.knowledge_base import index_all_transcripts
        index_all_transcripts()
        print("  [OK] ChromaDB knowledge base is fully up to date.")
    except Exception as e:
        print(f"  [!] Error updating vector DB: {e}")


def run_sync(sync_stories_flag: bool = True,
             sync_reels_flag: bool = True,
             transcribe_flag: bool = True,
             index_flag: bool = True,
             shortcodes: list = None,
             cookies_path: str = str(COOKIES_FILE),
             username: str = DEFAULT_USERNAME,
             user_id: str = DEFAULT_USER_ID):
    """Orchestrates the entire sync and AI ingestion pipeline."""
    start_time = time.time()
    print("==================================================")
    print("   HASSAN ALI FARRUKH - MEDIA SYNC & INGESTION    ")
    print("==================================================")

    if sync_stories_flag:
        sync_stories(cookies_path=cookies_path, username=username, user_id=user_id)

    if sync_reels_flag:
        sync_reels(shortcodes=shortcodes, cookies_path=cookies_path, username=username)

    if transcribe_flag:
        transcribe_pending_media()

    if index_flag:
        update_vector_db()

    elapsed = time.time() - start_time
    print(f"\n==================================================")
    print(f"   SYNC FINISHED IN {elapsed:.1f}s - KNOWLEDGE READY  ")
    print(f"==================================================")


def main():
    parser = argparse.ArgumentParser(
        description="Instagram Media Sync & AI Knowledge Ingestion Pipeline"
    )
    parser.add_argument("--stories-only", action="store_true", help="Only sync active 24h stories")
    parser.add_argument("--reels-only", action="store_true", help="Only sync reels")
    parser.add_argument("--transcribe-only", action="store_true", help="Only transcribe pending media")
    parser.add_argument("--index-only", action="store_true", help="Only refresh ChromaDB vector embeddings")
    parser.add_argument("--reels", nargs="+", help="Specific reel shortcodes or URLs to sync")
    parser.add_argument("--cookies", default=str(COOKIES_FILE), help="Path to cookies.txt")
    parser.add_argument("--username", default=DEFAULT_USERNAME, help="Instagram username")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="Instagram numeric user ID")

    args = parser.parse_args()

    if args.transcribe_only:
        transcribe_pending_media()
        update_vector_db()
    elif args.index_only:
        update_vector_db()
    elif args.stories_only:
        sync_stories(cookies_path=args.cookies, username=args.username, user_id=args.user_id)
        transcribe_pending_media()
        update_vector_db()
    elif args.reels_only:
        sync_reels(shortcodes=args.reels, cookies_path=args.cookies, username=args.username)
        transcribe_pending_media()
        update_vector_db()
    else:
        # Full sync
        run_sync(
            sync_stories_flag=True,
            sync_reels_flag=True,
            transcribe_flag=True,
            index_flag=True,
            shortcodes=args.reels,
            cookies_path=args.cookies,
            username=args.username,
            user_id=args.user_id
        )


if __name__ == "__main__":
    main()

