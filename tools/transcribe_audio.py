import os
import sys
import glob
import json
import subprocess
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from groq import Groq
from src.config import REELS_DIR, AUDIO_DIR, TRANSCRIPTS_DIR, GROQ_API_KEY, BASE_DIR

STATUS_FILE = BASE_DIR / "data" / "transcription_status.json"

def extract_audio(video_file: str, audio_file: str) -> bool:
    """Extracts 16kHz mono MP3 audio from video file."""
    cmd = [
        "ffmpeg", "-i", video_file,
        "-vn", "-acodec", "libmp3lame",
        "-ar", "16000", "-ac", "1",
        audio_file, "-y", "-loglevel", "error"
    ]
    res = subprocess.run(cmd)
    return res.returncode == 0

def load_status() -> dict:
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"total": 0, "completed": 0, "pending": 0, "reels": {}}

def save_status(status: dict):
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2, ensure_ascii=False)

def transcribe_all_reels(limit: int = None):
    """
    Transcribes reels using Groq Whisper-large-v3 and keeps an exact progress tracker.
    """
    if not GROQ_API_KEY:
        print("ERROR: GROQ_API_KEY is missing in your .env file.")
        return

    client = Groq(api_key=GROQ_API_KEY)
    reel_videos = sorted(glob.glob(str(REELS_DIR / "*.mp4")))
    total = len(reel_videos)

    status = load_status()
    status["total"] = total

    print(f"Total Reels Found: {total}")
    processed_count = 0

    for idx, video_path in enumerate(reel_videos, 1):
        if limit and processed_count >= limit:
            print(f"Reached batch limit of {limit} reels for this run.")
            break

        base_name = os.path.splitext(os.path.basename(video_path))[0]
        audio_path = str(AUDIO_DIR / f"{base_name}.mp3")
        transcript_json_path = str(TRANSCRIPTS_DIR / f"{base_name}.json")
        meta_json_path = video_path + ".json"

        # Check if already transcribed
        if os.path.exists(transcript_json_path) and base_name in status["reels"]:
            continue

        print(f"\n[{idx}/{total}] Processing Reel: {base_name}...")

        # Load metadata/caption if available
        caption = ""
        post_date = ""
        if os.path.exists(meta_json_path):
            try:
                with open(meta_json_path, "r", encoding="utf-8") as mj:
                    meta = json.load(mj)
                    caption = meta.get("description", "")
                    post_date = meta.get("date", "")
            except Exception:
                pass

        # 1. Extract Audio
        if not os.path.exists(audio_path):
            ok = extract_audio(video_path, audio_path)
            if not ok or not os.path.exists(audio_path):
                print(f"  [x] Failed to extract audio: {base_name}")
                continue

        # 2. Transcribe with Groq Whisper
        try:
            with open(audio_path, "rb") as f:
                transcription = client.audio.transcriptions.create(
                    file=(f"{base_name}.mp3", f.read()),
                    model="whisper-large-v3",
                    response_format="verbose_json"
                )

            words_count = len(transcription.text.split())
            record = {
                "reel_id": base_name,
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

            with open(transcript_json_path, "w", encoding="utf-8") as out_f:
                json.dump(record, out_f, indent=2, ensure_ascii=False)

            # Update status ledger
            status["reels"][base_name] = {
                "transcribed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "words": words_count,
                "caption_preview": caption[:80] if caption else ""
            }
            status["completed"] = len(status["reels"])
            status["pending"] = status["total"] - status["completed"]
            save_status(status)

            processed_count += 1
            print(f"  [OK] Transcribed ({words_count} words). Total Completed: {status['completed']}/{total}", flush=True)
            time.sleep(0.5)

        except Exception as e:
            print(f"  [!] Transcription error on {base_name}: {e}", flush=True)
            time.sleep(2)

    status["completed"] = len(status["reels"])
    status["pending"] = status["total"] - status["completed"]
    save_status(status)
    print(f"\n==========================================", flush=True)
    print(f"Batch Complete! Total Transcribed: {status['completed']} / {total}", flush=True)
    print(f"Updating ChromaDB vector store...", flush=True)
    try:
        from src.knowledge_base import index_all_transcripts
        index_all_transcripts()
        print("Vector database update complete!", flush=True)
    except Exception as e:
        print(f"Error updating vector DB: {e}", flush=True)
    print(f"==========================================", flush=True)

if __name__ == "__main__":
    transcribe_all_reels()
