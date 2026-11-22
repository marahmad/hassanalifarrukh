"""
Hassan Ali Farrukh AI - Unified Knowledge Base & Vector Engine
=============================================================
Manages vector embeddings and retrieval for Hassan Ali Farrukh's reels, posts,
captions, and spoken audio transcripts.

Features:
1. Primary: High-speed cosine vector search via Supabase pgvector (`knowledge_vectors`).
2. Fallback: Local persistent ChromaDB collection (`data/chroma_db`).
3. Dual-Sync: Updates both Supabase and local ChromaDB when indexing new media.
"""

import os
import sys
import glob
import json
import logging
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions

# Configure UTF-8 for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.config import TRANSCRIPTS_DIR, CHROMA_DB_DIR, REELS_DIR, POSTS_DIR, DATABASE_URL
from src.database import DBContext

logger = logging.getLogger("knowledge_base")

# Initialize embedding model (384-dimensional all-MiniLM-L6-v2)
emb_fn = embedding_functions.DefaultEmbeddingFunction()

# Initialize local ChromaDB client & collection as fallback
try:
    chroma_client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
    collection = chroma_client.get_or_create_collection(
        name="hassan_knowledge",
        embedding_function=emb_fn
    )
except Exception as e:
    logger.warning("Local ChromaDB initialization note: %s", e)
    collection = None


def search_knowledge_supabase(query: str, n_results: int = 4) -> list[str]:
    """Retrieves top matching knowledge segments from Supabase pgvector using cosine distance."""
    try:
        query_emb = emb_fn([query])[0]
        emb_str = "[" + ",".join(f"{x:.8f}" for x in query_emb) + "]"

        with DBContext() as db:
            if db.is_pg:
                db.execute("""
                    SELECT content, 1 - (embedding <=> %s::vector) AS similarity
                    FROM knowledge_vectors
                    ORDER BY embedding <=> %s::vector
                    LIMIT %s;
                """, (emb_str, emb_str, n_results))
                rows = db.fetchall()
                if rows:
                    return [r["content"] for r in rows]
    except Exception as e:
        logger.warning("Supabase pgvector search failed: %s. Falling back to local ChromaDB.", e)

    return []


def search_knowledge_chroma(query: str, n_results: int = 4) -> list[str]:
    """Retrieves top matching knowledge segments from local ChromaDB."""
    if collection is None:
        return []
    try:
        results = collection.query(
            query_texts=[query],
            n_results=n_results
        )
        if results and results.get("documents") and results["documents"][0]:
            return results["documents"][0]
    except Exception as e:
        logger.error("ChromaDB search error: %s", e)

    return []


def search_knowledge(query: str, n_results: int = 4) -> list[str]:
    """
    Unified knowledge search.
    Tries Supabase pgvector first (instant cloud vector retrieval),
    then seamlessly falls back to local ChromaDB if offline or unreachable.
    """
    # 1. Try Supabase pgvector
    results = search_knowledge_supabase(query, n_results=n_results)
    if results:
        return results

    # 2. Fallback to local ChromaDB
    return search_knowledge_chroma(query, n_results=n_results)


