"""
Hassan Ali Farrukh AI - Unified Multilingual Knowledge Base & Vector Engine
===========================================================================
Manages multilingual vector embeddings and semantic retrieval for Hassan Ali Farrukh's
reels, posts, captions, and spoken audio transcripts.

Features:
1. Primary: Multilingual Gemini Embeddings (`gemini-embedding-001`, 768-dim L2-normalized)
   + Supabase pgvector (`knowledge_vectors`) so Roman Urdu, Urdu script, and English
   queries accurately match Hassan's spoken Urdu & English talks.
2. Quality Filtering: Automatically strips Whisper music/song hallucinations and empty noise.
3. Lightweight Runtime: Lazy-loads local ChromaDB ONLY if Supabase/Gemini are unavailable,
   saving ~150-180 MB RAM per worker on the production VM.
"""

from functools import lru_cache
import glob
import json
import logging
import math
import os
from pathlib import Path
import re
import sys

# Configure UTF-8 for Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKSPACE_ROOT))

from src.config import (
    CHROMA_DB_DIR,
    GEMINI_API_KEY,
    POSTS_DIR,
    REELS_DIR,
    TRANSCRIPTS_DIR,
)
import src.database as _db_mod
from src.database import DBContext

logger = logging.getLogger("knowledge_base")

EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIM = 768
MIN_SIMILARITY_SCORE = 0.45

# Noise & foreign-script filters (strips Hindi/Punjabi song lyrics, CJK spam, Whisper hallucinations)
_FOREIGN_SCRIPT_RE = re.compile(
    r"[\u0900-\u097F\u0A00-\u0A7F\u0A80-\u0DFF\u3000-\u9FFF\uAC00-\uD7AF\u0400-\u04FF]"
)
_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_STRIP_NOISE_RE = re.compile(
    r"(©\s*BF-WATCH TV.*$|Terima kasih(?:\s+telah menonton)?[.!]?|"
    r"Thank you for watching[.!]?|Subtitles by\s+\S+.*$|"
    r"amara\.org.*$|请不吝点赞.*$|[♪🎵]+)",
    re.IGNORECASE,
)
_FILLER_PHRASES = {
    "thank you",
    "thanks",
    "ready",
    "for me",
    "music",
    "موسیقی",
    "bye",
    "hello",
}

_genai_client = None
_chroma_collection = None
_chroma_attempted = False


def _get_genai_client():
    global _genai_client
    if _genai_client is not None:
        return _genai_client
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai

        _genai_client = genai.Client(api_key=GEMINI_API_KEY)
        return _genai_client
    except Exception as e:
        logger.warning("Gemini client init failed: %s", e)
        return None


def _get_chroma_collection():
    """Lazily initializes local ChromaDB only when needed (keeps production RAM low)."""
    global _chroma_collection, _chroma_attempted
    if _chroma_attempted:
        return _chroma_collection
    _chroma_attempted = True
    try:
        import chromadb
        from chromadb.utils import embedding_functions

        emb_fn = embedding_functions.DefaultEmbeddingFunction()
        client = chromadb.PersistentClient(path=str(CHROMA_DB_DIR))
        _chroma_collection = client.get_or_create_collection(
            name="hassan_knowledge",
            embedding_function=emb_fn,
        )
    except Exception as e:
        logger.warning("Local ChromaDB initialization skipped: %s", e)
        _chroma_collection = None
    return _chroma_collection


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 0.0:
        return vec
    return [v / norm for v in vec]


def embed_texts_gemini(
    texts: list[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> list[list[float]]:
    """
    Generates L2-normalized 768-dim multilingual embeddings using `gemini-embedding-001`.
    Works natively across Roman Urdu, Urdu script, and English.
    """
    client = _get_genai_client()
    if client is None or not texts:
        return []

    from google.genai import types

    results: list[list[float]] = []
    batch_size = 50
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        resp = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=batch,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=EMBEDDING_DIM,
            ),
        )
        for emb_obj in resp.embeddings:
            results.append(_l2_normalize(list(emb_obj.values)))
    return results


