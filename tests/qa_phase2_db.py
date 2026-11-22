import sqlite3
import json
import csv
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = WORKSPACE_ROOT / "data" / "tiktok_scheduler.db"
CSV_PATH = WORKSPACE_ROOT / "data" / "tiktok_schedule.csv"
JSON_PATH = WORKSPACE_ROOT / "data" / "tiktok_schedule.json"

print("=== PHASE 2: DATABASE & SCHEDULE QA ===")

conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row

# 1. Total & breakdown
total = conn.execute("SELECT count(*) FROM tiktok_schedule").fetchone()[0]
videos = conn.execute("SELECT count(*) FROM tiktok_schedule WHERE media_type = 'video'").fetchone()[0]
photos = conn.execute("SELECT count(*) FROM tiktok_schedule WHERE media_type = 'photo'").fetchone()[0]
print(f"Total items in DB: {total} (Videos: {videos}, Photos: {photos})")
assert total == 284, f"Expected 284 items, got {total}"
assert videos == 274, f"Expected 274 videos, got {videos}"
assert photos == 10, f"Expected 10 photos, got {photos}"

# 2. Check Today's Slots
today_items = conn.execute("SELECT id, scheduled_date, scheduled_time, media_type, shortcode, title_hook FROM tiktok_schedule WHERE scheduled_date = '2026-11-02' ORDER BY scheduled_datetime ASC").fetchall()
print(f"Items scheduled today (2026-11-02): {len(today_items)}")
for it in today_items:
    print(f"  [{it['scheduled_date']} @ {it['scheduled_time']}] [{it['media_type'].upper()}] ({it['shortcode']}): {it['title_hook']}")

# 3. Check CSV & JSON exports
assert CSV_PATH.exists(), "CSV manifest missing!"
assert JSON_PATH.exists(), "JSON manifest missing!"

with open(JSON_PATH, "r", encoding="utf-8") as jf:
    jdata = json.load(jf)
    print(f"JSON manifest item count: {len(jdata)}")
    assert len(jdata) == total, "JSON count mismatch!"

with open(CSV_PATH, "r", encoding="utf-8-sig") as cf:
    reader = csv.DictReader(cf)
    csv_rows = list(reader)
    print(f"CSV manifest row count:   {len(csv_rows)}")
    assert len(csv_rows) == total, "CSV count mismatch!"

conn.close()
print("=== PHASE 2 COMPLETE: ALL DB CHECKS PASSED ===")

