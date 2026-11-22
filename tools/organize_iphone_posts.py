"""
TikTok iPhone Streamlining & Mobile Publishing Suite
====================================================
Streamlines uploading scheduled TikTok videos directly from an iPhone to maximize
native mobile algorithmic reach, trending sounds, and FYP distribution.

Key Features:
1. Automated Daily Folders (iCloud Drive / Local):
   Organizes queued posts into daily folders (e.g. `2026-11-04 (Wednesday)`)
   with subfolders for each slot containing `video.mp4` and `caption.txt`.
   Automatically places them into Apple iCloud Drive so they sync wirelessly to your iPhone Files app!

2. Direct Telegram 1-Tap Delivery:
   Sends the exact video and caption directly into your personal Telegram chat on your iPhone.
   Tap 'Save Video' to Camera Roll, tap to copy caption, open TikTok, and post in 15 seconds.

3. Quick Status & Tracking:
   Easily mark items as posted to keep your schedule database accurate.
"""

import os
import sys
import shutil
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime, timedelta

# UTF-8 encoding on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

DB_PATH = WORKSPACE_ROOT / "data" / "tiktok_scheduler.db"

def get_telegram_creds():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id

try:
    import requests
except ImportError:
    requests = None

# Detect iCloud Drive on Windows
DEFAULT_ICLOUD_DIR = Path.home() / "iCloudDrive" / "TikTok_Schedule"
DEFAULT_LOCAL_DIR = WORKSPACE_ROOT / "data" / "tiktok_iphone_ready"


def get_destination_dir() -> Path:
    """Returns iCloud Drive directory if available, otherwise local folder."""
    icloud_parent = Path.home() / "iCloudDrive"
    if icloud_parent.exists():
        DEFAULT_ICLOUD_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_ICLOUD_DIR
    DEFAULT_LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_LOCAL_DIR


def pretty_original_date(raw) -> str:
    """Formats the stored original Instagram post datetime for caption.txt."""
    if not raw:
        return "unknown"
    s = str(raw).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt).strftime("%a %d %b %Y, %I:%M %p PKT")
        except ValueError:
            continue
    return s


def clean_filename(name: str) -> str:
    """Sanitizes text for filesystem directory and file names."""
    import re
    cleaned = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return cleaned[:60] if cleaned else "post"


