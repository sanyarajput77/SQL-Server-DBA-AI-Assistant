"""Offline tests for the local lexical DBA knowledge retrieval prototype."""

import tempfile
import unittest
from pathlib import Path

from rag import chunk_documents, load_documents, retrieve


class LocalRagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = load_documents()
        cls.chunks = chunk_documents(cls.documents)

    def test_loads_generic_sample_documents(self):
        names = {document.filename for document in self.documents}
        self.assertIn("sample-backup-recovery.md", names)
        self.assertIn("sample-blocking.md", names)

    def test_metadata_extraction(self):
        backup = next(doc for doc in self.documents if doc.filename == "sample-backup-recovery.md")
        self.assertEqual(backup.title, "SQL Server Backup and Recovery Concepts")
        self.assertEqual(backup.category, "backup-recovery")
        self.assertEqual(backup.source_path, backup.filename)
        self.assertIn("Transaction log backup", backup.content)

    def test_chunking_is_deterministic_and_retains_source_metadata(self):
        first = chunk_documents(self.documents, max_chars=500, overlap=60)
        second = chunk_documents(self.documents, max_chars=500, overlap=60)
        self.assertEqual(first, second)
        self.assertTrue(first)
        self.assertTrue(all(chunk.filename and chunk.title and chunk.source_path and chunk.category for chunk in first))
        self.assertTrue(all(chunk.section and chunk.chunk_index >= 0 for chunk in first))

    def test_retrieval_has_source_attribution(self):
        results = retrieve("transaction log backup", self.chunks, self.documents)
        self.assertTrue(results)
        result = results[0]
        self.assertEqual(result.source_document.filename, "sample-backup-recovery.md")
        self.assertIn(result.source_document.filename, result.source_attribution)
        self.assertIn(result.relevant_chunk.section, result.source_attribution)
        self.assertGreater(result.relevance_score, 0)
        self.assertNotIn(str(Path.cwd()), result.source_attribution)

    def test_transaction_log_backup_query_finds_backup_document(self):
        results = retrieve("transaction log backup", self.chunks, self.documents)
        self.assertEqual(results[0].source_document.filename, "sample-backup-recovery.md")

    def test_blocking_sessions_query_finds_blocking_document(self):
        results = retrieve("blocking sessions", self.chunks, self.documents)
        self.assertTrue(results)
        self.assertEqual(results[0].source_document.filename, "sample-blocking.md")

    def test_top_k_limits_result_count(self):
        all_results = retrieve("backup blocking session", self.chunks, self.documents, top_k=5)
        limited = retrieve("backup blocking session", self.chunks, self.documents, top_k=1)
        self.assertEqual(len(limited), 1)
        self.assertEqual(limited[0], all_results[0])

    def test_empty_and_tokenless_queries_are_rejected(self):
        for query in ("", "   ", "!!!"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                retrieve(query, self.chunks, self.documents)

    def test_unknown_topic_returns_no_matches(self):
        self.assertEqual(retrieve("quantum telescope nebula", self.chunks, self.documents), [])

    def test_path_traversal_filenames_are_rejected(self):
        for filename in ("../outside.md", r"..\outside.md", "nested/outside.md", r"nested\outside.md"):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                load_documents(filenames=[filename])

    def test_loading_outside_configured_knowledge_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "knowledge"
            outside = Path(temporary) / "outside.md"
            root.mkdir()
            outside.write_text("# Outside\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_documents(root, filenames=[outside])

    def test_query_cannot_select_or_disclose_an_arbitrary_path(self):
        results = retrieve(r"transaction log backup C:\Users\private\notes.md", self.chunks, self.documents)
        self.assertTrue(results)
        for result in results:
            self.assertNotIn("C:\\Users", result.source_attribution)
            self.assertEqual(result.source_document.source_path, result.source_document.filename)


if __name__ == "__main__":
    unittest.main()
