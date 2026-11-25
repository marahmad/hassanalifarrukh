"""
Generates v3 two-voice personal late-night TikTok story narration using
Gemini 3.8 Flash TTS (`gemini-3.8-flash-tts`):
  - Voice 1 (`Puck` — youthful, relatable casual Pakistani narrator)
  - Voice 2 (`Algenib` — deep, gravelly, authoritative older-brother mentor voice)
Zero "AI" buzzword — uses natural "Hassan Ali Farrukh ke bot" & "ME" comment trigger.
"""
import base64
import json
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from google import genai
from scipy.io import wavfile

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

OUT_DIR = Path(__file__).resolve().parent / "vo_v3"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BEATS = [
    {
        "id": "01_confession",
        "voice": "Puck",
        "text": "Sach bataun, raat ke do baje jab sab so rahe hote hain, tab asal tension shuru hoti hai.",
        "style": "Youthful, relatable, honest young Pakistani male voice speaking directly at normal brisk TikTok conversational speed. Real and personal, zero slow pauses, zero ad announcer tone.",
    },
    {
        "id": "02_typing",
        "voice": "Puck",
        "text": "Aaj maine bina soche Hassan Ali Farrukh ke bot ko message kar diya — yar samajh nahi aa raha life kidhar ja rahi hai, teen din baad routine toot jati hai.",
        "style": "Natural, conversational, slightly vulnerable young Pakistani male sharing a real late-night story on TikTok. Clear natural pronunciation of 'Hassan Ali Farrukh ke bot', brisk everyday speed.",
    },
    {
        "id": "03_mentor_reply",
        "voice": "Algenib",
        "text": "Tu weak nahi hai yar. Bas perfect ban'ne ke chakkar mein khud ko thaka deta hai. Kal subah sirf bees minute ka ek wada khud se kar, aur kisi haal mein mat tod. Pehle apni zubaan ka pakka ban, zindagi khud line pe aa jayegi.",
        "style": "Deep, gravelly, calm, grounded older Pakistani brother mentor voice — strong presence, direct conversational pace (not slow), warm yet firm conviction on 'Pehle apni zubaan ka pakka ban'.",
    },
    {
        "id": "04_personal_close",
        "voice": "Puck",
        "text": "Pehli baar kisi ne jhooti tasalli nahi di, seedha aaina dikha diya. Agar tumhare saath bhi raat ko aisa hota hai, neeche ME comment karo.",
        "style": "Genuine, moved, conversational young Pakistani male voice at brisk natural TikTok pace — completely organic and personal, clearly saying 'ME'.",
    },
]


def trim_silence(wav_path: Path, threshold_db: float = -38.0, pad_ms: int = 45) -> float:
    sr, data = wavfile.read(wav_path)
    mono = data.mean(axis=1) if data.ndim > 1 else data.astype(np.float32)
    peak = np.max(np.abs(mono)) + 1e-9
    norm = np.abs(mono) / peak
    thresh = 10.0 ** (threshold_db / 20.0)
    idx = np.where(norm > thresh)[0]
    if len(idx) == 0:
        return len(mono) / sr
    pad = int(sr * pad_ms / 1000.0)
    start = max(0, idx[0] - pad)
    end = min(len(data), idx[-1] + pad)
    trimmed = data[start:end]
    wavfile.write(wav_path, sr, trimmed)
    return len(trimmed) / sr


def main():
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not found in .env")

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    manifest = []

    for beat in BEATS:
        out_path = OUT_DIR / f"{beat['id']}.wav"
        print(f"Generating v3 VO: {beat['id']} (voice={beat['voice']})...")
        interaction = client.interactions.create(
            model="gemini-3.8-flash-tts",
            input=[{
                "type": "user_input",
                "content": [{
                    "type": "text",
                    "text": beat["text"],
                    "annotations": [{
                        "type": "speech_metadata",
                        "style": beat["style"],
                    }],
                }],
            }],
            response_format={"type": "audio"},
            generation_config={
                "speech_config": [
                    {"voice": beat["voice"]},
                ]
            },
        )
        raw_bytes = base64.b64decode(interaction.output_audio.data)
        out_path.write_bytes(raw_bytes)
        dur = trim_silence(out_path)
        print(f"  -> Saved {out_path.name} ({dur:.2f}s)")
        manifest.append({
            "id": beat["id"],
            "voice": beat["voice"],
            "file": str(out_path),
            "duration": round(dur, 3),
            "text": beat["text"],
        })

    manifest_path = OUT_DIR / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    total = sum(b["duration"] for b in manifest)
    print(f"\nAll {len(manifest)} v3 2-voice clips ready! Total speech: {total:.2f}s")


if __name__ == "__main__":
    main()