@lru_cache(maxsize=256)
def _embed_query_cached(query: str) -> tuple[float, ...] | None:
    embs = embed_texts_gemini([query], task_type="QUESTION_ANSWERING")
    if not embs:
        return None
    return tuple(embs[0])


def clean_and_validate_chunk(content: str, content_type: str = "speech") -> tuple[bool, str, str]:
    """
    Strips Whisper music/caption noise and rejects gibberish, foreign-script songs,
    and empty chunks before indexing into vector search.
    Returns (is_valid, cleaned_content, reason).
    """
    text = (content or "").strip()
    if not text or "[visual reel without audio track]" in text.lower():
        return False, "", "visual_only"

    text = _STRIP_NOISE_RE.sub("", text).strip()
    if "Spoken Transcript:" in text:
        prefix, spoken = text.split("Spoken Transcript:", 1)
        spoken = spoken.strip()
    else:
        prefix = ""
        spoken = re.sub(
            r"^(Topic / Caption:|Feed Post / Caption:|Caption:|Speech:)\s*",
            "",
            text,
        ).strip()
        spoken = re.sub(r"\nDate:.*$", "", spoken, flags=re.DOTALL).strip()

    # Reject chunks dominated by non-Hassan scripts (Hindi Devanagari songs, Gurmukhi songs, CJK, Cyrillic)
    foreign_chars = len(_FOREIGN_SCRIPT_RE.findall(spoken))
    total_letters = max(1, len(_LETTER_RE.findall(spoken)))
    if foreign_chars >= 12 and (foreign_chars / total_letters) >= 0.50:
        return False, "", f"foreign_script({foreign_chars}/{total_letters})"

    # Strip hashtags & @mentions to verify meaningful content length
    cleaned = re.sub(r"[#@]\S+", "", spoken)
    cleaned_letters = _LETTER_RE.findall(cleaned)
    if content_type == "speech":
        if len(cleaned_letters) < 25:
            return False, "", f"too_short_speech({len(cleaned_letters)})"
        if cleaned.strip().lower().rstrip(".!? ") in _FILLER_PHRASES:
            return False, "", "filler_phrase"
    else:
        if len(cleaned_letters) < 20:
            return False, "", f"too_short_caption({len(cleaned_letters)})"

    rebuilt = f"{prefix}Spoken Transcript: {spoken}".strip() if prefix else text
    return True, rebuilt, "ok"


def _search_knowledge_pg_keywords(query: str, n_results: int = 4) -> list[str]:
    """Lightweight keyword fallback in PostgreSQL if Gemini embedding API is temporarily throttled."""
    words = [w for w in re.findall(r"[A-Za-z0-9\u0600-\u06FF]{4,}", query or "")][:5]
    if not words:
        return []
    try:
        with DBContext() as db:
            if not db.is_pg:
                return []
            clauses = " OR ".join(["content ILIKE %s"] * len(words))
            params = tuple(f"%{w}%" for w in words) + (n_results,)
            db.execute(
                f"""
                SELECT content FROM knowledge_vectors
                WHERE {clauses}
                ORDER BY CASE WHEN content_type = 'speech' THEN 0 ELSE 1 END, LENGTH(content) DESC
                LIMIT %s;
                """,
                params,
            )
            return [r["content"] for r in db.fetchall()]
    except Exception:
        return []