def sync_batch_to_supabase(ids: list[str], documents: list[str], metadatas: list[dict], embeddings: list):
    """Upserts a batch of documents and vector embeddings into Supabase knowledge_vectors."""
    try:
        import psycopg2
        from psycopg2.extras import execute_batch

        if not DATABASE_URL:
            return

        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        conn.autocommit = True
        cur = conn.cursor()

        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_vectors (
            id TEXT PRIMARY KEY,
            reel_id TEXT,
            content_type TEXT,
            caption TEXT,
            content TEXT,
            embedding vector(384),
            metadata JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        rows = []
        for cid, doc, meta, emb in zip(ids, documents, metadatas, embeddings):
            meta = meta or {}
            reel_id = meta.get("reel_id", "")
            content_type = meta.get("type", "speech")
            caption = meta.get("caption", "")
            emb_str = "[" + ",".join(f"{x:.8f}" for x in emb) + "]"
            meta_json = json.dumps(meta)
            rows.append((cid, reel_id, content_type, caption, doc, emb_str, meta_json))

        upsert_sql = """
        INSERT INTO knowledge_vectors (id, reel_id, content_type, caption, content, embedding, metadata)
        VALUES (%s, %s, %s, %s, %s, %s::vector, %s)
        ON CONFLICT (id) DO UPDATE SET
            reel_id = EXCLUDED.reel_id,
            content_type = EXCLUDED.content_type,
            caption = EXCLUDED.caption,
            content = EXCLUDED.content,
            embedding = EXCLUDED.embedding,
            metadata = EXCLUDED.metadata;
        """

        execute_batch(cur, upsert_sql, rows)
        cur.close()
        conn.close()
        print(f"  [+] Synced {len(rows)} embeddings to Supabase pgvector!")
    except Exception as e:
        logger.warning("Could not sync vectors to Supabase: %s", e)


def index_all_transcripts():
    """Reads all transcribed JSON files and reel metadata captions, populating ChromaDB and Supabase."""
    print("==================================================")
    print("  INDEXING KNOWLEDGE: REELS, POSTS & TRANSCRIPTS  ")
    print("==================================================")

    # 1. Index reel metadata captions
    meta_files = glob.glob(str(REELS_DIR / "*.json"))
    print(f"Found {len(meta_files)} reel metadata files...")

    documents = []
    metadatas = []
    ids = []

    for mf in meta_files:
        try:
            with open(mf, "r", encoding="utf-8") as f:
                meta = json.load(f)
            desc = (meta.get("description") or meta.get("caption") or "").strip()
            if "likes," in desc and "comments - hassanalifarrukh" in desc:
                desc = ""
            date = meta.get("date", "")
            base_id = os.path.splitext(os.path.basename(mf))[0].replace(".mp4", "")
            if desc:
                cid = f"meta_{base_id}"
                documents.append(f"Topic / Caption: {desc}\nDate: {date}")
                metadatas.append({
                    "reel_id": base_id,
                    "type": "caption",
                    "date": date,
                    "caption": desc[:100]
                })
                ids.append(cid)
        except Exception:
            pass

    # 1b. Index feed posts metadata captions
    post_files = glob.glob(str(POSTS_DIR / "*.json"))
    print(f"Found {len(post_files)} post metadata files...")
    for pf in post_files:
        try:
            with open(pf, "r", encoding="utf-8") as f:
                meta = json.load(f)
            desc = (meta.get("description") or meta.get("caption") or "").strip()
            date = meta.get("date", "")
            base_id = os.path.splitext(os.path.basename(pf))[0]
            if desc:
                cid = f"post_{base_id}"
                documents.append(f"Feed Post / Caption: {desc}\nDate: {date}")
                metadatas.append({
                    "reel_id": base_id,
                    "type": "post_caption",
                    "date": date,
                    "caption": desc[:100]
                })
                ids.append(cid)
        except Exception:
            pass

    # 2. Index transcribed speech files from data/transcripts/
    transcript_files = glob.glob(str(TRANSCRIPTS_DIR / "*.json"))
    print(f"Found {len(transcript_files)} audio transcript files...")

    for fpath in transcript_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            reel_id = data.get("reel_id") or data.get("story_id") or data.get("shortcode") or os.path.splitext(os.path.basename(fpath))[0]
            content_type = data.get("type", "speech")
            caption = data.get("caption") or data.get("description", "")
            full_text = (data.get("full_text") or data.get("text", "")).strip()

            if not full_text:
                continue

            segments = data.get("segments", [])
            if segments:
                chunk_buffer = ""
                chunk_start = 0.0

                for seg in segments:
                    seg_text = seg.get("text", "")
                    chunk_buffer += " " + seg_text

                    if len(chunk_buffer.split()) >= 80:
                        cid = f"trans_{reel_id}_{int(chunk_start)}"
                        text_to_store = f"Caption: {caption}\nSpoken Transcript: {chunk_buffer.strip()}"
                        documents.append(text_to_store)
                        metadatas.append({
                            "reel_id": reel_id,
                            "type": "speech",
                            "start": str(chunk_start),
                            "end": str(seg.get("end", 0)),
                            "caption": caption[:100]
                        })
                        ids.append(cid)
                        chunk_buffer = ""
                        chunk_start = seg.get("end", 0)

                if chunk_buffer.strip():
                    cid = f"trans_{reel_id}_{int(chunk_start)}"
                    text_to_store = f"Caption: {caption}\nSpoken Transcript: {chunk_buffer.strip()}"
                    documents.append(text_to_store)
                    metadatas.append({
                        "reel_id": reel_id,
                        "type": "speech",
                        "start": str(chunk_start),
                        "end": "end",
                        "caption": caption[:100]
                    })
                    ids.append(cid)
            elif full_text:
                cid = f"trans_{reel_id}_0"
                text_to_store = f"{content_type.capitalize()}: {full_text}"
                documents.append(text_to_store)
                metadatas.append({
                    "reel_id": reel_id,
                    "type": content_type,
                    "start": "0",
                    "end": "end",
                    "caption": caption[:100]
                })
                ids.append(cid)
        except Exception:
            pass

    # Batch upsert into ChromaDB and Supabase
    if documents:
        BATCH_SIZE = 100
        print(f"\nProcessing {len(documents)} total knowledge chunks...")

        for i in range(0, len(documents), BATCH_SIZE):
            batch_docs = documents[i:i + BATCH_SIZE]
            batch_meta = metadatas[i:i + BATCH_SIZE]
            batch_ids = ids[i:i + BATCH_SIZE]

            # 1. Update ChromaDB
            if collection is not None:
                collection.upsert(
                    documents=batch_docs,
                    metadatas=batch_meta,
                    ids=batch_ids
                )

            # 2. Compute embeddings and sync to Supabase
            try:
                batch_embs = emb_fn(batch_docs)
                sync_batch_to_supabase(batch_ids, batch_docs, batch_meta, batch_embs)
            except Exception as e:
                logger.warning("Supabase batch sync error: %s", e)

        print(f"\n[OK] Successfully indexed {len(documents)} items across ChromaDB and Supabase pgvector!")


if __name__ == "__main__":
    print("Testing Unified Knowledge Base Search...")
    queries = [
        "0 argument kick out policy",
        "camera aur content creation",
        "Hataff Security Systems ex SSG"
    ]
    for q in queries:
        print(f"\nQuery: '{q}'")
        res = search_knowledge(q, n_results=2)
        for idx, r in enumerate(res, 1):
            print(f"  [{idx}] {r[:120].strip()}...")
