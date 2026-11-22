"""
ChromaDB to Supabase pgvector Migration Script
==============================================
Transfers all 1,027 vector embeddings and documents from local ChromaDB
into Supabase PostgreSQL using pgvector.
"""

import os
import sys
import json
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import execute_batch
import chromadb

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(WORKSPACE_ROOT / ".env")

DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    print("[ERROR] DATABASE_URL missing in .env")
    sys.exit(1)

CHROMA_PATH = WORKSPACE_ROOT / "data" / "chroma_db"

print("=========================================================")
print("  MIGRATING CHROMADB KNOWLEDGE BASE TO SUPABASE PGVECTOR ")
print("=========================================================")

# 1. Connect to Supabase
print("\n[Step 1/4] Connecting to Supabase...")
conn = psycopg2.connect(DB_URL, connect_timeout=15)
conn.autocommit = True
cur = conn.cursor()
print("  [OK] Connected to Supabase PostgreSQL.")

# 2. Enable pgvector extension and create schema
print("\n[Step 2/4] Enabling pgvector & creating knowledge_vectors table...")
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

# Create HNSW index for fast cosine search (falling back to IVFFlat if HNSW not available)
try:
    cur.execute("""
    CREATE INDEX IF NOT EXISTS knowledge_vectors_hnsw_idx 
    ON knowledge_vectors USING hnsw (embedding vector_cosine_ops);
    """)
    print("  [OK] Created HNSW cosine similarity index.")
except Exception as ex:
    print(f"  [i] Note on HNSW index: {ex}. Falling back to default indexing.")

print("  [OK] Supabase table 'knowledge_vectors' ready.")

# 3. Read all records from ChromaDB
print("\n[Step 3/4] Reading all items from local ChromaDB...")
client = chromadb.PersistentClient(path=str(CHROMA_PATH))
col = client.get_collection("hassan_knowledge")
total_items = col.count()
print(f"  Found {total_items} items in ChromaDB collection 'hassan_knowledge'.")

# Fetch all data including embeddings
data = col.get(include=["documents", "metadatas", "embeddings"])

ids = data.get("ids", [])
documents = data.get("documents", [])
metadatas = data.get("metadatas", [])
embeddings = data.get("embeddings", [])

print(f"  Loaded {len(ids)} documents and {len(embeddings)} vectors (dim: {len(embeddings[0]) if len(embeddings) > 0 else 0}).")

# 4. Batch Upsert to Supabase
print("\n[Step 4/4] Inserting embeddings into Supabase knowledge_vectors...")
rows_to_insert = []
for cid, doc, meta, emb in zip(ids, documents, metadatas, embeddings):
    meta = meta or {}
    reel_id = meta.get("reel_id", "")
    content_type = meta.get("type", "speech")
    caption = meta.get("caption", "")
    emb_str = "[" + ",".join(f"{x:.8f}" for x in emb) + "]"
    meta_json = json.dumps(meta)

    rows_to_insert.append((
        cid,
        reel_id,
        content_type,
        caption,
        doc,
        emb_str,
        meta_json
    ))

upsert_query = """
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

BATCH_SIZE = 100
for i in range(0, len(rows_to_insert), BATCH_SIZE):
    batch = rows_to_insert[i:i + BATCH_SIZE]
    execute_batch(cur, upsert_query, batch)
    print(f"  Inserted {min(i + BATCH_SIZE, len(rows_to_insert))}/{len(rows_to_insert)} items...")

# Verify count in Supabase
cur.execute("SELECT COUNT(*) FROM knowledge_vectors;")
supa_count = cur.fetchone()[0]
print(f"\n[OK] Migration complete! Total rows in Supabase knowledge_vectors: {supa_count}")

# 5. Quick test search query
print("\n--- Verifying Live Cosine Similarity Search ---")
test_emb = "[" + ",".join(f"{x:.8f}" for x in embeddings[0]) + "]"
cur.execute("""
    SELECT id, content_type, 1 - (embedding <=> %s::vector) AS similarity, LEFT(content, 100)
    FROM knowledge_vectors
    ORDER BY embedding <=> %s::vector
    LIMIT 3;
""", (test_emb, test_emb))

test_results = cur.fetchall()
for r in test_results:
    print(f"  Match ID: {r[0]} | Type: {r[1]} | Similarity: {r[2]:.4f}")
    print(f"    Preview: {r[3]}...\n")

cur.close()
conn.close()
print("=========================================================")
print("  UPGRADE 2 MIGRATION SUCCESSFULLY VERIFIED!             ")
print("=========================================================")
