"""List Meko tool names and complete input schemas without invoking them.

Run from the repository root with: python -m scripts.inspect_tools
"""

import asyncio
import json
import os

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

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
            for tool in tools.tools:
                print("=" * 60)
                print(tool.name)
                print(json.dumps(tool.inputSchema, indent=2, ensure_ascii=False))


asyncio.run(main())