def ensure_ios_compatible_video(source_path: Path, dest_path: Path) -> bool:
    """
    Ensures video is 100% compatible with Apple iOS Photos app / Camera Roll.
    Transcodes VP9 / non-standard codecs / non-yuv420p to Apple H.264 (yuv420p) + AAC + faststart.
    """
    import subprocess
    import json
    try:
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,pix_fmt", "-of", "json",
            str(source_path)
        ], capture_output=True, text=True, timeout=10)
        stream_info = json.loads(probe.stdout).get("streams", [{}])[0]
        codec = stream_info.get("codec_name")
        pix_fmt = stream_info.get("pix_fmt")
    except Exception:
        codec, pix_fmt = "", ""

    if codec == "h264" and pix_fmt == "yuv420p":
        shutil.copy2(source_path, dest_path)
        return True

    temp_dir = WORKSPACE_ROOT / "data" / "temp_transcode"
    temp_dir.mkdir(parents=True, exist_ok=True)
    local_temp = temp_dir / f"trans_{dest_path.name}"

    cmd = [
        "ffmpeg", "-y", "-i", str(source_path),
        "-c:v", "libx264", "-preset", "fast", "-crf", "19",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(local_temp)
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode == 0:
        shutil.copy2(local_temp, dest_path)
        if local_temp.exists():
            local_temp.unlink()
        return True
    else:
        shutil.copy2(source_path, dest_path)
        return False


def export_schedule_to_daily_folders(days_ahead: int = 7, target_dir: Path = None) -> list:
    """
    Exports queued posts for the upcoming N days into clean daily folders:
    e.g.
    TikTok_Schedule/
      ├── 2026-11-04 (Wednesday)/
      │   ├── 1. [12-30 PM] Stop overthinking/
      │   │   ├── video.mp4 (or photos)
      │   │   └── caption.txt
      │   └── ...
    """
    out_dir = target_dir or get_destination_dir()
    print(f"\n[*] Exporting TikTok posts to folder: {out_dir}")

    if not DB_PATH.exists():
        print(f"[!] Database not found at: {DB_PATH}")
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Find earliest queued date
    row = conn.execute("SELECT scheduled_date FROM tiktok_schedule WHERE status = 'queued' ORDER BY scheduled_datetime ASC LIMIT 1").fetchone()
    if not row:
        print("[*] No queued posts found in database.")
        conn.close()
        return []

    start_date_str = row["scheduled_date"]
    start_dt = datetime.strptime(start_date_str, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=days_ahead)
    end_date_str = end_dt.strftime("%Y-%m-%d")

    query = """
        SELECT * FROM tiktok_schedule
        WHERE status = 'queued'
          AND scheduled_date >= ?
          AND scheduled_date < ?
        ORDER BY scheduled_datetime ASC
    """
    items = conn.execute(query, (start_date_str, end_date_str)).fetchall()
    conn.close()

    print(f"[*] Found {len(items)} queued posts across {days_ahead} days ({start_date_str} to {end_date_str}).")

    exported = []

    for row in items:
        item = dict(row)
        date_str = item["scheduled_date"]
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        day_name = dt.strftime("%A")
        daily_folder_name = f"{date_str} ({day_name})"
        daily_path = out_dir / daily_folder_name

        time_str = item["scheduled_time"].replace(":", "-")
        media_type = (item["media_type"] or "video").lower()
        type_tag = "[PHOTO]" if media_type == "photo" else "[VIDEO]"
        hook = clean_filename(item["title_hook"] or item["shortcode"])
        slot_name = f"{time_str} {type_tag} {hook}"
        slot_path = daily_path / slot_name

        slot_path.mkdir(parents=True, exist_ok=True)

        # 1. Write the ready-to-copy caption
        caption_content = (
            f"=== TIKTOK READY CAPTION ===\n"
            f"Post Slot: {date_str} @ {item['scheduled_time']} PKT\n"
            f"Format: {media_type.upper()}\n"
            f"Hook: {item['title_hook']}\n"
            f"Shortcode: {item['shortcode']}\n"
            f"Originally Posted on Instagram: {pretty_original_date(item.get('original_date'))}\n"
            f"Instagram Link: https://www.instagram.com/{'reel' if media_type == 'video' else 'p'}/{item['shortcode']}/\n"
            f"{'='*40}\n\n"
            f"{item['full_tiktok_caption']}\n\n"
            f"{'='*40}\n"
            f"Tips for Maximum iPhone Reach:\n"
            f"1. Open TikTok -> Tap (+)\n"
            f"2. Select this video or photos\n"
            f"3. Tap 'Add Sound' and select a trending audio (turn volume to 5-10% if speaking)\n"
            f"4. Paste the caption above\n"
            f"5. Add a 3-word native text overlay hook in the first 2 seconds\n"
            f"6. Tap 'Post'!\n"
        )
        (slot_path / "caption.txt").write_text(caption_content, encoding="utf-8")

        # 2. Copy and prepare media files
        raw_path = (item.get("file_path") or item.get("video_path") or item.get("media_path") or "")
        source_media = WORKSPACE_ROOT / raw_path if not Path(raw_path).is_absolute() else Path(raw_path)
        
        if media_type == "video":
            if source_media.exists():
                dest_video = slot_path / "video.mp4"
                if not dest_video.exists() or dest_video.stat().st_size == 0:
                    ensure_ios_compatible_video(source_media, dest_video)
            else:
                print(f"    [!] Video file missing: {source_media}")
        elif media_type == "photo":
            # Check for multiple carousel images
            folder_hint = source_media.parent if source_media.exists() else None
            if folder_hint and folder_hint.exists():
                imgs = sorted(list(folder_hint.glob(f"*{item['shortcode']}*.jpg")) + list(folder_hint.glob(f"*{item['shortcode']}*.webp")))
                if not imgs and source_media.exists():
                    imgs = [source_media]
                for idx, img_path in enumerate(imgs, 1):
                    dest_img = slot_path / f"slide_{idx}_{img_path.name}"
                    if not dest_img.exists():
                        shutil.copy2(img_path, dest_img)
            elif source_media.exists():
                shutil.copy2(source_media, slot_path / source_media.name)

        exported.append({
            "id": item["id"],
            "shortcode": item["shortcode"],
            "date": date_str,
            "time": item["scheduled_time"],
            "type": media_type,
            "slot_path": str(slot_path)
        })

    print(f"[OK] Successfully exported {len(exported)} slots to {out_dir}!")
    return exported


def send_slot_to_telegram(item_id: int = None, date_str: str = None) -> bool:
    """
    Sends the video file and ready-to-copy caption directly to your Telegram chat
    so you can save it straight to your iPhone Photos app in 1 tap.
    """
    token, chat_id = get_telegram_creds()
    if not token or not chat_id:
        print("[!] Telegram credentials (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) missing.")
        return False

    if not requests:
        print("[!] 'requests' library required for Telegram uploads.")
        return False

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    if item_id:
        row = conn.execute("SELECT * FROM tiktok_schedule WHERE id = ?", (item_id,)).fetchone()
    elif date_str:
        row = conn.execute("SELECT * FROM tiktok_schedule WHERE scheduled_date = ? AND status = 'queued' ORDER BY scheduled_datetime ASC LIMIT 1", (date_str,)).fetchone()
    else:
        # Get next upcoming queued item
        row = conn.execute("SELECT * FROM tiktok_schedule WHERE status = 'queued' ORDER BY scheduled_datetime ASC LIMIT 1").fetchone()

    conn.close()

    if not row:
        print("[!] No matching queued post found.")
        return False

    media_type = (row["media_type"] or "video").lower()
    source_media = WORKSPACE_ROOT / row["file_path"] if not Path(row["file_path"]).is_absolute() else Path(row["file_path"])

    print(f"[*] Sending {media_type.upper()} [{row['shortcode']}] to Telegram chat...")

    caption_text = (
        f"📱 <b>TikTok Post Ready for Upload</b>\n\n"
        f"📅 <b>Slot:</b> {row['scheduled_date']} @ {row['scheduled_time']} PKT\n"
        f"📝 <b>Hook:</b> <code>{row['title_hook']}</code>\n\n"
        f"📋 <b>Caption to Copy:</b>\n"
        f"<blockquote>{row['full_tiktok_caption']}</blockquote>\n\n"
        f"👉 <i>Save media to Camera Roll, paste caption, and post on TikTok!</i>"
    )

    if media_type == "video" and source_media.exists():
        url = f"https://api.telegram.org/bot{token}/sendVideo"
        with open(source_media, "rb") as vf:
            files = {"video": vf}
            data = {
                "chat_id": chat_id,
                "caption": caption_text,
                "parse_mode": "HTML"
            }
            res = requests.post(url, data=data, files=files, timeout=60)
            if res.status_code == 200:
                print("[OK] Video successfully delivered to your Telegram!")
                return True
            else:
                print(f"[!] Telegram sendVideo failed: {res.status_code} - {res.text}")
                return False
    else:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": caption_text,
            "parse_mode": "HTML"
        }
        res = requests.post(url, json=data, timeout=15)
        return res.status_code == 200


