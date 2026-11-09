"""debug_search.py - show exactly what Meko returns for a search.

Run:
    python debug_search.py "My app crashes at checkout when I apply a gift card."
"""
import asyncio
import json
import sys

from agents import Meko


async def main():
    query = " ".join(sys.argv[1:]) or "My app crashes at checkout when I apply a gift card."
    meko = await Meko().connect()
    try:
        conv = await meko.call("conversation_create", {"title": "debug search"})
        res = await meko.call("context_search", {
            "conversation_id": conv["id"], "datapack_id": meko.dp, "query": query})
        print("QUERY:", query)
        print("match_quality:", res.get("match_quality"))
        print(json.dumps(res, indent=2)[:6000])

        print("\n--- promoted memories (approved) ---")
        promoted = await meko.call("memory_get_all", {
            "conversation_id": conv["id"], "datapack_id": meko.dp, "promoted": True})
        print(json.dumps(promoted, indent=2)[:2500])
    finally:
        await meko.close()


asyncio.run(main())
