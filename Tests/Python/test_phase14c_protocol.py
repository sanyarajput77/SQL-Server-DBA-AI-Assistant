"""Real stdio MCP protocol smoke test; initializes and lists tools only."""

import asyncio
import os
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from assistant.llm_contract import APPROVED_MCP_TOOLS


async def _discover_over_stdio():
    environment = os.environ.copy()
    environment["DBA_AUTOMATION_CONFIG"] = str(ROOT / "Config" / "config.development.json")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "mcp-server" / "server.py")],
        env=environment,
        cwd=ROOT,
    )
    async with stdio_client(parameters, errlog=sys.stderr) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            initialized = await session.initialize()
            discovered = await session.list_tools()
            return initialized.protocol_version, {tool.name for tool in discovered.tools}


class Phase14CProtocolTests(unittest.TestCase):
    def test_real_stdio_server_initializes_and_lists_exact_approved_tools(self):
        protocol_version, names = asyncio.run(_discover_over_stdio())
        self.assertTrue(protocol_version)
        self.assertEqual(names, APPROVED_MCP_TOOLS)
        self.assertEqual(len(names), 10)


if __name__ == "__main__":
    unittest.main()
