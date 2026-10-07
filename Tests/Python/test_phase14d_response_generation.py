"""Deterministic tests for presentation-only DBA assistant responses."""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.response import AssistantResponse
from assistant.response_generation import generate_response


def reference():
    return {
        "evidence_type": "REFERENCE_KNOWLEDGE",
        "title": "Blocking concepts",
        "filename": "sample-blocking.md",
        "source": "sample-blocking.md#blocking",
        "section": "Blocking",
        "passage": "Blocking occurs when one session waits for another session's lock.",
        "attribution": "sample-blocking.md — Blocking",
    }


def response(status, *, knowledge=(), live=(), actions=(), classification="READ_ONLY", attempted=False, explanation="Executor result"):
    return AssistantResponse(
        status=status,
        intent="live_dba",
        classification=classification,
        explanation=explanation,
        knowledge_sources=tuple(knowledge),
        planned_actions=tuple(actions),
        mcp_results=tuple(live),
        dispatch_performed=attempted,
    )


class Phase14DResponseGenerationTests(unittest.TestCase):
    def test_rag_only_is_reference_and_not_live_sql(self):
        rendered = generate_response(response("SUCCESS", knowledge=[reference()], classification="REFERENCE_ONLY"))
        self.assertIn("not verified against live SQL", rendered.answer)
        self.assertIn("Blocking occurs when one session waits", rendered.answer)
        self.assertEqual(rendered.reference_context[0]["filename"], "sample-blocking.md")
        self.assertEqual(rendered.live_results, ())

    def test_mcp_only_inventory_preserves_returned_values(self):
        data = {"databases": [{"name": "PracticeDB", "state": "ONLINE"}]}
        rendered = generate_response(response(
            "SUCCESS", live=[{"tool": "get_ops_database_inventory", "source": "existing_mcp_server", "status": "SUCCESS", "data": data}], attempted=True,
        ))
        self.assertIn("Live/current MCP tool result", rendered.answer)
        self.assertIn('"name":"PracticeDB"', rendered.answer)
        self.assertIn('"state":"ONLINE"', rendered.answer)
        self.assertEqual(rendered.live_results[0]["data"], data)
        self.assertTrue(rendered.tool_attempted)

    def test_combined_response_separates_reference_and_live_attribution(self):
        rendered = generate_response(response(
            "SUCCESS",
            knowledge=[reference()],
            live=[{"tool": "get_ops_blocking_sessions", "source": "existing_mcp_server", "status": "SUCCESS", "data": {"blocking_count": 1}}],
            attempted=True,
        ))
        self.assertIn("Reference context (documentation", rendered.answer)
        self.assertIn("Live MCP result:", rendered.answer)
        self.assertIn("Blocking occurs when one session waits", rendered.answer)
        self.assertIn('"blocking_count":1', rendered.answer)
        self.assertEqual(rendered.reference_context[0]["evidence_type"], "REFERENCE_KNOWLEDGE")
        self.assertEqual(rendered.live_results[0]["tool"], "get_ops_blocking_sessions")
        self.assertEqual(rendered.live_results[0]["source"], "existing_mcp_server")

    def test_health_report_response_requires_approval_and_claims_no_report(self):
        rendered = generate_response(response("APPROVAL_REQUIRED", classification="WRITES_REPORT"))
        self.assertIn("requires host approval", rendered.answer)
        self.assertIn("No report was generated", rendered.answer)

    def test_reject_response_says_no_operation_was_executed(self):
        rendered = generate_response(response("REJECTED", classification="REJECT", explanation="private validation detail"))
        self.assertIn("not supported or permitted", rendered.answer)
        self.assertIn("No operation was executed", rendered.answer)
        self.assertNotIn("private validation detail", rendered.answer)

    def test_needs_approval_backup_does_not_claim_backup_success(self):
        rendered = generate_response(response(
            "NEEDS_APPROVAL",
            classification="NEEDS_APPROVAL",
            actions=[{"tool": "backup_ops_database", "requires_approval": True}],
        ))
        self.assertIn("requires explicit approval", rendered.answer)
        self.assertIn("No backup was executed", rendered.answer)
        self.assertNotIn("backup completed", rendered.answer.casefold())

    def test_mcp_execution_failure_is_not_reported_as_no_problem(self):
        rendered = generate_response(response(
            "MCP_ERROR",
            live=[{"tool": "get_ops_database_inventory", "source": "existing_mcp_server", "status": "ERROR", "error": "sanitized failure"}],
            attempted=True,
        ))
        self.assertIn("execution failed", rendered.answer)
        self.assertIn("did not establish whether", rendered.answer)
        self.assertEqual(rendered.live_results[0]["status"], "ERROR")

    def test_no_fabricated_sql_result_or_values_are_added(self):
        rendered = generate_response(response(
            "SUCCESS",
            live=[{"tool": "get_ops_database_inventory", "source": "existing_mcp_server", "status": "SUCCESS", "data": {"count": 2}}],
            attempted=True,
        ))
        self.assertEqual(rendered.live_results[0]["data"], {"count": 2})
        self.assertNotIn("SELECT", rendered.answer)
        self.assertNotIn("healthy", rendered.answer.casefold())

    def test_source_attribution_is_preserved(self):
        rendered = generate_response(response(
            "SUCCESS",
            knowledge=[reference()],
            live=[{"tool": "get_ops_blocking_sessions", "source": "existing_mcp_server", "status": "SUCCESS", "data": {}}],
            attempted=True,
        ))
        self.assertEqual(rendered.reference_context[0]["attribution"], "sample-blocking.md — Blocking")
        self.assertEqual(rendered.live_results[0]["source"], "existing_mcp_server")

    def test_errors_secrets_and_paths_are_not_exposed(self):
        rendered = generate_response(response(
            "MCP_ERROR",
            live=[{
                "tool": "get_ops_database_inventory",
                "source": "existing_mcp_server",
                "status": "ERROR",
                "error": r"Password=hunter2 at C:\private\error.log",
            }],
            attempted=True,
        ))
        serialized = str(rendered.to_dict())
        self.assertNotIn("hunter2", serialized)
        self.assertNotIn("private", serialized)
        self.assertIn("details were withheld", rendered.live_results[0]["error"])

    def test_response_generation_has_no_execution_interface(self):
        source = (ROOT / "assistant" / "response_generation.py").read_text(encoding="utf-8").casefold()
        for forbidden in ("call_tool", "clientsession", "subprocess", "cursor.execute", "invoke_expression"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
