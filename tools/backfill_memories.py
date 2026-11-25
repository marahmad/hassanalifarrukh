"""
Backfills compact long-term profile memory cards (`users.memory_summary`) for existing
users who have saved `chat_history` turns but an empty `memory_summary`.

Usage (on VM or local machine with .env configured):
    python3 -m tools.backfill_memories
"""

import sys
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.database import get_chat_conversations, update_user_memory_summary
from src.server import GROQ_MEMORY_MODELS, _sanitize_ai_reply, groq_client


def backfill_all_memories(force: bool = False) -> int:
    if not groq_client:
        print("[Error] GROQ_API_KEY is not configured in .env.")
        return 0

    conversations = get_chat_conversations(limit_users=200, messages_per_user=20)
    updated = 0

    for conv in conversations:
        uid = conv["user_id"]
        existing = (conv.get("memory_summary") or "").strip()
        if existing and not force:
            continue

        msgs = conv.get("messages") or []
        if not msgs:
            continue

        lines = []
        for m in msgs[-14:]:
            role = "User" if m.get("role") == "user" else "Assistant"
            lines.append(f"{role}: {(m.get('content') or '')[:300]}")
        transcript_block = "\n".join(lines)

        prompt = (
            "Create a compact long-term profile memory card for this user based ONLY on verified facts they shared.\n"
            "Rules:\n"
            "- Output 2 to 5 short plain-text bullet facts (max 420 characters total, NO markdown bolding or headers).\n"
            "- Capture concrete personal facts: gender (if clear), age, city/living setup, job/studies/exams, skills, core struggle/goals, hard constraints, and active commitments.\n"
            "- Never guess or invent facts. If no concrete personal facts were shared, output exactly: NONE\n\n"
            f"Conversation:\n{transcript_block}\n\n"
            "Memory Card:"
        )

        saved = False
        for model_id, supports_effort in GROQ_MEMORY_MODELS:
            try:
                kwargs = {
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                    "max_completion_tokens": 450 if supports_effort else 240,
                }
                if supports_effort:
                    kwargs["reasoning_effort"] = "low"
                resp = groq_client.chat.completions.create(**kwargs)
                summary = _sanitize_ai_reply((resp.choices[0].message.content or "").strip())
                low = summary.lower().strip(".! ")
                if low in ("", "none", "n/a", "(none yet)", "no personal facts", "no facts yet"):
                    print(f"[-] Skipped {conv['name']} ({uid}): no concrete personal facts yet")
                    saved = True
                    break
                if len(summary) >= 12:
                    update_user_memory_summary(uid, summary[:480])
                    print(f"[+] Updated {conv['name']} ({uid}) via {model_id}:\n    {summary}")
                    updated += 1
                    saved = True
                    break
            except Exception as e:
                print(f"[!] {model_id} failed for {uid}: {e}")
        if not saved:
            print(f"[!] Could not backfill {conv['name']} ({uid})")

    print(f"\nDone. Backfilled {updated} user memory card(s).")
    return updated


if __name__ == "__main__":
    force_flag = "--force" in sys.argv
    backfill_all_memories(force=force_flag)
