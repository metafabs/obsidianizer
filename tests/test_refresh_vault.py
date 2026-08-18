import sqlite3
import tempfile
import unittest
from pathlib import Path

from index_vault import create_database
from refresh_vault import ensure_semantic_schema, refresh


class FakeEmbedder:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, texts):
        self.calls.append(list(texts))

        if self.error:
            raise self.error

        return [[0.25, 0.5] for _ in texts]

    @property
    def embedded(self):
        return sum(len(call) for call in self.calls)


class RefreshVaultTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.vault = root / "vault"
        self.vault.mkdir()
        self.source_db = root / "vault.db"
        self.semantic_db = root / "semantic.db"

        source = sqlite3.connect(self.source_db)
        create_database(source)
        source.close()

        semantic = sqlite3.connect(self.semantic_db)
        ensure_semantic_schema(semantic)
        semantic.close()

    def tearDown(self):
        self.temp.cleanup()

    def write_note(self, relative, content):
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def semantic_paths(self):
        conn = sqlite3.connect(self.semantic_db)
        paths = {
            row[0]
            for row in conn.execute("SELECT DISTINCT path FROM chunks")
        }
        conn.close()
        return paths

    def test_incremental_refresh_and_independent_semantic_repair(self):
        alpha = self.write_note("alpha.md", "# Alpha\n\nfirst version")
        self.write_note("folder/beta.md", "# Beta\n\nfirst version")
        self.write_note("copilot/background.md", "excluded from semantics")
        self.write_note("_agent-instructions/control.md", "excluded entirely")

        first_embedder = FakeEmbedder()
        structural, semantic = refresh(
            self.vault,
            self.source_db,
            self.semantic_db,
            first_embedder,
        )

        self.assertEqual((structural.total, structural.new), (3, 3))
        self.assertEqual(
            (semantic.missing, semantic.stale, semantic.orphaned),
            (2, 0, 0),
        )
        self.assertEqual(semantic.chunks_embedded, 2)

        alpha.touch()
        metadata_only_embedder = FakeEmbedder()
        structural, semantic = refresh(
            self.vault,
            self.source_db,
            self.semantic_db,
            metadata_only_embedder,
        )
        self.assertEqual(
            (structural.new, structural.updated, structural.deleted),
            (0, 0, 0),
        )
        self.assertEqual(
            (semantic.missing, semantic.stale, semantic.orphaned),
            (0, 0, 0),
        )
        self.assertEqual(metadata_only_embedder.embedded, 0)

        no_work_embedder = FakeEmbedder()
        structural, semantic = refresh(
            self.vault,
            self.source_db,
            self.semantic_db,
            no_work_embedder,
        )
        self.assertEqual(
            (structural.new, structural.updated, structural.deleted),
            (0, 0, 0),
        )
        self.assertEqual(
            (semantic.missing, semantic.stale, semantic.orphaned),
            (0, 0, 0),
        )
        self.assertEqual(no_work_embedder.embedded, 0)

        alpha.unlink()
        self.write_note("folder/beta.md", "# Beta\n\nsecond version")
        self.write_note("gamma.md", "# Gamma\n\nnew note")

        change_embedder = FakeEmbedder()
        structural, semantic = refresh(
            self.vault,
            self.source_db,
            self.semantic_db,
            change_embedder,
        )
        self.assertEqual(
            (structural.new, structural.updated, structural.deleted),
            (1, 1, 1),
        )
        self.assertEqual(
            (semantic.missing, semantic.stale, semantic.orphaned),
            (1, 1, 1),
        )
        self.assertEqual(semantic.chunks_embedded, 2)

        conn = sqlite3.connect(self.semantic_db)
        conn.execute("DELETE FROM chunks WHERE path = ?", ("gamma.md",))
        conn.execute(
            "UPDATE chunks SET content = ? WHERE path = ?",
            ("stale chunk", "folder/beta.md"),
        )
        conn.execute(
            """
            INSERT INTO chunks
            (path, title, chunk_index, content, word_count, embedding)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("orphan.md", "Orphan", 0, "orphan", 1, b"vector"),
        )
        conn.commit()
        conn.close()

        repair_embedder = FakeEmbedder()
        structural, semantic = refresh(
            self.vault,
            self.source_db,
            self.semantic_db,
            repair_embedder,
        )
        self.assertEqual(
            (structural.new, structural.updated, structural.deleted),
            (0, 0, 0),
        )
        self.assertEqual(
            (semantic.missing, semantic.stale, semantic.orphaned),
            (1, 1, 1),
        )
        self.assertEqual(semantic.chunks_embedded, 2)
        self.assertEqual(
            self.semantic_paths(),
            {"folder/beta.md", "gamma.md"},
        )

        final_embedder = FakeEmbedder()
        structural, semantic = refresh(
            self.vault,
            self.source_db,
            self.semantic_db,
            final_embedder,
        )
        self.assertEqual(
            (structural.new, structural.updated, structural.deleted),
            (0, 0, 0),
        )
        self.assertEqual(
            (semantic.missing, semantic.stale, semantic.orphaned),
            (0, 0, 0),
        )
        self.assertEqual(final_embedder.embedded, 0)

    def test_embedding_failure_keeps_semantic_changes_atomic(self):
        self.write_note("alpha.md", "# Alpha\n\ncurrent content")

        conn = sqlite3.connect(self.semantic_db)
        conn.execute(
            """
            INSERT INTO chunks
            (path, title, chunk_index, content, word_count, embedding)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("orphan.md", "Orphan", 0, "orphan", 1, b"vector"),
        )
        conn.commit()
        conn.close()

        with self.assertRaisesRegex(RuntimeError, "embedding unavailable"):
            refresh(
                self.vault,
                self.source_db,
                self.semantic_db,
                FakeEmbedder(RuntimeError("embedding unavailable")),
            )

        source = sqlite3.connect(self.source_db)
        self.assertEqual(
            source.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
            1,
        )
        source.close()
        self.assertEqual(self.semantic_paths(), {"orphan.md"})

        structural, semantic = refresh(
            self.vault,
            self.source_db,
            self.semantic_db,
            FakeEmbedder(),
        )
        self.assertEqual(structural.new, 0)
        self.assertEqual(
            (semantic.missing, semantic.stale, semantic.orphaned),
            (1, 0, 1),
        )
        self.assertEqual(self.semantic_paths(), {"alpha.md"})


if __name__ == "__main__":
    unittest.main()
