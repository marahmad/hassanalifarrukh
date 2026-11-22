"""
Quick local interactive test runner for Hassan Ali Farrukh's AI Twin.
Run this directly in your terminal:
    python -m src.test_bot_local
"""
import sys
import os
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from groq import Groq
from src.config import GROQ_API_KEY
from src.knowledge_base import search_knowledge
from src.persona import build_hassan_prompt

def main():
    print("=" * 65)
    print("   HASSAN ALI FARRUKH (@hassanalifarrukh) AI TWIN - TEST CONSOLE")
    print("=" * 65)
    print("Bio: 'can not reply to DM's ... they've been blocked by Instagram'")
    print("Brand: Hataff Security Systems (www.hataff.com)")
    print("Followers: 111,625+")
    print("LLM Engine: Groq High-Speed Inference (openai/gpt-oss-120b)")
    print("=" * 65)

    if not GROQ_API_KEY:
        print("\n[!] ERROR: GROQ_API_KEY is not set in your .env file.")
        return

    groq_client = Groq(api_key=GROQ_API_KEY)

    while True:
        try:
            user_input = input("\nYou (Ask Hassan anything in Roman Urdu or English / 'exit' to quit):\n> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit", "q"]:
                print("Exiting test console.")
                break

            print("\n[+] Searching Hassan's reel knowledge base...")
            contexts = search_knowledge(user_input, n_results=3)

            prompt = build_hassan_prompt(user_input, contexts)
            print("[+] Generating response as Hassan Ali Farrukh...")

            chat = groq_client.chat.completions.create(
                messages=[
                    {"role": "user", "content": prompt}
                ],
                model="openai/gpt-oss-120b",
                temperature=0.4,
                max_tokens=600
            )

            response_text = chat.choices[0].message.content

            print("\n" + "=" * 55)
            print(f"Hassan Ali Farrukh:\n{response_text}")
            print("=" * 55)

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    main()
