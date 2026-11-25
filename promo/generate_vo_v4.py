"""
Generates natural, fluent Pakistani Urdu+English 2-voice clips for v4 using
Gemini 3.8 Flash TTS (`gemini-3.8-flash-tts`) with native Urdu script + English code-switching:
  - Narrator (`Puck`): Youthful, casual Pakistani guy telling a late-night story
  - Bot Reply (`ur-pk-podcaster-1`): Native Karachi mature male Urdu studio voice with natural
    human warmth & vocal fry — ZERO robotic English accent, ZERO choppy silence cuts!
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

OUT_DIR = Path(__file__).resolve().parent / "vo_v4"
OUT_DIR.mkdir(parents=True, exist_ok=True)

BEATS = [
    {
        "id": "02_typing",
        "voice": "Puck",
        "text": "رات کے دو بجے یہ کلپ دیکھی، اور میں نے بنا سوچے حسن علی فرخ کے بوٹ کو میسج کر دیا۔ یار سب صرف اپنا کام نکالنے آتے ہیں، کیسے ہینڈل کروں؟",
        "style": "Natural, casual, relatable young Pakistani male speaking fluent conversational Urdu at normal everyday speed — warm, personal late-night TikTok storytelling, no robotic pauses.",
    },
    {
        "id": "03_mentor_reply",
        "voice": "ur-pk-podcaster-1",
        "text": "سیدھی بات سنو۔ جب کوئی تمہیں use کر جاتا ہے، وہ تمہارا thankful نہیں ہوتا، بلکہ تمہاری disrespect کرتا ہے۔ اپنوں کے لیے بھی ہمیشہ ایک clear boundary رکھو۔ ایک دفعہ پیار سے منع کرو، اگر ڈرامہ کرے تو خاموشی سے راستہ الگ کر لو۔",
        "style": "Warm, deeply human, calm, grounded Pakistani mentor talking 1-on-1 like an older brother in natural conversational Karachi/Lahore Urdu mixed naturally with English words. Smooth natural flow, zero stiffness, zero long pauses.",
    },
    {
        "id": "04_personal_close",
        "voice": "Puck",
        "text": "پہلی بار کسی نے جھوٹی تسلی نہیں دی، بالکل وہی سیدھی بات کی جو ضروری تھی۔ اگر تم بھی اپنے کسی مسئلے پر بات کرنا چاہتے ہو، تو نیچے ME کمنٹ کرو۔",
        "style": "Genuine, relatable young Pakistani male speaking natural fluent Urdu at comfortable TikTok conversational speed — completely organic and human, clearly saying 'ME'.",
    },
]


def trim_silence(wav_path: Path, threshold_db: float = -40.0, pad_ms: int = 55) -> float:
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
        print(f"Generating natural Urdu v4 VO: {beat['id']} (voice={beat['voice']})...")
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
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    total = sum(b["duration"] for b in manifest)
    print(f"\nAll {len(manifest)} natural Urdu v4 VO clips ready! Total speech: {total:.2f}s")


if __name__ == "__main__":
    main()
