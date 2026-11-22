import sys
import json
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.knowledge_base import collection
from src.persona import build_hassan_prompt
from groq import Groq
from src.config import GROQ_API_KEY

test_cases = [
    {
        "topic": "Hataff Guards & Bakra Pricing",
        "query": "Hataff ke security guards mehnge kyun hain aur bakra pricing ka kia matlab hai?"
    },
    {
        "topic": "Content Creation Camera & Perfection",
        "query": "Content creation start karni hai camera aur mic konsa loon?"
    },
    {
        "topic": "Col Farrukh & 'Jo Bichar Gaye' Drama",
        "query": "Aapke walid Col Farrukh ke bare mein batayein aur Jo Bichar Gaye drama mein unka role kisne play kiya tha?"
    }
]

client = Groq(api_key=GROQ_API_KEY)

for idx, tc in enumerate(test_cases, 1):
    q = tc["query"]
    res = collection.query(query_texts=[q], n_results=1)
    doc = res["documents"][0][0]
    meta = res["metadatas"][0][0]
    reel_id = meta.get("reel_id", "")

    # Load transcript file
    raw_spoken = "N/A"
    t_file = f"data/transcripts/{reel_id}.json"
    try:
        with open(t_file, "r", encoding="utf-8") as f:
            td = json.load(f)
            raw_spoken = td.get("full_text", "")
    except Exception:
        raw_spoken = doc

    # Generate answer
    prompt = build_hassan_prompt(q, [doc])
    chat = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="openai/gpt-oss-120b",
        temperature=0.3,
        max_tokens=250
    )
    ans = chat.choices[0].message.content

    print(f"==================================================================")
    print(f"PROOF #{idx}: {tc['topic']}")
    print(f"==================================================================")
    print(f"Source Video Reel: instagram_archive/reels/{reel_id}.mp4")
    print(f"\n[WHAT HASSAN ACTUALLY SAID IN THIS REEL]:")
    print(f"\"{raw_spoken.strip()[:300]}...\"")
    print(f"\n[WHAT THE AI PERSONA REPLIES TO THE USER]:")
    print(f"{ans}")
    print("\n")
