---
name: instagram-media-sync
description: >-
  Automated pipeline to download new Instagram reels, active 24h stories, and feed posts
  for Hassan Ali Farrukh (@hassanalifarrukh), extract 16kHz audio via FFmpeg, transcribe speech
  using Groq Whisper Large-v3, and index captions and spoken transcripts into the ChromaDB vector database.
  Use when asked to check for new Instagram videos, sync stories/reels, or update the persona knowledge base.
---

# Instagram Media Sync & AI Ingestion Skill

This skill provides an automated synchronization and AI ingestion pipeline for Hassan Ali Farrukh's Instagram content. It checks for new reels, 24-hour stories, and feed posts, downloads media and metadata, transcribes speech with Groq Whisper Large-v3, and indexes everything into ChromaDB.

## Features
- **Delta Sync:** Only downloads new items; skips already archived reels and stories.
- **Stories Ingestion:** Bypasses Instagram's GraphQL schema blocks by injecting the direct numeric user ID (`7410114277`).
- **Whisper AI Speech-to-Text:** Extracts 16kHz mono audio via FFmpeg and transcribes Urdu/English speech with timestamped segments.
- **Vector Knowledge Base:** Automatically embeds both captions and spoken transcripts into ChromaDB (`hassan_knowledge`).
- **Workspace-Bound:** Runs within this workspace by default; portable to any other workspace by copying `.agents/skills/instagram-media-sync/`.

---

## Quick Usage

### 1. Run Complete Sync (Stories + Reels + Transcription + ChromaDB)
```bash
python -m src.sync
```
Or execute the skill script directly:
```bash
python .agents/skills/instagram-media-sync/scripts/sync.py
```

### 2. Selective Execution Commands
- **Check and sync active stories only:**
  ```bash
  python -m src.sync --stories-only
  ```
- **Sync reels only:**
  ```bash
  python -m src.sync --reels-only
  ```
- **Transcribe any pending audio files:**
  ```bash
  python -m src.sync --transcribe-only
  ```
- **Refresh ChromaDB vector embeddings only:**
  ```bash
  python -m src.sync --index-only
  ```
- **Download specific reel shortcodes:**
  ```bash
  python -m src.sync --reels DfFq58XMOCf DfFZkBPMn2j
  ```

---

## Workspace Structure & Data Locations

| Component | Path | Description |
|---|---|---|
| **Reels Archive** | `instagram_archive/reels/` | Video files (`.mp4`) & JSON metadata |
| **Stories Archive** | `instagram_archive/stories/` | 24-hr story videos & metadata |
| **Feed Posts Archive** | `instagram_archive/posts/` | High-res image/carousel webp & metadata |
| **Audio Directory** | `data/extracted_audio/` | 16kHz mono MP3 tracks extracted by FFmpeg |
| **Transcripts** | `data/transcripts/` | Full text & timestamped segments JSON |
| **ChromaDB Database** | `data/chroma_db/` | Vector store with all-MiniLM-L6-v2 embeddings |
| **Session Cookies** | `cookies.txt` | Authenticated Instagram session |

---

## How to Export/Import into Another Workspace

To use this skill and program in a different workspace:
1. **Copy the skill folder:**
   Copy `.agents/skills/instagram-media-sync/` to the destination workspace's `.agents/skills/` folder.
2. **Copy the session cookies:**
   Copy `cookies.txt` to the destination project root.
3. **Environment requirements:**
   Ensure the destination workspace has `.env` with `GROQ_API_KEY`:
   ```bash
   pip install gallery-dl groq chromadb python-dotenv
   ```
4. Run the sync command in the new workspace:
   ```bash
   python .agents/skills/instagram-media-sync/scripts/sync.py
   ```

