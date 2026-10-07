"""Offline checks for the DBA knowledge-base foundation and security boundaries."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE_DIR = ROOT / "docs" / "knowledge"
SPEC_PATH = ROOT / "docs" / "KNOWLEDGE_BASE_SPEC.md"
SAMPLE_PATHS = (
    KNOWLEDGE_DIR / "sample-backup-recovery.md",
    KNOWLEDGE_DIR / "sample-blocking.md",
)


class KnowledgeBaseSpecificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = SPEC_PATH.read_text(encoding="utf-8")

    def test_spec_and_knowledge_directory_exist(self):
        self.assertTrue(SPEC_PATH.is_file())
        self.assertTrue(KNOWLEDGE_DIR.is_dir())

    def test_required_metadata_concepts_are_documented(self):
        for concept in (
            "document_id",
            "source_name",
            "source_uri",
            "owner",
            "version",
            "reviewed_at",
            "product_version",
            "environment_scope",
            "classification",
            "allowed_audience",
            "content_hash",
        ):
            with self.subTest(concept=concept):
                self.assertIn(concept, self.spec)

    def test_source_attribution_is_documented(self):
        self.assertIn("Source attribution and citations", self.spec)
        self.assertIn("source title and version/date", self.spec)
        self.assertIn("source locator", self.spec)

    def test_security_and_proprietary_content_rules_are_documented(self):
        self.assertIn("Security and excluded content", self.spec)
        self.assertIn("Proprietary/confidential documents", self.spec)
        self.assertIn("Passwords, tokens, private keys", self.spec)
        self.assertIn("least privilege", self.spec)

    def test_live_results_are_distinguished_from_retrieved_knowledge(self):
        self.assertIn("Knowledge versus live operational results", self.spec)
        self.assertIn("not evidence of the current state", self.spec)
        self.assertIn("Live state comes only from results returned by the existing MCP Ops tools", self.spec)

    def test_azure_ai_search_is_future_only(self):
        self.assertIn("Future Azure AI Search migration path", self.spec)
        self.assertIn("not a current dependency", self.spec)
        self.assertIn("This phase installs nothing and calls no Azure service", self.spec)

    def test_sample_documents_exist(self):
        for path in SAMPLE_PATHS:
            with self.subTest(path=path.name):
                self.assertTrue(path.is_file())

    def test_sample_knowledge_has_no_credential_assignments_or_secrets(self):
        credential_assignment = re.compile(
            r"(?i)\b(?:password|passwd|pwd|token|api[_ -]?key|client[_ -]?secret)\b\s*[:=]"
        )
        for path in SAMPLE_PATHS:
            content = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertIsNone(credential_assignment.search(content))
                self.assertNotRegex(content, r"(?i)hcltech|ericsson|customer\s+(?:name|data)")
                self.assertNotRegex(content, r"(?i)(?:sql|server)=.{0,80};(?:password|pwd)=")


if __name__ == "__main__":
    unittest.main()
