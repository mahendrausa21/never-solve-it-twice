"""debug_kb.py - is the approved (promoted) fix searchable in Meko's knowledge base?

Run:
    python debug_kb.py
It searches the knowledge base directly, then asks context_search to look ONLY at the
knowledge base, and prints short summaries (not the whole raw reply).
"""
import asyncio
import json

from agents import Meko

QUERIES = [
    "The app crashes whenever I try to apply a gift card at checkout.",
    "My app crashes at checkout when I apply a gift card.",
]


def summarize(label, res):
    print(f"\n--- {label} ---")
    if "raw" in res and len(res) == 1:
        print(res["raw"][:500])
        return
    text = json.dumps(res)
    print("contains the word 'gift card':", "gift card" in text.lower())
    print("top-level keys:", list(res.keys()))
    found = res.get("results", res)
    if isinstance(found, dict):
        for key, val in found.items():
            if isinstance(val, list):
                print(f"  {key}: {len(val)} item(s)")
                for item in val[:3]:
                    chunk = item.get("chunk_text") or item.get("memory") or item.get("data") or ""
                    print("     ->", chunk[:100].replace("\n", " "))
    elif isinstance(found, list):
        print(f"  {len(found)} item(s)")
        for item in found[:3]:
            chunk = item.get("chunk_text") or item.get("memory") or item.get("data") or ""
            print("     ->", str(chunk)[:100].replace("\n", " "))


async def main():
    meko = await Meko().connect()
    try:
        conv = await meko.call("conversation_create", {"title": "debug kb"})
        for q in QUERIES:
            print("\n" + "=" * 70)
            print("QUERY:", q)
            kb = await meko.call("knowledgebase_search", {
                "conversation_id": conv["id"], "datapack_id": meko.dp, "query": q})
            summarize("knowledgebase_search (direct)", kb)
            cs = await meko.call("context_search", {
                "conversation_id": conv["id"], "datapack_id": meko.dp, "query": q,
                "force_source": "knowledge_base"})
            summarize("context_search, knowledge base only", cs)
            print("match_quality:", cs.get("match_quality"))
    finally:
        await meko.close()


asyncio.run(main())
