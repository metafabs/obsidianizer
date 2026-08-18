from array import array
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3

from config import VAULT, is_index_excluded
from embed_vault import BATCH_SIZE, chunk_note, embed_batch, word_count
from index_vault import parse_note


SOURCE_DB = Path("data/vault.db")
SEMANTIC_DB = Path("data/semantic.db")


@dataclass(frozen=True)
class FileMetadata:
    path: str
    source: Path
    modified: str
    modified_ns: int
    created: str | None
    size: int


@dataclass(frozen=True)
class StructuralReport:
    total: int
    new: int
    updated: int
    deleted: int


@dataclass(frozen=True)
class SemanticReport:
    missing: int
    stale: int
    orphaned: int
    chunks_embedded: int
    notes: int
    chunks: int


def include_note(path, vault=VAULT):
    relative = path.relative_to(vault)
    return not is_index_excluded(relative)


def semantic_allowed(path):
    p = path.lower()

    return (
        "/meetings/" not in p
        and not p.startswith("copilot/")
    )


def _iso_timestamp(timestamp):
    return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")


def inventory_vault(vault):
    inventory = {}

    for source in vault.rglob("*.md"):
        if not include_note(source, vault):
            continue

        stat = source.stat()
        relative = str(source.relative_to(vault))
        birth_time = getattr(stat, "st_birthtime", None)

        inventory[relative] = FileMetadata(
            path=relative,
            source=source,
            modified=_iso_timestamp(stat.st_mtime),
            modified_ns=stat.st_mtime_ns,
            created=(
                _iso_timestamp(birth_time)
                if birth_time is not None
                else None
            ),
            size=stat.st_size,
        )

    return inventory


def ensure_structural_metadata_columns(conn):
    columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(notes)")
    }

    if not columns:
        raise RuntimeError(
            "Structural database is not initialized. Run index_vault.py first."
        )

    additions = {
        "modified_ns": "INTEGER",
        "created": "TEXT",
        "size": "INTEGER",
    }

    for name, column_type in additions.items():
        if name not in columns:
            conn.execute(
                f"ALTER TABLE notes ADD COLUMN {name} {column_type}"
            )


def _replace_related_rows(conn, note_id, note):
    conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM links WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM headings WHERE note_id = ?", (note_id,))

    conn.executemany(
        "INSERT INTO note_tags (note_id, tag) VALUES (?, ?)",
        [(note_id, tag) for tag in note["tags"]],
    )
    conn.executemany(
        "INSERT INTO links (note_id, target) VALUES (?, ?)",
        [(note_id, link) for link in note["links"]],
    )
    conn.executemany(
        "INSERT INTO headings (note_id, heading) VALUES (?, ?)",
        [(note_id, heading) for heading in note["headings"]],
    )


def _insert_note(conn, note):
    cursor = conn.execute(
        """
        INSERT INTO notes
        (
            path, folder, title, content, word_count, modified,
            modified_ns, created, size
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            note["path"],
            note["folder"],
            note["title"],
            note["content"],
            note["word_count"],
            note["modified"],
            note["modified_ns"],
            note["created"],
            note["size"],
        ),
    )
    _replace_related_rows(conn, cursor.lastrowid, note)


def _update_note(conn, note_id, note):
    conn.execute(
        """
        UPDATE notes
        SET folder = ?, title = ?, content = ?, word_count = ?,
            modified = ?, modified_ns = ?, created = ?, size = ?
        WHERE id = ?
        """,
        (
            note["folder"],
            note["title"],
            note["content"],
            note["word_count"],
            note["modified"],
            note["modified_ns"],
            note["created"],
            note["size"],
            note_id,
        ),
    )
    _replace_related_rows(conn, note_id, note)


def _delete_note(conn, note_id):
    conn.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM links WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM headings WHERE note_id = ?", (note_id,))
    conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))


def sync_structural(vault, source_db):
    inventory = inventory_vault(vault)
    conn = sqlite3.connect(source_db)

    try:
        with conn:
            ensure_structural_metadata_columns(conn)

            indexed = {
                row[1]: {
                    "id": row[0],
                    "content": row[2],
                    "modified": row[3],
                    "modified_ns": row[4],
                    "size": row[5],
                }
                for row in conn.execute(
                    """
                    SELECT id, path, content, modified, modified_ns, size
                    FROM notes
                    """
                )
            }

            vault_paths = set(inventory)
            indexed_paths = set(indexed)
            new_paths = sorted(vault_paths - indexed_paths)
            deleted_paths = sorted(indexed_paths - vault_paths)
            updated_notes = {}
            metadata_only = {}

            for path in sorted(vault_paths & indexed_paths):
                metadata = inventory[path]
                previous = indexed[path]

                if previous["modified_ns"] is not None:
                    metadata_changed = (
                        metadata.modified_ns != previous["modified_ns"]
                        or metadata.size != previous["size"]
                    )
                else:
                    metadata_changed = (
                        metadata.modified != previous["modified"]
                        or (
                            previous["size"] is not None
                            and metadata.size != previous["size"]
                        )
                    )

                if not metadata_changed:
                    if (
                        previous["modified_ns"] is None
                        or previous["size"] is None
                    ):
                        metadata_only[path] = metadata
                    continue

                note = parse_note(metadata.source, vault=vault)

                if note["content"] != previous["content"]:
                    updated_notes[path] = note
                else:
                    metadata_only[path] = metadata

            new_notes = {
                path: parse_note(inventory[path].source, vault=vault)
                for path in new_paths
            }

            for path in deleted_paths:
                _delete_note(conn, indexed[path]["id"])

            for path, note in updated_notes.items():
                _update_note(conn, indexed[path]["id"], note)

            for path, note in new_notes.items():
                _insert_note(conn, note)

            conn.executemany(
                """
                UPDATE notes
                SET modified = ?, modified_ns = ?, created = ?, size = ?
                WHERE path = ?
                """,
                [
                    (
                        metadata.modified,
                        metadata.modified_ns,
                        metadata.created,
                        metadata.size,
                        path,
                    )
                    for path, metadata in metadata_only.items()
                ],
            )
    finally:
        conn.close()

    return StructuralReport(
        total=len(inventory),
        new=len(new_paths),
        updated=len(updated_notes),
        deleted=len(deleted_paths),
    )


def ensure_semantic_schema(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            word_count INTEGER NOT NULL,
            embedding BLOB NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_path
        ON chunks(path);
        """
    )


