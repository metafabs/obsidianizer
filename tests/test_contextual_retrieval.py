from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ask_gemma import answer_question, resolve_citations
from index_vault import create_database
from retrieval import (
    EvidenceItem,
    RetrievalResult,
    execute_plan,
)
from retrieval_planner import PlannerError, RetrievalPlan, plan_question


class ContextualRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source_db = root / "vault.db"
        self.semantic_db = root / "semantic.db"
        source = sqlite3.connect(self.source_db)
        create_database(source)
        source.close()
        semantic = sqlite3.connect(self.semantic_db)
        semantic.executescript(
            """
            CREATE TABLE chunks (
                id INTEGER PRIMARY KEY,
                path TEXT NOT NULL,
                title TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                word_count INTEGER NOT NULL,
                embedding BLOB NOT NULL
            );
            """
        )
        semantic.close()

    def tearDown(self):
        self.temp.cleanup()

    def insert_note(self, path, content, modified, created=None):
        created = created or modified
        source = sqlite3.connect(self.source_db)
        cursor = source.execute(
            """
            INSERT INTO notes
            (
                path, folder, title, content, word_count, modified,
                modified_ns, created, size
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                path,
                str(Path(path).parent),
                Path(path).stem,
                content,
                len(content.split()),
                modified.isoformat(timespec="seconds"),
                int(modified.timestamp() * 1_000_000_000),
                created.isoformat(timespec="seconds"),
                len(content.encode("utf-8")),
            ),
        )
        source.commit()
        source.close()
        return cursor.lastrowid

    def test_planner_parses_and_validates_allowlisted_plan(self):
        plan, elapsed = plan_question(
            "Which note changed last?",
            generate_fn=lambda question: {
                "mode": "structural",
                "operation": "latest_notes",
                "topic": None,
                "time_scope": "none",
                "days": None,
                "limit": 100,
                "authored_only": False,
            },
        )
        self.assertEqual(plan.operation, "latest_notes")
        self.assertEqual(plan.limit, 30)
        self.assertGreaterEqual(elapsed, 0)

    def test_planner_accepts_fenced_json_and_null_defaults(self):
        raw = """```json
        {
          "mode": "structural",
          "operation": "latest_notes",
          "topic": null,
          "time_scope": null,
          "days": null,
          "limit": null,
          "authored_only": null
        }
        ```"""
        plan, _ = plan_question(
            "Which note changed last?",
            generate_fn=lambda question: raw,
        )
        self.assertEqual(plan.time_scope, "none")
        self.assertEqual(plan.limit, 1)
        self.assertFalse(plan.authored_only)
        self.assertEqual(plan.recency, ("modified",))

    def test_planner_preserves_multiple_recency_facts(self):
        plan, _ = plan_question(
            "Compare the newest timestamps",
            generate_fn=lambda question: {
                "mode": "structural",
                "operation": "latest_notes",
                "topic": None,
                "time_scope": "none",
                "days": None,
                "limit": 1,
                "authored_only": False,
                "recency": ["added", "modified", "created", "modified"],
            },
        )
        self.assertEqual(plan.recency, ("added", "modified", "created"))

    def test_planner_rejects_invalid_mode_operation_combination(self):
        with self.assertRaisesRegex(PlannerError, "Invalid retrieval"):
            plan_question(
                "Count matching notes",
                generate_fn=lambda question: {
                    "mode": "semantic",
                    "operation": "topic_stats",
                    "topic": "example",
                    "time_scope": "none",
                    "days": None,
                    "limit": 10,
                    "authored_only": False,
                },
            )

    def test_latest_note_uses_modified_timestamp_without_synthesis(self):
        now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        self.insert_note("older.md", "older content", now - timedelta(days=2))
        self.insert_note("newer.md", "newer content", now - timedelta(hours=1))
        plan = RetrievalPlan(
            mode="structural",
            operation="latest_notes",
            limit=1,
        )
        result = execute_plan(
            plan,
            "Which note changed last?",
            source_db=self.source_db,
            semantic_db=self.semantic_db,
            now=now,
        )
        self.assertFalse(result.needs_synthesis)
        self.assertEqual(result.evidence[0].path, "newer.md")
        self.assertIn("newer.md", result.direct_answer)

    def test_created_and_modified_recency_are_distinct(self):
        now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        self.insert_note(
            "latest-created.md",
            "created later",
            now - timedelta(days=3),
            created=now - timedelta(hours=1),
        )
        self.insert_note(
            "latest-modified.md",
            "modified later",
            now,
            created=now - timedelta(days=10),
        )
        plan = RetrievalPlan(
            mode="structural",
            operation="latest_notes",
            limit=1,
            recency=("created", "modified"),
        )
        result = execute_plan(
            plan,
            "Compare creation and modification recency",
            source_db=self.source_db,
            semantic_db=self.semantic_db,
            now=now,
        )
        self.assertFalse(result.needs_synthesis)
        created_start = result.direct_answer.index("Most recently created note")
        modified_start = result.direct_answer.index("Most recently modified note")
        created_section = result.direct_answer[created_start:modified_start]
        modified_section = result.direct_answer[modified_start:]
        self.assertIn("latest-created.md", created_section)
        self.assertNotIn("latest-modified.md", created_section)
        self.assertIn("latest-modified.md", modified_section)
        self.assertNotIn("latest-created.md", modified_section)

    def test_added_recency_is_explicitly_unavailable_and_compound_survives(self):
        now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        self.insert_note("modified.md", "content", now)
        plan = RetrievalPlan(
            mode="structural",
            operation="latest_notes",
            limit=1,
            recency=("added", "modified"),
        )
        result = execute_plan(
            plan,
            "Compare vault addition and modification recency",
            source_db=self.source_db,
            semantic_db=self.semantic_db,
            now=now,
        )
        self.assertFalse(result.needs_synthesis)
        self.assertIn("no persisted first-seen/indexed timestamp", result.direct_answer)
        self.assertIn("Most recently modified note", result.direct_answer)
        self.assertIn("modified.md", result.direct_answer)

    def test_topic_count_is_deterministic(self):
        now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        self.insert_note("one.md", "A note about copper", now)
        self.insert_note("two.md", "Copper appears here too", now)
        self.insert_note("three.md", "Unrelated material", now)
        plan = RetrievalPlan(
            mode="structural",
            operation="topic_stats",
            topic="copper",
            limit=10,
        )
        result = execute_plan(
            plan,
            "Count direct notes about copper",
            source_db=self.source_db,
            semantic_db=self.semantic_db,
            now=now,
        )
        self.assertFalse(result.needs_synthesis)
        self.assertIn("**2**", result.direct_answer)

    def test_authored_only_filters_structural_and_semantic_corpora(self):
        now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        self.insert_note("authored.md", "copper idea", now)
        self.insert_note("reference.md", "copper reference", now)

        topic_plan = RetrievalPlan(
            mode="structural",
            operation="topic_stats",
            topic="copper",
            authored_only=True,
        )
        with patch(
            "retrieval.is_authored",
            side_effect=lambda path: path == "authored.md",
        ):
            topic_result = execute_plan(
                topic_plan,
                "Count authored copper notes",
                source_db=self.source_db,
                semantic_db=self.semantic_db,
                now=now,
            )
        self.assertIn("**1**", topic_result.direct_answer)

        captured = {}

        def fake_semantic(query, **kwargs):
            captured["allowed_paths"] = kwargs["allowed_paths"]
            return []

        semantic_plan = RetrievalPlan(
            mode="semantic",
            operation="semantic_search",
            authored_only=True,
        )
        with patch(
            "retrieval.is_authored",
            side_effect=lambda path: path == "authored.md",
        ):
            execute_plan(
                semantic_plan,
                "Interpret authored copper ideas",
                source_db=self.source_db,
                semantic_db=self.semantic_db,
                semantic_search_fn=fake_semantic,
                now=now,
            )
        self.assertEqual(captured["allowed_paths"], {"authored.md"})

    def test_recent_hybrid_uses_only_structurally_recent_paths(self):
        now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        recent_paths = []
        for index in range(13):
            path = f"recent-{index}.md"
            recent_paths.append(path)
            self.insert_note(path, f"recent content {index}", now)
        self.insert_note("old.md", "old content", now - timedelta(days=60))
        captured = {}

        def fake_semantic(query, **kwargs):
            captured["allowed_paths"] = kwargs["allowed_paths"]
            return [{
                "path": recent_paths[0],
                "title": "Recent 0",
                "content": "ranked recent content",
                "semantic": 0.9,
                "score": 0.9,
                "chunk": 0,
            }]

        plan = RetrievalPlan(
            mode="hybrid",
            operation="recent_notes",
            time_scope="recent",
            limit=30,
            authored_only=True,
        )
        result = execute_plan(
            plan,
            "Summarize recent themes",
            source_db=self.source_db,
            semantic_db=self.semantic_db,
            semantic_search_fn=fake_semantic,
            now=now,
        )
        self.assertTrue(result.needs_synthesis)
        self.assertEqual(captured["allowed_paths"], set(recent_paths))
        self.assertNotIn("old.md", captured["allowed_paths"])

    def test_recent_hybrid_falls_back_to_structural_evidence(self):
        now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
        for index in range(13):
            self.insert_note(f"recent-{index}.md", "recent content", now)

        def failed_semantic(query, **kwargs):
            raise RuntimeError("embedding unavailable")

        plan = RetrievalPlan(
            mode="hybrid",
            operation="recent_notes",
            time_scope="recent",
            limit=30,
            authored_only=True,
        )
        result = execute_plan(
            plan,
            "Summarize recent themes",
            source_db=self.source_db,
            semantic_db=self.semantic_db,
            semantic_search_fn=failed_semantic,
            now=now,
        )
        self.assertTrue(result.needs_synthesis)
        self.assertEqual(len(result.evidence), 12)
        self.assertIn("semantic ranking unavailable", result.scope)

    def test_direct_answer_skips_synthesis(self):
        plan = RetrievalPlan("structural", "latest_notes", limit=1)
        retrieval = RetrievalResult(
            "structural",
            "latest_notes",
            (),
            "Deterministic answer",
            False,
            "test scope",
        )

        def fail_synthesis(question, context):
            raise AssertionError("Synthesis must not run")

        result = answer_question(
            "Which note changed last?",
            planner_fn=lambda question: (plan, 0.01),
            retrieval_fn=lambda actual_plan, question: retrieval,
            synthesis_fn=fail_synthesis,
        )
        self.assertEqual(result.answer, "Deterministic answer")
        self.assertFalse(result.synthesized)

    def test_citations_resolve_only_from_retrieved_evidence(self):
        evidence = (
            EvidenceItem(
                "E1",
                "generic.md",
                "Generic",
                "NOTE",
                None,
                "content",
                "test",
            ),
        )
        self.assertEqual(
            resolve_citations("Supported [E1]", evidence),
            "Supported [generic.md]",
        )
        with self.assertRaisesRegex(RuntimeError, "unavailable evidence"):
            resolve_citations("Unsupported [E2]", evidence)


if __name__ == "__main__":
    unittest.main()