def mark_item_as_posted(shortcode: str):
    """Marks an item as published in the local database."""
    conn = sqlite3.connect(str(DB_PATH))
    now = datetime.utcnow().isoformat()
    c = conn.cursor()
    c.execute("UPDATE tiktok_schedule SET status = 'published', updated_at = ? WHERE shortcode = ?", (now, shortcode))
    conn.commit()
    count = c.rowcount
    conn.close()
    if count > 0:
        print(f"[OK] Marked [{shortcode}] as published in schedule database.")
    else:
        print(f"[!] Shortcode [{shortcode}] not found in database.")


def show_upcoming_schedule(limit: int = 10):
    """Prints upcoming queued posts with dates and times."""
    if not DB_PATH.exists():
        print(f"[!] Database not found: {DB_PATH}")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT id, scheduled_date, scheduled_time, media_type, title_hook, shortcode
        FROM tiktok_schedule
        WHERE status = 'queued'
        ORDER BY scheduled_datetime ASC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()

    print("\n" + "="*70)
    print("       UPCOMING TIKTOK IPHONE SCHEDULE (OPTIMAL PKT SLOTS)")
    print("="*70)
    for r in rows:
        tag = "[PHOTO]" if r["media_type"] == "photo" else "[VIDEO]"
        print(f" ID #{r['id']:<3} | {r['scheduled_date']} @ {r['scheduled_time']} | {tag:<7} | {r['title_hook'][:35]}")
    print("="*70)


def main():
    parser = argparse.ArgumentParser(description="TikTok iPhone Streamlining & Mobile Publishing Suite")
    parser.add_argument("--export-folders", action="store_true", help="Organize posts into daily date folders in iCloud Drive")
    parser.add_argument("--days", type=int, default=7, help="Number of days ahead to export (default: 7)")
    parser.add_argument("--out-dir", type=str, default=None, help="Custom output directory")
    parser.add_argument("--send-telegram", action="store_true", help="Send upcoming post to Telegram chat")
    parser.add_argument("--item-id", type=int, help="Specific item ID to send to Telegram")
    parser.add_argument("--mark-posted", type=str, help="Mark a shortcode as posted")
    parser.add_argument("--list", action="store_true", help="List upcoming queued slots")

    args = parser.parse_args()

    if args.export_folders:
        custom_p = Path(args.out_dir) if args.out_dir else None
        export_schedule_to_daily_folders(days_ahead=args.days, target_dir=custom_p)
    elif args.send_telegram:
        send_slot_to_telegram(item_id=args.item_id)
    elif args.mark_posted:
        mark_item_as_posted(args.mark_posted)
    elif args.list:
        show_upcoming_schedule()
    else:
        # Default action: list schedule and export
        show_upcoming_schedule()
        export_schedule_to_daily_folders(days_ahead=args.days)


if __name__ == "__main__":
    main()

