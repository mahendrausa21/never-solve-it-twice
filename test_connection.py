"""Step 1 test: connect to Meko from Python and list the tools it offers.

Run:
    pip install mcp
    set MEKO_API_KEY=your_full_key        (Windows)
    export MEKO_API_KEY=your_full_key     (Mac/Linux)
    python test_connection.py
"""
import asyncio
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MEKO_URL = "https://mcp.mekodata.ai/mcp"
API_KEY = os.environ.get("MEKO_API_KEY")

if not API_KEY:
    raise SystemExit("Set the MEKO_API_KEY environment variable first.")


async def main():
    headers = {"Authorization": f"Bearer {API_KEY}"}
    async with streamablehttp_client(MEKO_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"Connected! Meko offers {len(tools.tools)} tools:\n")
            for tool in tools.tools:
                print(" -", tool.name)


asyncio.run(main())
