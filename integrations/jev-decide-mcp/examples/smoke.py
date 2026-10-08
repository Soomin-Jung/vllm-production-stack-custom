"""Run real MCP-over-stdio calls against your backend. This incurs model inference."""

import asyncio
import json
import os
import sys
from datetime import timedelta

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> None:
    if not os.environ.get("JEV_BASE_URL"):
        raise SystemExit("Set JEV_BASE_URL to your deployed /v1/decide backend first")
    deadline = float(os.environ.get("JEV_TIMEOUT_SECONDS", "60"))
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jev_decide_mcp.server"],
        env=dict(os.environ),
    )
    async with (
        stdio_client(params) as (read, write),
        ClientSession(
            read, write, read_timeout_seconds=timedelta(seconds=deadline + 30)
        ) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        print("tools:", ", ".join(tool.name for tool in tools.tools))
        info = await session.call_tool("jev_decide_info", {})
        if info.isError:
            raise RuntimeError(info.content[0].text)
        print("backend info:", json.dumps(info.structuredContent, ensure_ascii=False))
        bodies = [
            {
                "kind": "choice",
                "state": "Database connection refused before tests ran.",
                "question": "What failed?",
                "options": ["test assertion", "infrastructure", "uncertain"],
            },
            {
                "kind": "noul",
                "state": "The test runner could not connect to its database.",
                "question": "Did the runner report a database connection problem?",
            },
            {
                "kind": "score",
                "state": "All production users cannot access the service.",
                "question": "Severity: 0=no impact, 1=one test, 2=development, 3=some users, "
                "4=many users, 5=all users. Score the impact.",
            },
        ]
        for body in bodies:
            body["thinking"] = "off"
            result = await session.call_tool("jev_decide", body)
            if result.isError:
                raise RuntimeError(result.content[0].text)
            data = result.structuredContent
            print(
                json.dumps(
                    {
                        k: data.get(k)
                        for k in (
                            "kind",
                            "choice",
                            "probabilities",
                            "usage",
                            "elapsed_seconds",
                            "num_model_requests",
                        )
                    },
                    ensure_ascii=False,
                )
            )
        print("PASS: MCP transport and all three backend primitives responded")


if __name__ == "__main__":
    asyncio.run(main())
