"""Offline checks that the assistant behavior documentation matches the MCP surface."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = (ROOT / "docs" / "AI_ASSISTANT_SPEC.md").read_text(encoding="utf-8")
ROUTING = (ROOT / "docs" / "TOOL_ROUTING.md").read_text(encoding="utf-8")


EXPECTED_TOOLS = {
    "get_ops_database_inventory",
    "get_ops_database_health",
    "get_ops_database_size",
    "get_ops_backup_status",
    "get_ops_disk_space",
    "get_ops_failed_jobs",
    "get_ops_blocking_sessions",
    "get_ops_long_running_queries",
    "get_ops_server_health_report",
    "backup_ops_database",
}


class AssistantSpecificationTests(unittest.TestCase):
    def test_routing_table_covers_exactly_the_existing_tools(self):
        routed = set(re.findall(r"`(get_ops_[a-z_]+|backup_ops_database)`", ROUTING))
        self.assertEqual(routed, EXPECTED_TOOLS)

    def test_specification_covers_required_safety_topics(self):
        required_phrases = (
            "Purpose",
            "Supported request categories",
            "Tool behavior and approval",
            "Target and input validation",
            "ApprovedInstances",
            "ApprovedDatabases",
            "System databases",
            "Explaining results",
            "Error handling",
            "Prohibited behavior",
            "Request examples",
            "never invent SQL execution capability",
        )
        for phrase in required_phrases:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), SPEC.casefold())

    def test_sql_shell_backup_path_and_backup_approval_rules_are_explicit(self):
        for phrase in (
            "arbitrary SQL",
            "arbitrary PowerShell",
            "C:\\whatever\\DatabaseA.bak",
            "NEEDS_APPROVAL",
            "fails closed",
            "elicitation",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase.casefold(), SPEC.casefold())

    def test_report_side_effect_and_databasea_configuration_are_not_misrepresented(self):
        self.assertIn("READ_ONLY_SQL / WRITES_REPORT", SPEC)
        self.assertIn("configured Reports directory", SPEC)
        self.assertIn("`DatabaseA` is a placeholder", ROUTING)
        self.assertIn("reject `DatabaseA` unless", ROUTING)


if __name__ == "__main__":
    unittest.main()
