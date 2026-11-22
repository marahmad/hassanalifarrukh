"""
Live Progress Tracker for Hassan Ali Farrukh Reel Transcription
Run this in any terminal window to monitor live progress:
    python -m src.track_progress
"""
import time
import os
import sys

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from tools.transcribe_audio import load_status

def render_progress_bar(completed: int, total: int, width: int = 35) -> str:
    if total == 0:
        return "[-----------------------------------] 0.0%"
    pct = (completed / total) * 100
    filled = int(width * (completed / total))
    bar = "=" * filled + "-" * (width - filled)
    return f"[{bar}] {pct:5.1f}%"

def main():
    print("\033[2J\033[H", end="") # Clear screen
    print("=" * 65)
    print("   HASSAN ALI FARRUKH REEL TRANSCRIPTION LIVE TRACKER")
    print("=" * 65)
    print("Press Ctrl+C to stop monitoring.\n")

    try:
        last_completed = -1
        while True:
            status = load_status()
            total = status.get("total", 253)
            completed = status.get("completed", 0)
            pending = total - completed
            reels = status.get("reels", {})

            bar_str = render_progress_bar(completed, total)
            print(f"\rProgress: {bar_str} | Completed: {completed}/{total} | Pending: {pending}", end="", flush=True)

            if completed != last_completed and completed > 0:
                print(f"\n[Latest Transcribed Reel]:")
                recent_ids = list(reels.keys())[-3:]
                for rid in recent_ids:
                    info = reels[rid]
                    cap = info.get("caption_preview", "")[:60]
                    words = info.get("words", 0)
                    t_time = info.get("transcribed_at", "")
                    print(f"  • ID: {rid} | Words: {words} | Time: {t_time} | Cap: {cap}...")
                print("-" * 65)
                last_completed = completed

            if completed >= total and total > 0:
                print("\n\nAll 253 Reels Have Been Successfully Transcribed!")
                break

            time.sleep(2)
    except KeyboardInterrupt:
        print("\n\nStopped monitoring.")

if __name__ == "__main__":
    main()