def search_knowledge_supabase(query: str, n_results: int = 4) -> list[str]:
    """
    Retrieves top matching knowledge chunks from Supabase pgvector using multilingual
    Gemini embeddings. Prioritizes Hassan's spoken transcripts and caps duplicates per reel.
    """
    clean_q = (query or "").strip()
    if not clean_q or not _db_mod.DATABASE_URL:
        return []

    try:
        query_emb = _embed_query_cached(clean_q)
        if not query_emb:
            return _search_knowledge_pg_keywords(clean_q, n_results=n_results)
        emb_str = "[" + ",".join(f"{x:.8f}" for x in query_emb) + "]"
        fetch_k = max(n_results * 3, 12)

        with DBContext() as db:
            if not db.is_pg:
                return []
            db.execute(
                """
                SELECT reel_id, content_type, content,
                       1 - (embedding <=> %s::vector) AS similarity
                FROM knowledge_vectors
                ORDER BY embedding <=> %s::vector
                LIMIT %s;
                """,
                (emb_str, emb_str, fetch_k),
            )
            rows = [dict(r) for r in db.fetchall()]
            if not rows:
                return []

        # Re-rank slightly favoring actual spoken talks over bare short captions,
        # drop low-similarity noise, and diversify across reels (max 2 per reel_id).
        scored = []
        for r in rows:
            sim = float(r.get("similarity") or 0.0)
            if sim < MIN_SIMILARITY_SCORE:
                continue
            ctype = (r.get("content_type") or "").lower()
            boost = 0.035 if ctype == "speech" else 0.0
            scored.append((sim + boost, r))

        scored.sort(key=lambda item: item[0], reverse=True)

        selected: list[str] = []
        reel_counts: dict[str, int] = {}
        for _, r in scored:
            rid = str(r.get("reel_id") or "")
            if rid and reel_counts.get(rid, 0) >= 2:
                continue
            selected.append(r["content"])
            if rid:
                reel_counts[rid] = reel_counts.get(rid, 0) + 1
            if len(selected) >= n_results:
                break

        return selected
    except Exception as e:
        logger.warning("Supabase pgvector search failed: %s", e)
        return _search_knowledge_pg_keywords(clean_q, n_results=n_results)


def search_knowledge_chroma(query: str, n_results: int = 4) -> list[str]:
    """Offline fallback: retrieves matching segments from local ChromaDB."""
    coll = _get_chroma_collection()
    if coll is None:
        return []
    try:
        results = coll.query(query_texts=[query], n_results=n_results)
        if results and results.get("documents") and results["documents"][0]:
            return results["documents"][0]
    except Exception as e:
        logger.error("ChromaDB search error: %s", e)
    return []


def search_knowledge(query: str, n_results: int = 4) -> list[str]:
    """
    Unified knowledge search.
    Uses Supabase pgvector + multilingual Gemini embeddings in production;
    falls back to local ChromaDB only when offline or unconfigured.
    """
    if _db_mod.DATABASE_URL and GEMINI_API_KEY:
        results = search_knowledge_supabase(query, n_results=n_results)
        if results:
            return results
        # If Supabase is live and simply found no relevant chunks above threshold,
        # avoid loading heavy local ChromaDB on the production VM.
        return []

    return search_knowledge_chroma(query, n_results=n_results)


