"""Never Solve It Twice: two support agents that share one Meko datapack.

Every ticket, each agent:
  1. Searches Meko for an earlier fix.
  2. Found one?  -> writes the reply from that fix (small prompt).
  3. Nothing?    -> solves it from scratch with the AI, then SAVES the fix to Meko.

It also measures a BASELINE: how big the prompt would be if, instead of using Meko,
you pasted the whole knowledge base into every request.

Run:
    pip install google-genai
    set MEKO_API_KEY=your_meko_key
    set GEMINI_API_KEY=your_gemini_key
    set GEMINI_MODEL=gemini-3.8-flash
    python support_agents.py
"""
import asyncio
import json
import os
import time

from google import genai
from google.genai import types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MEKO_URL = "https://mcp.mekodata.ai/mcp"
MEKO_KEY = os.environ.get("MEKO_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
DATAPACK_NAME = "support-hackathon"
KB_FILE = "support_knowledge_base.md"  # copy it into this folder for the baseline

if not MEKO_KEY or not GEMINI_KEY:
    raise SystemExit("Set both MEKO_API_KEY and GEMINI_API_KEY first.")

llm = genai.Client(api_key=GEMINI_KEY)

KB_TEXT = ""
if os.path.exists(KB_FILE):
    with open(KB_FILE, encoding="utf-8") as f:
        KB_TEXT = f.read()
else:
    print(f"(Note: {KB_FILE} not found in this folder, so the baseline will be skipped.)")


# ---------- small helpers ----------
def parse(result):
    text = "".join(b.text for b in result.content if getattr(b, "text", None))
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def ask_llm(prompt):
    """Call Gemini. Returns dict with text, prompt_tokens, total_tokens, seconds."""
    for attempt in range(4):
        try:
            t = time.time()
            try:
                # ask the model not to spend extra time/tokens "thinking"
                cfg = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0))
                resp = llm.models.generate_content(
                    model=GEMINI_MODEL, contents=prompt, config=cfg)
            except Exception as err:
                if "thinking" not in str(err).lower():
                    raise
                resp = llm.models.generate_content(model=GEMINI_MODEL, contents=prompt)
            u = resp.usage_metadata
            return {
                "text": resp.text.strip(),
                "prompt_tokens": getattr(u, "prompt_token_count", 0) or 0,
                "total_tokens": getattr(u, "total_token_count", 0) or 0,
                "seconds": round(time.time() - t, 1),
            }
        except Exception as err:
            if attempt == 3:
                raise
            print(f"   (AI busy, retrying... {str(err)[:80]})")
            time.sleep(8)


def count_tokens(text):
    try:
        return llm.models.count_tokens(model=GEMINI_MODEL, contents=text).total_tokens
    except Exception:
        return 0


def context_text(search_result):
    found = search_result.get("results", {})
    parts = [c["chunk_text"] for c in found.get("knowledge_base", [])]
    parts += [m["memory"] for m in found.get("memory", [])]
    return "\n---\n".join(parts)


# ---------- the agent ----------
class SupportAgent:
    def __init__(self, name, session, datapack_id):
        self.name = name
        self.s = session
        self.dp = datapack_id
        self.conv = None

    async def start(self):
        res = parse(await self.s.call_tool(
            "conversation_create", {"title": f"{self.name} session"}))
        self.conv = res["id"]

    async def handle(self, ticket):
        t0 = time.time()
        search = parse(await self.s.call_tool("context_search", {
            "conversation_id": self.conv,
            "datapack_id": self.dp,
            "query": ticket,
        }))
        quality = search.get("match_quality", "low")
        search_s = round(time.time() - t0, 1)

        baseline = 0
        if quality != "low":
            source = "REUSED from Meko"
            ctx = context_text(search)
            prompt = (
                "You are a ShopEasy support agent. Using ONLY the context below, write a "
                "short, friendly reply (max 4 sentences) to the customer.\n\n"
                f"CONTEXT:\n{ctx}\n\nCUSTOMER TICKET: {ticket}"
            )
            ai = ask_llm(prompt)
            if KB_TEXT:  # what the same job costs if the WHOLE KB is pasted in
                baseline = count_tokens(
                    prompt.replace(ctx, KB_TEXT))
        else:
            source = "SOLVED from scratch + SAVED to Meko"
            prompt = (
                "You are a senior ShopEasy support agent. Nobody has solved this problem "
                "before. Write a short, practical fix: first a one-line problem summary, "
                "then 2-3 concrete steps for the customer, then who to escalate to if it "
                f"fails (max 5 sentences).\n\nCUSTOMER TICKET: {ticket}"
            )
            ai = ask_llm(prompt)
            await self.s.call_tool("memory_add", {
                "conversation_id": self.conv,
                "datapack_id": self.dp,
                "text": f"Support problem: {ticket}\nVerified fix (written by {self.name}): {ai['text']}",
                "metadata": json.dumps({"learned_by": self.name}),
            })
            await asyncio.sleep(6)  # let Meko index the new memory

        return {
            "agent": self.name,
            "ticket": ticket,
            "meko_match": quality,
            "what_happened": source,
            "meko_search_seconds": search_s,
            "ai_seconds": ai["seconds"],
            "total_seconds": round(time.time() - t0, 1),
            "prompt_tokens_with_meko": ai["prompt_tokens"],
            "prompt_tokens_whole_kb_baseline": baseline,
            "total_tokens_billed": ai["total_tokens"],
            "reply": ai["text"],
        }