def sync_semantic(source_db, semantic_db, embed_batch_fn=embed_batch):
    source = sqlite3.connect(source_db)
    semantic = sqlite3.connect(semantic_db)

    try:
        eligible = {
            path: (title, content)
            for path, title, content in source.execute(
                "SELECT path, title, content FROM notes ORDER BY path"
            )
            if semantic_allowed(path)
        }

        ensure_semantic_schema(semantic)
        stored = {}

        for path, title, chunk_index, content in semantic.execute(
            """
            SELECT path, title, chunk_index, content
            FROM chunks
            ORDER BY path, chunk_index, id
            """
        ):
            stored.setdefault(path, []).append(
                (chunk_index, title, content)
            )

        eligible_paths = set(eligible)
        stored_paths = set(stored)
        missing_paths = sorted(eligible_paths - stored_paths)
        orphaned_paths = sorted(stored_paths - eligible_paths)
        stale_paths = []
        expected_chunks = {}

        for path, (title, content) in eligible.items():
            expected = [
                (chunk_index, title, chunk)
                for chunk_index, chunk in enumerate(chunk_note(content))
            ]
            expected_chunks[path] = expected

            if path in stored and stored[path] != expected:
                stale_paths.append(path)

        repair_paths = sorted(set(missing_paths) | set(stale_paths))
        pending = [
            (path, title, chunk_index, content)
            for path in repair_paths
            for chunk_index, title, content in expected_chunks[path]
        ]

        with semantic:
            semantic.executemany(
                "DELETE FROM chunks WHERE path = ?",
                [
                    (path,)
                    for path in sorted(
                        set(orphaned_paths) | set(stale_paths)
                    )
                ],
            )

            processed = 0

            for start in range(0, len(pending), BATCH_SIZE):
                batch = pending[start:start + BATCH_SIZE]
                texts = [
                    f"Title: {title}\n\n{content}"
                    for path, title, chunk_index, content in batch
                ]
                embeddings = embed_batch_fn(texts)

                if len(embeddings) != len(batch):
                    raise RuntimeError(
                        "Embedding service returned an unexpected batch size."
                    )

                rows = []

                for item, vector in zip(batch, embeddings):
                    path, title, chunk_index, content = item
                    rows.append(
                        (
                            path,
                            title,
                            chunk_index,
                            content,
                            word_count(content),
                            array("f", vector).tobytes(),
                        )
                    )

                semantic.executemany(
                    """
                    INSERT INTO chunks
                    (
                        path, title, chunk_index, content,
                        word_count, embedding
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )

                processed += len(batch)
                print(f"Embedded {processed}/{len(pending)} chunks")

        semantic_note_count = semantic.execute(
            "SELECT COUNT(DISTINCT path) FROM chunks"
        ).fetchone()[0]
        chunk_count = semantic.execute(
            "SELECT COUNT(*) FROM chunks"
        ).fetchone()[0]
    finally:
        semantic.close()
        source.close()

    return SemanticReport(
        missing=len(missing_paths),
        stale=len(stale_paths),
        orphaned=len(orphaned_paths),
        chunks_embedded=len(pending),
        notes=semantic_note_count,
        chunks=chunk_count,
    )


def refresh(
    vault=VAULT,
    source_db=SOURCE_DB,
    semantic_db=SEMANTIC_DB,
    embed_batch_fn=embed_batch,
):
    structural = sync_structural(vault, source_db)
    semantic = sync_semantic(
        source_db,
        semantic_db,
        embed_batch_fn=embed_batch_fn,
    )
    return structural, semantic


def print_report(structural, semantic):
    print()
    print("OBSIDIANIZER REFRESH")
    print("=" * 40)
    print(f"Total notes:          {structural.total}")
    print(f"New:                  {structural.new}")
    print(f"Updated:              {structural.updated}")
    print(f"Deleted:              {structural.deleted}")
    print()
    print(f"Semantic missing:     {semantic.missing}")
    print(f"Semantic stale:       {semantic.stale}")
    print(f"Semantic orphaned:    {semantic.orphaned}")
    print(f"Chunks embedded:      {semantic.chunks_embedded}")
    print()

    no_changes = (
        structural.new == 0
        and structural.updated == 0
        and structural.deleted == 0
        and semantic.missing == 0
        and semantic.stale == 0
        and semantic.orphaned == 0
    )

    if no_changes:
        print("Vault is already current.")
        print("Both structural and semantic indexes are current.")
        return

    print("REFRESH COMPLETE")
    print("=" * 40)
    print(f"Structural notes:     {structural.total}")
    print(f"Semantic notes:       {semantic.notes}")
    print(f"Semantic chunks:      {semantic.chunks}")
    print()
    print("Obsidianizer is current.")


def main():
    structural, semantic = refresh()
    print_report(structural, semantic)


if __name__ == "__main__":
    main()
