"""
TikTok Schedule & Queue Engine
==============================
Manages, structures, and schedules both archived Reels (Videos) and Photo/Carousel Posts
of Hassan Ali Farrukh for sequential posting on TikTok from the earliest date to the latest.

Ensures:
1. Chronological interleaving: If a video was posted first, it is scheduled; if a photo was posted next, it is scheduled.
2. Full captions are extracted faithfully from saved JSON metadata (reels and posts).
3. Missing or scrape-placeholder captions are recovered from Groq Whisper transcripts.
4. Every reel (.mp4) and post image (.webp/.jpg) is verified on-disk.
5. All actions and slots are logged to SQLite (data/tiktok_scheduler.db), CSV, and JSON manifests.
"""

import os
import sys
import json
import glob
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Ensure UTF-8 console output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

DB_PATH = WORKSPACE_ROOT / "data" / "tiktok_scheduler.db"
REELS_DIR = WORKSPACE_ROOT / "instagram_archive" / "reels"
POSTS_DIR = WORKSPACE_ROOT / "instagram_archive" / "posts"
TRANSCRIPTS_DIR = WORKSPACE_ROOT / "data" / "transcripts"
CSV_EXPORT_PATH = WORKSPACE_ROOT / "data" / "tiktok_schedule.csv"
JSON_EXPORT_PATH = WORKSPACE_ROOT / "data" / "tiktok_schedule.json"

DEFAULT_HASHTAGS = "#HassanAliFarrukh #HataffSecurity #Motivation #Discipline #Pakistan #Mindset #UrduQuotes"

# Default posting slots (Pakistan Standard Time, UTC+5)
DEFAULT_DAILY_SLOTS = ["13:00:00", "17:00:00", "20:00:00"]  # 1:00 PM, 5:00 PM, and 8:00 PM PKT