# ---------- the demo ----------
TICKETS = [
    # 1. Known problem: answered from the uploaded knowledge base
    ("agent1-solver", "My refund hasn't arrived and it has been 9 days."),
    # 2. New problem: Agent 1 solves it and SAVES the fix
    ("agent1-solver", "The app crashes whenever I try to apply a gift card at checkout."),
    # 3. Same problem, new words: Agent 2 REUSES Agent 1's fix
    ("agent2-helper", "Checkout keeps crashing on the gift card screen in your app."),
    # 4. Another new problem: this time Agent 2 solves and SAVES it
    ("agent2-helper", "Loyalty points were not added after my purchase."),
    # 5. Same problem, new words: Agent 1 REUSES Agent 2's fix
    ("agent1-solver", "I bought something yesterday but my reward points balance did not go up."),
]


async def main():
    headers = {"Authorization": f"Bearer {MEKO_KEY}"}
    async with streamablehttp_client(MEKO_URL, headers=headers) as (read, write, _):
        async with ClientSession(read, write) as s:
            await s.initialize()

            setup = parse(await s.call_tool("conversation_create", {"title": "setup"}))
            packs = parse(await s.call_tool("datapack_list", {"conversation_id": setup["id"]}))
            dp_id = next(p["datapack_id"] for p in packs if p["datapack_name"] == DATAPACK_NAME)

            if os.environ.get("RESET") == "1":
                # Forget what the agents learned in earlier runs (this datapack only),
                # so the demo starts fresh. The uploaded knowledge base is NOT touched.
                await s.call_tool("memory_delete_all", {
                    "conversation_id": setup["id"], "datapack_id": dp_id})
                print("RESET: cleared learned memories in", DATAPACK_NAME)
                await asyncio.sleep(3)

            agents = {}
            for name in ("agent1-solver", "agent2-helper"):
                agents[name] = SupportAgent(name, s, dp_id)
                await agents[name].start()

            results = []
            for agent_name, ticket in TICKETS:
                print(f"\n>>> {agent_name} gets ticket: {ticket}")
                r = await agents[agent_name].handle(ticket)
                results.append(r)
                print(f"    Meko match: {r['meko_match']} | {r['what_happened']}")
                print(f"    Time: Meko search {r['meko_search_seconds']}s, "
                      f"AI {r['ai_seconds']}s, total {r['total_seconds']}s")
                print(f"    Prompt tokens: {r['prompt_tokens_with_meko']} with Meko"
                      + (f" vs {r['prompt_tokens_whole_kb_baseline']} if whole KB pasted in"
                         if r['prompt_tokens_whole_kb_baseline'] else ""))
                print(f"    Reply: {r['reply'][:300]}")

            print("\n" + "=" * 78)
            print("SUMMARY")
            print("=" * 78)
            for r in results:
                print(f"{r['agent']:14} | {r['what_happened']:36} | "
                      f"search {r['meko_search_seconds']:>5}s | AI {r['ai_seconds']:>5}s | "
                      f"prompt tokens {r['prompt_tokens_with_meko']}")

            with open("results.json", "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2)
            print("\nSaved full results to results.json")


asyncio.run(main())
