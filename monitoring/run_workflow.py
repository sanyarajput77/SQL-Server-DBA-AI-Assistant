"""Manual entry point; a scheduler may invoke this module later."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from assistant.audit import AuditEvent
from .workflow import MonitoringWorkflow


_EXIT_CODES = {"SUCCESS": 0, "WARNING": 1, "CRITICAL": 2, "UNKNOWN": 3, "FAILED": 4}


async def _run(config_path: Path) -> int:
    project_root = Path(__file__).resolve().parents[1]
    server_path = project_root / "mcp-server" / "server.py"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(server_path)],
        cwd=str(project_root),
    )
    try:
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await MonitoringWorkflow().run(session, config_path)
    except Exception:
        workflow_id = uuid4().hex
        timestamp = datetime.now(timezone.utc).isoformat()
        event = AuditEvent(
            tool_name=None,
            classification="READ_ONLY",
            approval_required=False,
            approval_outcome="NOT_REQUIRED",
            execution_attempted=False,
            execution_result="NOT_ATTEMPTED",
            failure_category="MCP",
            request_id=workflow_id,
            timestamp_utc=timestamp,
        )
        print(json.dumps({
            "workflow_id": workflow_id,
            "timestamp_utc": timestamp,
            "sql_instance": None,
            "overall_severity": "UNKNOWN",
            "check_count": 0,
            "collection_failure_count": 0,
            "healthy_count": 0,
            "info_count": 0,
            "warning_count": 0,
            "critical_count": 0,
            "unknown_count": 0,
            "artifact_locations": {},
            "workflow_status": "FAILED",
            "failure": {
                "stage": "mcp_startup",
                "category": "MCP",
                "message": "The configured MCP server could not be started; details were withheld.",
            },
            "audit_events": [event.to_dict()],
        }, sort_keys=True))
        return _EXIT_CODES["FAILED"]
    print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    return _EXIT_CODES[result.workflow_status]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the configured read-only DBA monitoring workflow.")
    parser.add_argument("--config", required=True, type=Path, help="Workflow JSON configuration file")
    args = parser.parse_args()
    return asyncio.run(_run(args.config))


if __name__ == "__main__":
    raise SystemExit(main())