def init_db(db_path: Path = DB_PATH):
    """Initializes the SQLite database schema for TikTok scheduling, with automatic column migrations."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS tiktok_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reel_id TEXT UNIQUE,
            shortcode TEXT,
            media_type TEXT DEFAULT 'video',
            media_path TEXT,
            original_date TEXT,
            video_path TEXT,
            file_size_mb REAL,
            title_hook TEXT,
            caption TEXT,
            hashtags TEXT,
            full_tiktok_caption TEXT,
            scheduled_slot INTEGER,
            scheduled_date TEXT,
            scheduled_time TEXT,
            scheduled_datetime TEXT,
            status TEXT DEFAULT 'queued',
            tiktok_item_id TEXT,
            error_message TEXT,
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()

    # Migration: Ensure media_type, media_path, and retry_count exist
    c.execute("PRAGMA table_info(tiktok_schedule)")
    columns = [row[1] for row in c.fetchall()]
    if "media_type" not in columns:
        c.execute("ALTER TABLE tiktok_schedule ADD COLUMN media_type TEXT DEFAULT 'video'")
    if "media_path" not in columns:
        c.execute("ALTER TABLE tiktok_schedule ADD COLUMN media_path TEXT")
    if "retry_count" not in columns:
        c.execute("ALTER TABLE tiktok_schedule ADD COLUMN retry_count INTEGER DEFAULT 0")

    # Standardize all datetimes to ISO 8601 (T separator)
    c.execute("UPDATE tiktok_schedule SET scheduled_datetime = replace(scheduled_datetime, ' ', 'T') WHERE scheduled_datetime LIKE '% %'")
    conn.commit()
    conn.close()


def normalize_date(val) -> str:
    """Normalizes unix timestamps, ISO strings, or YYYY-MM-DD into a standard sortable date string."""
    if not val:
        return ""
    s = str(val).strip()
    if s.isdigit():
        try:
            return datetime.fromtimestamp(int(s)).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    m = re.match(r"^(\d{4})[-/](\d{2})[-/](\d{2})", s)
    if m:
        return s.replace("/", "-")
    return s


def find_video_path(meta_file: str, data: dict) -> str:
    """Finds the corresponding .mp4 file on disk for a given reel metadata JSON."""
    base = meta_file[:-5]  # strip .json
    clean_base = base.replace(".info", "").replace(".mp4", "")
    candidates = [
        base,
        base + ".mp4" if not base.endswith(".mp4") else base,
        clean_base + ".mp4",
    ]
    filename = data.get("filename")
    if filename:
        candidates.append(str(REELS_DIR / f"{filename}.mp4"))

    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return os.path.abspath(c)
    return ""


def find_image_path(meta_file: str, shortcode: str) -> str:
    """Finds the corresponding image (.webp or .jpg) on disk for a given post."""
    base = meta_file.replace(".webp.json", "").replace(".info.json", "").replace(".json", "")
    candidates = [
        base + ".webp",
        base + ".jpg",
        meta_file.replace(".json", ""),
    ]
    for c in candidates:
        if os.path.exists(c) and os.path.getsize(c) > 0:
            return os.path.abspath(c)

    # Fallback search by shortcode in posts directory
    sc_cands = glob.glob(str(POSTS_DIR / f"*{shortcode}*.webp")) + glob.glob(str(POSTS_DIR / f"*{shortcode}*.jpg"))
    if sc_cands and os.path.getsize(sc_cands[0]) > 0:
        return os.path.abspath(sc_cands[0])

    return ""


def clean_caption_and_hook(raw_caption: str, shortcode: str, item_id: str, is_photo: bool = False) -> tuple[str, str]:
    """
    Cleans raw Instagram caption pulled from saved JSON.
    If the caption is empty or an Instagram scrape placeholder,
    recovers authentic speech from Groq Whisper transcript (for reels).
    Returns (clean_caption, title_hook).
    """
    raw = (raw_caption or "").strip()

    is_placeholder = False
    if not raw:
        is_placeholder = True
    elif re.search(r"\d+[\d,]*\s+likes", raw, re.IGNORECASE) and "hassanalifarrukh" in raw.lower():
        is_placeholder = True

    clean_text = ""
    if not is_placeholder:
        lines = [l.strip() for l in raw.splitlines()]
        body_lines = []
        for l in lines:
            if l.startswith("#") and len(l.split()) > 2:
                continue
            body_lines.append(l)
        clean_text = "\n".join(body_lines).strip()

    # Fallback to Whisper transcript for video reels if empty or placeholder
    if not clean_text or len(clean_text) < 10:
        if not is_photo:
            for candidate in [item_id, shortcode]:
                tpath = TRANSCRIPTS_DIR / f"{candidate}.json"
                if tpath.exists():
                    try:
                        with open(tpath, "r", encoding="utf-8") as tf:
                            tdata = json.load(tf)
                            full_t = (tdata.get("full_text") or tdata.get("transcript") or "").strip()
                            if full_t:
                                sentences = [s.strip() for s in re.split(r"[\.\?\!\n]+", full_t) if len(s.strip()) > 8]
                                if sentences:
                                    clean_text = ". ".join(sentences[:3]).strip() + "."
                                    break
                    except Exception:
                        pass

    # Generic fallback
    if not clean_text:
        if is_photo:
            clean_text = "Hassan Ali Farrukh | Hataff Security & Mindset"
        else:
            clean_text = "Discipline, Security & Mindset | Hassan Ali Farrukh"

    # Truncate cleanly if exceeds TikTok character allowance (keep within 1800 chars)
    if len(clean_text) > 1800:
        clean_text = clean_text[:1797].rstrip() + "..."

    # Title hook: First sentence or first 7-8 words
    first_sentence = clean_text.splitlines()[0] if clean_text.splitlines() else clean_text
    words = first_sentence.split()
    hook = " ".join(words[:7]) if len(words) > 7 else first_sentence
    hook = re.sub(r"[\,\.\:\;\-]+$", "", hook).strip()

    return clean_text, hook


def build_schedule_queue(start_date_str: str = None,
                         slots_per_day: list = None,
                         hashtags: str = DEFAULT_HASHTAGS) -> int:
    """
    Scans all archived reels (Videos) and posts (Photos), sorts them strictly chronologically
    (earliest date to latest date), assigns 3 daily publishing slots (1:00 PM, 5:00 PM, 8:00 PM PKT),
    and populates SQLite.
    """
    init_db()
    slots = slots_per_day or DEFAULT_DAILY_SLOTS
    now = datetime.now()

    all_items = []

    # 1. Collect Video Reels
    meta_files_reels = glob.glob(str(REELS_DIR / "*.json"))
    for mf in meta_files_reels:
        try:
            with open(mf, "r", encoding="utf-8") as f:
                data = json.load(f)

            shortcode = str(data.get("shortcode") or data.get("post_shortcode") or data.get("id") or os.path.splitext(os.path.basename(mf))[0].replace(".info", "").replace(".mp4", ""))
            post_id = str(data.get("media_id") or data.get("post_id") or shortcode)
            orig_date = normalize_date(data.get("date") or data.get("post_date") or data.get("timestamp") or "")
            raw_caption = data.get("description") or data.get("caption") or ""
            video_path = find_video_path(mf, data)

            if not video_path:
                continue

            file_size_mb = round(os.path.getsize(video_path) / (1024 * 1024), 2)
            all_items.append({
                "item_id": post_id,
                "shortcode": shortcode,
                "media_type": "video",
                "original_date": orig_date,
                "raw_caption": raw_caption,
                "file_path": video_path,
                "file_size_mb": file_size_mb
            })
        except Exception:
            pass

    # 2. Collect Photo & Carousel Posts
    meta_files_posts = glob.glob(str(POSTS_DIR / "*.json"))
    posts_grouped = {}

    for mf in meta_files_posts:
        try:
            with open(mf, "r", encoding="utf-8") as f:
                data = json.load(f)

            shortcode = str(data.get("shortcode") or data.get("post_shortcode") or data.get("id") or os.path.splitext(os.path.basename(mf))[0].replace(".webp", "").replace(".info", ""))
            post_id = str(data.get("post_id") or data.get("id") or shortcode)
            orig_date = normalize_date(data.get("date") or data.get("post_date") or data.get("timestamp") or "")
            raw_caption = data.get("description") or data.get("caption") or data.get("title") or ""
            img_path = find_image_path(mf, shortcode)

            if not img_path:
                continue

            file_size_mb = round(os.path.getsize(img_path) / (1024 * 1024), 2)

            # Avoid duplicate entries for carousel images belonging to the same post
            if post_id not in posts_grouped:
                posts_grouped[post_id] = {
                    "item_id": post_id,
                    "shortcode": shortcode,
                    "media_type": "photo",
                    "original_date": orig_date,
                    "raw_caption": raw_caption,
                    "file_path": img_path,
                    "file_size_mb": file_size_mb
                }
        except Exception:
            pass

    all_items.extend(posts_grouped.values())

    # Sort strictly chronologically by original post date (earliest -> latest)
    all_items.sort(key=lambda x: x["original_date"] or "9999")

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Read existing records to preserve already-scheduled and published items
    existing_items = {}
    latest_queued_dt = None
    for r in c.execute("SELECT reel_id, status, scheduled_datetime, scheduled_date, scheduled_time, scheduled_slot FROM tiktok_schedule").fetchall():
        s_dt = (r["scheduled_datetime"] or "").replace(" ", "T")
        existing_items[r["reel_id"]] = {
            "status": r["status"],
            "scheduled_datetime": s_dt,
            "scheduled_date": r["scheduled_date"],
            "scheduled_time": r["scheduled_time"],
            "scheduled_slot": r["scheduled_slot"]
        }
        if r["status"] in ("queued", "scheduled") and s_dt:
            try:
                dt = datetime.fromisoformat(s_dt)
                if latest_queued_dt is None or dt > latest_queued_dt:
                    latest_queued_dt = dt
            except Exception:
                pass

    # Re-use the ID already stored in the DB for the same media file. Older builder
    # versions keyed rows by post_id, newer ones by media_id, so the same video used
    # to look "new" on every rebuild and got queued (and posted) twice.
    path_to_id = {}
    for r in c.execute("SELECT reel_id, video_path FROM tiktok_schedule WHERE video_path IS NOT NULL ORDER BY id").fetchall():
        path_to_id.setdefault(os.path.normcase(os.path.abspath(r["video_path"])), r["reel_id"])
    remapped = 0
    for it in all_items:
        known = path_to_id.get(os.path.normcase(os.path.abspath(it["file_path"])))
        if known and known != it["item_id"]:
            it["item_id"] = known
            remapped += 1
    if remapped:
        print(f"[*] Matched {remapped} item(s) to existing DB rows by media file (no duplicate rows created).")

    # De-duplicate by physical media file. One video can carry two metadata JSONs
    # (two different IDs); without this the same file gets scheduled twice and
    # gets posted to TikTok twice. Prefer the ID already in the DB (earliest slot).
    def _dedupe_rank(x):
        ex = existing_items.get(x["item_id"])
        return (0, ex["scheduled_datetime"] or "") if ex else (1, "")

    by_path = {}
    for it in all_items:
        key = os.path.normcase(os.path.abspath(it["file_path"]))
        cur_it = by_path.get(key)
        if cur_it is None or _dedupe_rank(it) < _dedupe_rank(cur_it):
            by_path[key] = it
    dropped_dupes = len(all_items) - len(by_path)
    if dropped_dupes:
        print(f"[*] Skipped {dropped_dupes} duplicate metadata entries pointing at the same media file.")
    all_items = list(by_path.values())

    # Determine start slots for newly introduced items
    if latest_queued_dt and latest_queued_dt >= now:
        # Continue sequentially after the last queued item
        current_day = latest_queued_dt.date()
        time_str = latest_queued_dt.strftime("%H:%M:%S")
        next_slot_idx = -1
        for idx, s in enumerate(slots):
            if s > time_str:
                next_slot_idx = idx
                break
        if next_slot_idx == -1:
            current_day += timedelta(days=1)
            slot_idx = 0
        else:
            slot_idx = next_slot_idx
    else:
        current_day = now.date()
        slot_idx = 0
        now_time_str = now.strftime("%H:%M:%S")
        found_today_slot = False
        for idx, s in enumerate(slots):
            if s > now_time_str:
                slot_idx = idx
                found_today_slot = True
                break
        if not found_today_slot:
            current_day += timedelta(days=1)
            slot_idx = 0

    print(f"[*] Processing {len(all_items)} combined items ({len(meta_files_reels)} reels, {len(posts_grouped)} posts) for TikTok scheduling...")
    print(f"[*] Schedule starting / appending on {current_day} at slot: {slots[slot_idx]}")
    print(f"[*] Daily slots: {slots}")

    now_str = datetime.now().isoformat()
    inserted_or_updated = 0

    for item in all_items:
        is_photo = (item["media_type"] == "photo")
        clean_text, hook = clean_caption_and_hook(item["raw_caption"], item["shortcode"], item["item_id"], is_photo=is_photo)
        full_caption = f"{clean_text}\n\n{hashtags}"

        item_id = item["item_id"]
        # If item is already scheduled or published, preserve its assigned schedule
        if item_id in existing_items and existing_items[item_id]["scheduled_datetime"]:
            ex = existing_items[item_id]
            sched_slot = ex["scheduled_slot"] or (slot_idx + 1)
            scheduled_date_str = ex["scheduled_date"]
            scheduled_time = ex["scheduled_time"]
            scheduled_dt_str = ex["scheduled_datetime"]
            item_status = ex["status"] or "queued"
        else:
            # Assign next sequential slot for new items
            scheduled_time = slots[slot_idx]
            scheduled_date_str = current_day.strftime("%Y-%m-%d")
            scheduled_dt_str = f"{scheduled_date_str}T{scheduled_time}"
            sched_slot = slot_idx + 1
            item_status = "queued"

            slot_idx += 1
            if slot_idx >= len(slots):
                slot_idx = 0
                current_day += timedelta(days=1)

        c.execute("""
            INSERT INTO tiktok_schedule (
                reel_id, shortcode, media_type, media_path, original_date, video_path, file_size_mb,
                title_hook, caption, hashtags, full_tiktok_caption,
                scheduled_slot, scheduled_date, scheduled_time, scheduled_datetime,
                status, retry_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(reel_id) DO UPDATE SET
                shortcode=excluded.shortcode,
                media_type=excluded.media_type,
                media_path=excluded.media_path,
                original_date=excluded.original_date,
                video_path=excluded.video_path,
                file_size_mb=excluded.file_size_mb,
                title_hook=excluded.title_hook,
                caption=excluded.caption,
                hashtags=excluded.hashtags,
                full_tiktok_caption=excluded.full_tiktok_caption,
                updated_at=excluded.updated_at
        """, (
            item["item_id"],
            item["shortcode"],
            item["media_type"],
            item["file_path"],
            item["original_date"],
            item["file_path"],
            item["file_size_mb"],
            hook,
            clean_text,
            hashtags,
            full_caption,
            sched_slot,
            scheduled_date_str,
            scheduled_time,
            scheduled_dt_str,
            item_status,
            now_str,
            now_str
        ))
        inserted_or_updated += 1

    conn.commit()
    conn.close()

    print(f"[+] Successfully queued {inserted_or_updated} items (Videos & Photos) into {DB_PATH.name}!")
    export_schedule_manifests()
    return inserted_or_updated


def export_schedule_manifests():
    """Exports CSV and JSON copies of the unified schedule for inspection and analytics."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tiktok_schedule ORDER BY scheduled_datetime ASC").fetchall()
    conn.close()

    if not rows:
        return

    items = [dict(r) for r in rows]
    with open(JSON_EXPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2, ensure_ascii=False)

    import csv
    with open(CSV_EXPORT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "id", "scheduled_date", "scheduled_time", "media_type", "title_hook", "full_tiktok_caption",
            "media_path", "original_date", "shortcode", "status"
        ])
        writer.writeheader()
        for r in items:
            writer.writerow({
                "id": r["id"],
                "scheduled_date": r["scheduled_date"],
                "scheduled_time": r["scheduled_time"],
                "media_type": r.get("media_type") or "video",
                "title_hook": r["title_hook"],
                "full_tiktok_caption": r["full_tiktok_caption"],
                "media_path": r.get("media_path") or r.get("video_path"),
                "original_date": r["original_date"],
                "shortcode": r["shortcode"],
                "status": r["status"]
            })

    print(f"  [+] Exported CSV: {CSV_EXPORT_PATH.name}")
    print(f"  [+] Exported JSON: {JSON_EXPORT_PATH.name}")


