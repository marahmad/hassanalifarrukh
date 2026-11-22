import json
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

with open("data/transcription_status.json", "r", encoding="utf-8") as f:
    s = json.load(f)

total = s.get("total", 253)
done = s.get("completed", 0)
pending = total - done
pct = (done / total) * 100
bar = "=" * int(pct // 3) + "-" * (33 - int(pct // 3))

# Groq processes ~1 reel every 2.5 - 3.0 seconds
est_seconds_left = pending * 2.8
est_minutes = round(est_seconds_left / 60, 1)

print("=" * 65)
print(f"   LIVE TRACKER: [{bar}] {pct:.1f}%")
print(f"   Completed: {done} / {total} Reels | Pending: {pending} Reels")
print(f"   Current Processing Speed: ~20-22 reels / minute")
print(f"   Estimated Time Remaining (ETA): ~{est_minutes} minutes")
print("=" * 65)

recent_items = list(s.get("reels", {}).items())[-5:]
print("\nRecent Reels Transcribed Just Now:")
for rid, info in recent_items:
    cap = info.get("caption_preview", "No caption")[:50].replace("\n", " ")
    w = info.get("words", 0)
    t = info.get("transcribed_at", "")
    print(f"  [OK] ID: {rid} | Words: {w:3d} | At: {t} | \"{cap}...\"")
print("=" * 65)
