"""Create a fresh, isolated Meko datapack, optionally seeded with 100 sample entries.

Examples:
  python -m scripts.create_demo_datapack --name supportiq-benchmark-2026 --confirm-name supportiq-benchmark-2026
  python -m scripts.create_demo_datapack --name supportiq-demo-kb-2026 --confirm-name supportiq-demo-kb-2026 --seed-kb
"""

import argparse
import asyncio
import json
import os
import re
import uuid

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from scripts.generate_benchmark_data import build_knowledge


MEKO_URL = "https://mcp.mekodata.ai/mcp"


def parse_response(response, tool_name):
    text = "".join(block.text for block in response.content if getattr(block, "text", None))
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Meko {tool_name} returned a non-JSON response.") from err
    if response.isError or (isinstance(payload, dict) and payload.get("error")):
        raise RuntimeError(f"Meko {tool_name} failed ({tool_name} error).")
    return payload


def rows_from(payload, *keys):
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in keys:
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def find_datapack_id(payload):
    if isinstance(payload, dict):
        direct = payload.get("datapack_id") or payload.get("id")
        if isinstance(direct, str) and direct:
            return direct
        for value in payload.values():
            found = find_datapack_id(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_datapack_id(value)
            if found:
                return found
    return None


def find_memory_id(payload):
    if isinstance(payload, dict):
        direct = payload.get("id") or payload.get("memory_id")
        if isinstance(direct, str):
            try:
                uuid.UUID(direct)
                return direct
            except ValueError:
                pass
        for value in payload.values():
            found = find_memory_id(value)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = find_memory_id(value)
            if found:
                return found
    return None


async def call(session, name, arguments):
    response = await session.call_tool(name, arguments)
    return parse_response(response, name)


async def create_datapack(name, seed_kb):
    api_key = os.environ.get("MEKO_API_KEY")
    if not api_key:
        raise RuntimeError("MEKO_API_KEY is not configured.")
    headers = {"Authorization": "Bearer " + api_key}
    async with streamablehttp_client(MEKO_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            existing = await call(session, "datapack_list", {"query": name, "limit": 100})
            packs = rows_from(existing, "datapacks", "results", "items")
            if any(item.get("datapack_name") == name for item in packs if isinstance(item, dict)):
                raise RuntimeError("A datapack with this exact name already exists; choose a new name.")

            created = await call(session, "datapack_create", {"name": name})
            datapack_id = find_datapack_id(created)
            if not datapack_id:
                raise RuntimeError("Meko created a datapack but did not return its id.")
            print(f"Created isolated datapack {name!r}.")
            if not seed_kb:
                return datapack_id

            conversation = await call(session, "conversation_create", {
                "title": f"SupportIQ sample knowledge seed: {name}",
                "datapack_id": datapack_id,
            })
            conversation_id = conversation.get("id") if isinstance(conversation, dict) else None
            if not conversation_id:
                raise RuntimeError("Meko did not return a seed conversation id.")

            memory_ids = []
            for entry in build_knowledge():
                text = (
                    f"Section: {entry['section']} > {entry['title']}\n"
                    f"Problem: {entry['problem']} Fix: {entry['verified_fix']}\n"
                    f"Escalate to: {entry['escalate_to']}."
                )
                added = await call(session, "memory_add", {
                    "conversation_id": conversation_id,
                    "datapack_id": datapack_id,
                    "text": text,
                    "metadata": json.dumps({
                        "source": "supportiq-fictional-benchmark",
                        "entry_id": entry["id"],
                        "category": entry["category"],
                        "status": "draft",
                    }),
                })
                memory_id = find_memory_id(added)
                if not memory_id:
                    raise RuntimeError(f"Meko did not return a memory id for {entry['id']}.")
                memory_ids.append(memory_id)
                if len(memory_ids) % 10 == 0:
                    print(f"Added {len(memory_ids)} of 100 sample entries.")

            for start in range(0, len(memory_ids), 20):
                await call(session, "memory_promote", {
                    "conversation_id": conversation_id,
                    "datapack_id": datapack_id,
                    "memory_ids": memory_ids[start:start + 20],
                })
            print("Promoted all 100 fictional entries in the new datapack.")
            return datapack_id


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", required=True, help="Unique new datapack name containing demo or benchmark.")
    parser.add_argument("--confirm-name", required=True, help="Repeat --name to confirm additive creation.")
    parser.add_argument("--seed-kb", action="store_true", help="Add and promote the 100 fictional KB entries.")
    args = parser.parse_args()
    if args.name != args.confirm_name:
        parser.error("--confirm-name must exactly match --name.")
    if not re.search(r"(demo|benchmark)", args.name, re.IGNORECASE):
        parser.error("Use a clearly isolated name containing 'demo' or 'benchmark'.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,79}", args.name):
        parser.error("Datapack names may contain only letters, numbers, '.', '_' and '-'.")

    load_dotenv()
    try:
        datapack_id = asyncio.run(create_datapack(args.name, args.seed_kb))
    except Exception as err:
        raise SystemExit(f"Demo datapack creation failed ({type(err).__name__}).") from err
    print(f"Datapack id: {datapack_id}")
    print(f"Set DATAPACK_NAME={args.name} before starting SupportIQ.")


if __name__ == "__main__":
    main()
