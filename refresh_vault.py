from pathlib import Path
from array import array
import sqlite3
import subprocess
import sys

from embed_vault import (
    chunk_note,
    embed_batch,
    word_count,
    BATCH_SIZE,
)

from config import VAULT, is_index_excluded
SOURCE_DB = Path("data/vault.db")
SEMANTIC_DB = Path("data/semantic.db")

def include_note(path):
    relative = path.relative_to(VAULT)
    return not is_index_excluded(relative)


def semantic_allowed(path):
    p = path.lower()

    return (
        "/meetings/" not in p
        and not p.startswith("copilot/")
    )


# --------------------------------------------------
# 1. Detect changes BEFORE rebuilding structural DB
# --------------------------------------------------

vault_files = {}

for path in VAULT.rglob("*.md"):
    if not include_note(path):
        continue

    relative = str(path.relative_to(VAULT))

    vault_files[relative] = path.read_text(
        encoding="utf-8",
        errors="replace",
    )


db = sqlite3.connect(SOURCE_DB)

indexed = {
    path: content
    for path, content in db.execute(
        "SELECT path, content FROM notes"
    )
}

db.close()


vault_paths = set(vault_files)
indexed_paths = set(indexed)

new_paths = sorted(vault_paths - indexed_paths)
deleted_paths = sorted(indexed_paths - vault_paths)

changed_paths = sorted(
    path
    for path in vault_paths & indexed_paths
    if vault_files[path] != indexed[path]
)


print()
print("OBSIDIANIZER REFRESH")
print("=" * 40)
print(f"New:       {len(new_paths)}")
print(f"Changed:   {len(changed_paths)}")
print(f"Deleted:   {len(deleted_paths)}")
print()


if not new_paths and not changed_paths and not deleted_paths:
    print("✓ Vault index is already current.")
    raise SystemExit(0)


# --------------------------------------------------
# 2. Rebuild cheap structural index
# --------------------------------------------------

print("Refreshing structural index...")

result = subprocess.run(
    [sys.executable, "index_vault.py"]
)

if result.returncode != 0:
    raise SystemExit(
        "Structural indexing failed. Semantic index untouched."
    )

print()
print("✓ Structural index refreshed.")


# --------------------------------------------------
# 3. Remove stale semantic chunks
# --------------------------------------------------

semantic = sqlite3.connect(SEMANTIC_DB)

affected_paths = sorted(
    set(new_paths)
    | set(changed_paths)
    | set(deleted_paths)
)

for path in affected_paths:
    semantic.execute(
        "DELETE FROM chunks WHERE path = ?",
        (path,),
    )


# --------------------------------------------------
# 4. Build chunks ONLY for new/changed notes
# --------------------------------------------------

source = sqlite3.connect(SOURCE_DB)

pending = []

for path in sorted(set(new_paths) | set(changed_paths)):

    if not semantic_allowed(path):
        continue

    row = source.execute(
        """
        SELECT title, content
        FROM notes
        WHERE path = ?
        """,
        (path,),
    ).fetchone()

    if not row:
        continue

    title, content = row

    chunks = chunk_note(content)

    for chunk_index, chunk in enumerate(chunks):
        pending.append(
            (
                path,
                title,
                chunk_index,
                chunk,
            )
        )


print()
print(f"Chunks requiring embeddings: {len(pending)}")


# --------------------------------------------------
# 5. Embed only those chunks
# --------------------------------------------------

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

        semantic.execute(
            """
            INSERT INTO chunks
            (
                path,
                title,
                chunk_index,
                content,
                word_count,
                embedding
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                path,
                title,
                chunk_index,
                content,
                word_count(content),
                blob,
            ),
        )

    processed += len(batch)

    print(
        f"Embedded {processed}/{len(pending)} chunks"
    )


semantic.commit()


chunk_count = semantic.execute(
    "SELECT COUNT(*) FROM chunks"
).fetchone()[0]

semantic_note_count = semantic.execute(
    "SELECT COUNT(DISTINCT path) FROM chunks"
).fetchone()[0]

semantic.close()
source.close()


print()
print("REFRESH COMPLETE")
print("=" * 40)
print(f"Structural notes:  {len(vault_paths)}")
print(f"Semantic notes:    {semantic_note_count}")
print(f"Semantic chunks:   {chunk_count}")
print()
print("✓ Obsidianizer is current.")
