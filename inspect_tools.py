"""Step 2: show how Meko's key tools expect to be called, and list your datapacks.

Run:  python inspect_tools.py
(MEKO_API_KEY must be set in this Command Prompt window.)
"""
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MEKO_URL = "https://mcp.mekodata.ai/mcp"
API_KEY = os.environ.get("MEKO_API_KEY")

if not API_KEY:
    raise SystemExit("Set the MEKO_API_KEY environment variable first.")

WANTED = ["conversation_create", "datapack_list", "memory_add", "context_search"]


async def main():
    headers = {"Authorization": f"Bearer {API_KEY}"}
    async with streamablehttp_client(MEKO_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            for tool in tools.tools:
                if tool.name in WANTED:
                    print("=" * 60)
                    print(tool.name)
                    props = tool.inputSchema.get("properties", {})
                    required = tool.inputSchema.get("required", [])
                    for name, info in props.items():
                        mark = "(required)" if name in required else ""
                        print(f"  - {name} {mark} [{info.get('type', 'any')}]")

            print("=" * 60)
            print("YOUR DATAPACKS:")
            result = await session.call_tool("datapack_list", {})
            for block in result.content:
                if getattr(block, "text", None):
                    print(block.text)


asyncio.run(main())
