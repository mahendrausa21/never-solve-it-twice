"""Step 3: the core idea of the project, with no AI model involved yet.

1. Open a Meko conversation (Meko needs one to trace everything).
2. Find your 'support-hackathon' datapack.
3. Search for a problem that IS in the knowledge base (refund).
4. Search for a problem that is NOT known yet (smart speaker WiFi).
5. Save a fix for it ("learning").
6. Search again -> the fix should now be found.

Run:  python step3_demo.py
(MEKO_API_KEY must be set in this Command Prompt window.)
"""
import asyncio
import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MEKO_URL = "https://mcp.mekodata.ai/mcp"
API_KEY = os.environ.get("MEKO_API_KEY")
DATAPACK_NAME = "support-hackathon"

if not API_KEY:
    raise SystemExit("Set the MEKO_API_KEY environment variable first.")


def parse(result):
    """Turn a tool result into Python data (JSON if possible, else text)."""
    text = "".join(b.text for b in result.content if getattr(b, "text", None))
    try:
        return json.loads(text)
    except Exception:
        return text


def find_value(obj, keys):
    """Search nested data for the first string value under any of `keys`."""
    if isinstance(obj, dict):
        for k in keys:
            if isinstance(obj.get(k), str):
                return obj[k]
        for v in obj.values():
            found = find_value(v, keys)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_value(v, keys)
            if found:
                return found
    return None


def find_datapack_id(obj, name):
    """Find the id of the datapack called `name`."""
    if isinstance(obj, dict):
        if obj.get("datapack_name") == name or obj.get("name") == name:
            return obj.get("datapack_id") or obj.get("id")
        for v in obj.values():
            found = find_datapack_id(v, name)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_datapack_id(v, name)
            if found:
                return found
    return None


def show(title, data):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)
    print(json.dumps(data, indent=2)[:2500] if not isinstance(data, str) else data[:2500])


async def main():
    headers = {"Authorization": f"Bearer {API_KEY}"}
    async with streamablehttp_client(MEKO_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()

            # 1. conversation
            conv = parse(await s.call_tool("conversation_create", {"title": "Step 3 demo"}))
            show("conversation_create result", conv)
            conv_id = find_value(conv, ["conversation_id", "id"])
            if not conv_id:
                raise SystemExit("Could not find a conversation id. Send me the output above.")

            # 2. datapack id
            packs = parse(await s.call_tool("datapack_list", {"conversation_id": conv_id}))
            show("datapack_list result", packs)
            dp_id = find_datapack_id(packs, DATAPACK_NAME)
            if not dp_id:
                raise SystemExit(f"Could not find datapack '{DATAPACK_NAME}'. Send me the output above.")
            print("\nUsing datapack id:", dp_id)

            async def search(query):
                return parse(await s.call_tool("context_search", {
                    "conversation_id": conv_id,
                    "datapack_id": dp_id,
                    "query": query,
                }))

            # 3. known problem
            show("SEARCH 1: refund (should be in the knowledge base)",
                 await search("customer says refund not received after a week"))

            # 4. unknown problem
            q = "smart speaker keeps disconnecting from wifi"
            show("SEARCH 2: smart speaker (not known yet)", await search(q))

            # 5. learn
            fix = ("Smart speaker keeps disconnecting from WiFi: ask the customer to move it "
                   "closer to the router, switch the router to the 2.4 GHz band, then factory "
                   "reset the speaker and set it up again. Escalate to the device team if it "
                   "still drops.")
            added = parse(await s.call_tool("memory_add", {
                "conversation_id": conv_id,
                "datapack_id": dp_id,
                "text": fix,
            }))
            show("memory_add result (the agent learned something)", added)

            # 6. reuse
            await asyncio.sleep(5)  # give Meko a moment to index the new memory
            show("SEARCH 3: same problem again (should now find the fix)", await search(q))


asyncio.run(main())
