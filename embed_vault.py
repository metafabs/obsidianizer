import sqlite3
import re
import json
import urllib.request
from array import array

SOURCE_DB = "data/vault.db"
SEMANTIC_DB = "data/semantic.db"

MODEL = "qwen3-embedding:4b"

WHOLE_NOTE_LIMIT = 600
TARGET_WORDS = 550
MAX_WORDS = 700
OVERLAP_WORDS = 75

BATCH_SIZE = 8


def word_count(text):
    return len(re.findall(r"\b\w+\b", text))


def tail_words(text, count):
    words = text.split()
    return " ".join(words[-count:])


def chunk_note(text):
    total = word_count(text)

    if total <= WHOLE_NOTE_LIMIT:
        return [text.strip()]

    blocks = re.split(r"\n\s*\n", text)
    blocks = [b.strip() for b in blocks if b.strip()]

    chunks = []
    current = []
    current_words = 0

    for block in blocks:
        block_words = word_count(block)

        if block_words > MAX_WORDS:
            words = block.split()

            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_words = 0

            start = 0

            while start < len(words):
                end = min(start + TARGET_WORDS, len(words))
                chunks.append(" ".join(words[start:end]))

                if end == len(words):
                    break

                start = max(end - OVERLAP_WORDS, start + 1)

            continue

        if current and current_words + block_words > MAX_WORDS:
            finished = "\n\n".join(current)
            chunks.append(finished)

            overlap = tail_words(finished, OVERLAP_WORDS)

            current = [overlap, block] if overlap else [block]
            current_words = word_count(overlap) + block_words

        else:
            current.append(block)
            current_words += block_words

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def embed_batch(texts):
    payload = json.dumps({
        "model": MODEL,
        "input": texts
    }).encode("utf-8")

    request = urllib.request.Request(
        "http://localhost:11434/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.load(response)

    return data["embeddings"]


def rebuild_all():
    source = sqlite3.connect(SOURCE_DB)

    notes = source.execute("""
        SELECT path, title, content
        FROM notes
        WHERE lower(path) NOT LIKE '%/meetings/%'
          AND lower(path) NOT LIKE 'copilot/%'
        ORDER BY path
    """).fetchall()

    semantic = sqlite3.connect(SEMANTIC_DB)

    semantic.executescript("""
        DROP TABLE IF EXISTS chunks;

        CREATE TABLE chunks (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            word_count INTEGER NOT NULL,
            embedding BLOB NOT NULL
        );

        CREATE INDEX idx_chunks_path
        ON chunks(path);
    """)

    pending = []

    for path, title, content in notes:
        chunks = chunk_note(content)

        for chunk_index, chunk in enumerate(chunks):
            pending.append(
                (path, title, chunk_index, chunk)
            )

    print(f"Notes: {len(notes)}")
    print(f"Chunks to embed: {len(pending)}")
    print(f"Model: {MODEL}")
    print()

    processed = 0

    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]

        texts = [
            f"Title: {title}\n\n{text}"
            for path, title, index, text in batch
        ]

        embeddings = embed_batch(texts)

        for item, vector in zip(batch, embeddings):
            path, title, chunk_index, content = item

            blob = array("f", vector).tobytes()

            semantic.execute("""
                INSERT INTO chunks
                (path, title, chunk_index, content, word_count, embedding)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                path,
                title,
                chunk_index,
                content,
                word_count(content),
                blob,
            ))

        semantic.commit()

        processed += len(batch)

        print(
            f"Embedded {processed}/{len(pending)} chunks"
        )

    count = semantic.execute(
        "SELECT COUNT(*) FROM chunks"
    ).fetchone()[0]

    semantic.close()
    source.close()

    print()
    print(f"Semantic database: {SEMANTIC_DB}")
    print(f"Chunks stored: {count}")
    print("EMBEDDING COMPLETE")


if __name__ == "__main__":
    rebuild_all()