def sync_batch_to_supabase(
    ids: list[str],
    documents: list[str],
    metadatas: list[dict],
    embeddings: list[list[float]],
):
    """Upserts a batch of documents and 768-dim Gemini embeddings into Supabase knowledge_vectors."""
    try:
        import psycopg2
        from psycopg2.extras import execute_batch

        if not _db_mod.DATABASE_URL:
            return

        conn = psycopg2.connect(_db_mod.DATABASE_URL, connect_timeout=15)
        conn.autocommit = True
        cur = conn.cursor()

        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS knowledge_vectors (
                id TEXT PRIMARY KEY,
                reel_id TEXT,
                content_type TEXT,
                caption TEXT,
                content TEXT,
                embedding vector({EMBEDDING_DIM}),
                metadata JSONB,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

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
    """Reads all transcribed JSON files and reel metadata captions, filters noise, and indexes via Gemini."""
    print("==================================================")
    print("  INDEXING KNOWLEDGE: REELS, POSTS & TRANSCRIPTS  ")
    print("==================================================")

    documents: list[str] = []
    metadatas: list[dict] = []
    ids: list[str] = []
    dropped = 0

    def _add_if_valid(cid: str, raw_doc: str, meta: dict, ctype: str):
        nonlocal dropped
        ok, cleaned_doc, _ = clean_and_validate_chunk(raw_doc, ctype)
        if not ok:
            dropped += 1
            return
        ids.append(cid)
        documents.append(cleaned_doc)
        metadatas.append(meta)

    # 1. Index reel metadata captions
    meta_files = glob.glob(str(REELS_DIR / "*.json"))
    print(f"Found {len(meta_files)} reel metadata files...")

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
                _add_if_valid(
                    f"meta_{base_id}",
                    f"Topic / Caption: {desc}\nDate: {date}",
                    {
                        "reel_id": base_id,
                        "type": "caption",
                        "date": date,
                        "caption": desc[:100],
                    },
                    "caption",
                )
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
                _add_if_valid(
                    f"post_{base_id}",
                    f"Feed Post / Caption: {desc}\nDate: {date}",
                    {
                        "reel_id": base_id,
                        "type": "post_caption",
                        "date": date,
                        "caption": desc[:100],
                    },
                    "post_caption",
                )
        except Exception:
            pass

    # 2. Index transcribed speech files from data/transcripts/
    transcript_files = glob.glob(str(TRANSCRIPTS_DIR / "*.json"))
    print(f"Found {len(transcript_files)} audio transcript files...")

    for fpath in transcript_files:
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            reel_id = (
                data.get("reel_id")
                or data.get("story_id")
                or data.get("shortcode")
                or os.path.splitext(os.path.basename(fpath))[0]
            )
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
                        _add_if_valid(
                            f"trans_{reel_id}_{int(chunk_start)}",
                            f"Caption: {caption}\nSpoken Transcript: {chunk_buffer.strip()}",
                            {
                                "reel_id": reel_id,
                                "type": "speech",
                                "start": str(chunk_start),
                                "end": str(seg.get("end", 0)),
                                "caption": caption[:100],
                            },
                            "speech",
                        )
                        chunk_buffer = ""
                        chunk_start = seg.get("end", 0)

                if chunk_buffer.strip():
                    _add_if_valid(
                        f"trans_{reel_id}_{int(chunk_start)}",
                        f"Caption: {caption}\nSpoken Transcript: {chunk_buffer.strip()}",
                        {
                            "reel_id": reel_id,
                            "type": "speech",
                            "start": str(chunk_start),
                            "end": "end",
                            "caption": caption[:100],
                        },
                        "speech",
                    )
            elif full_text:
                _add_if_valid(
                    f"trans_{reel_id}_0",
                    f"{content_type.capitalize()}: {full_text}",
                    {
                        "reel_id": reel_id,
                        "type": content_type,
                        "start": "0",
                        "end": "end",
                        "caption": caption[:100],
                    },
                    content_type,
                )
        except Exception:
            pass

    if documents:
        batch_size = 100
        print(f"\nProcessing {len(documents)} clean chunks (filtered {dropped} noisy/short chunks)...")

        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i : i + batch_size]
            batch_meta = metadatas[i : i + batch_size]
            batch_ids = ids[i : i + batch_size]

            if _db_mod.DATABASE_URL and GEMINI_API_KEY:
                batch_embs = embed_texts_gemini(batch_docs, task_type="RETRIEVAL_DOCUMENT")
                sync_batch_to_supabase(batch_ids, batch_docs, batch_meta, batch_embs)
            else:
                coll = _get_chroma_collection()
                if coll is not None:
                    coll.upsert(documents=batch_docs, metadatas=batch_meta, ids=batch_ids)

        print(f"\n[OK] Indexed {len(documents)} clean chunks using {EMBEDDING_MODEL} ({EMBEDDING_DIM}d)!")


if __name__ == "__main__":
    print("Testing Unified Multilingual Knowledge Base Search...")
    queries = [
        "discipline nahi ban raha routine toot jati hai",
        "mere desi parents bahut toxic hai",
        "career ki taraf jana chahiye ya shadi",
        "overthinking hoti hai dimagh sukoon mein nahi rehta",
    ]
    for q in queries:
        print(f"\nQuery: '{q}'")
        res = search_knowledge(q, n_results=3)
        for idx, r in enumerate(res, 1):
            print(f"  [{idx}] {r[:140].replace(chr(10), ' | ')}...")