def show_summary():
    """Prints a clear summary of the unified TikTok schedule (Videos & Photos)."""
    if not DB_PATH.exists():
        print("[-] TikTok schedule database does not exist yet. Run --build first.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT count(*) FROM tiktok_schedule").fetchone()[0]
    total_videos = conn.execute("SELECT count(*) FROM tiktok_schedule WHERE media_type='video'").fetchone()[0]
    total_photos = conn.execute("SELECT count(*) FROM tiktok_schedule WHERE media_type='photo'").fetchone()[0]

    queued = conn.execute("SELECT count(*) FROM tiktok_schedule WHERE status='queued'").fetchone()[0]
    scheduled = conn.execute("SELECT count(*) FROM tiktok_schedule WHERE status='scheduled'").fetchone()[0]
    published = conn.execute("SELECT count(*) FROM tiktok_schedule WHERE status='published'").fetchone()[0]

    first_dt = conn.execute("SELECT scheduled_datetime FROM tiktok_schedule ORDER BY scheduled_datetime ASC LIMIT 1").fetchone()
    last_dt = conn.execute("SELECT scheduled_datetime FROM tiktok_schedule ORDER BY scheduled_datetime DESC LIMIT 1").fetchone()

    upcoming = conn.execute("SELECT id, scheduled_date, scheduled_time, media_type, title_hook, shortcode FROM tiktok_schedule WHERE status = 'queued' ORDER BY scheduled_datetime ASC LIMIT 6").fetchall()
    conn.close()

    print("\n=======================================================")
    print("      TIKTOK UNIFIED SCHEDULE QUEUE (VIDEOS & PHOTOS)  ")
    print("=======================================================")
    print(f"Total Content Items : {total} ({total_videos} Videos | {total_photos} Photos/Carousels)")
    print(f"Status Breakdown    : {queued} queued | {scheduled} scheduled | {published} published")
    if first_dt and last_dt:
        print(f"Posting Window      : {first_dt[0]} ---> {last_dt[0]}")
    print("\nNext Upcoming Posts in Chronological Queue:")
    for row in upcoming:
        mtype = (row['media_type'] or 'video').upper()
        icon = "🎬" if mtype == "VIDEO" else "📸"
        print(f"  {icon} [{row['scheduled_date']} @ {row['scheduled_time']}] [{mtype}] ({row['shortcode']}): {row['title_hook']}")
    print("=======================================================\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="TikTok Schedule & Queue Engine")
    parser.add_argument("--build", action="store_true", help="Build/rebuild the TikTok schedule queue from instagram_archive")
    parser.add_argument("--start-date", help="Start date (YYYY-MM-DD), default tomorrow")
    parser.add_argument("--summary", action="store_true", help="Print summary of scheduled items")

    args = parser.parse_args()

    if args.summary:
        show_summary()
    else:
        build_schedule_queue(start_date_str=args.start_date)
        show_summary()
