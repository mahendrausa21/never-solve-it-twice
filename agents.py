"""agents.py - the shared "brain" for Never Solve It Twice.

Used by the terminal demo now, and by the web dashboard next.

How a ticket is handled:
  1. Search Meko (knowledge base + learned memories).
  2. APPROVED knowledge found  -> answer from it (verified).
  3. Only an unapproved DRAFT found -> reuse it, but flag the reply for human review.
  4. Nothing found -> solve with the AI using generic, cautious advice, and save the
     result to Meko as an UNVERIFIED DRAFT.
A human can then APPROVE a draft, which promotes it into Meko's shared knowledge.
"""
import asyncio
import json
import os
import re
import time
from contextlib import AsyncExitStack

from google import genai
from google.genai import types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MEKO_URL = "https://mcp.mekodata.ai/mcp"
MEKO_KEY = os.environ.get("MEKO_API_KEY")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
DATAPACK_NAME = os.environ.get("DATAPACK_NAME", "support-hackathon")
KB_FILE = "support_knowledge_base.md"  # used only for the "whole KB pasted in" baseline

if not MEKO_KEY or not GEMINI_KEY:
    raise SystemExit("Set both MEKO_API_KEY and GEMINI_API_KEY first.")

llm = genai.Client(api_key=GEMINI_KEY)

KB_TEXT = ""
if os.path.exists(KB_FILE):
    with open(KB_FILE, encoding="utf-8") as f:
        KB_TEXT = f.read()

RULES = (
    "Rules: use only facts given to you. Never claim you checked a system, account, "
    "order or payment. Never invent app versions, policies, numbers, deadlines or team "
    "names that are not in the context. If something is unknown, say so and offer to "
    "escalate to a human specialist."
)


# ---------- helpers ----------
def parse(result):
    text = "".join(b.text for b in result.content if getattr(b, "text", None))
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def ask_llm(prompt):
    """Blocking Gemini call. Returns dict(text, prompt_tokens, total_tokens, seconds)."""
    for attempt in range(4):
        try:
            t = time.time()
            try:
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
            time.sleep(6)


def count_tokens(text):
    try:
        return llm.models.count_tokens(model=GEMINI_MODEL, contents=text).total_tokens
    except Exception:
        return 0


def is_relevant(chunk):
    """Is a knowledge-base hit relevant enough to answer from?"""
    if not isinstance(chunk, dict) or not chunk.get("chunk_text"):
        return False
    if chunk.get("rerank_score") is not None:
        return chunk["rerank_score"] >= 0.5
    if chunk.get("distance") is not None:
        return chunk["distance"] <= 0.45
    return False


def make_kb_entry(draft_text):
    """Rewrite an approved draft as a short, clean entry like the uploaded knowledge base."""
    clean = re.sub(r"^\[DRAFT[^\]]*\]\s*", "", draft_text)
    prompt = (
        "Rewrite this support draft as a short, clean knowledge-base entry in exactly "
        "this format, using only facts present in the draft:\n"
        "Section: Approved fixes > <short problem title>\n\n"
        "Problem: <one sentence>. Fix: <1-3 sentences>. "
        "Escalate to: <who, only if stated; otherwise 'a human specialist'>.\n\n"
        f"DRAFT:\n{clean}"
    )
    return ask_llm(prompt)["text"]


def same_problem(ticket, candidate):
    """Ask the AI whether a saved draft is about the same problem as the ticket."""
    clean = re.sub(r"^\[DRAFT[^\]]*\]\s*", "", candidate)[:600]
    prompt = (
        "Do the CUSTOMER TICKET and the SAVED ENTRY describe the same underlying customer "
        "problem (same kind of issue, even if worded differently)? "
        "Answer with exactly one word: YES or NO.\n\n"
        f"CUSTOMER TICKET: {ticket}\n\nSAVED ENTRY: {clean}"
    )
    try:
        return ask_llm(prompt)["text"].strip().upper().startswith("YES")
    except Exception:
        return False


# ---------- Meko connection ----------
class Meko:
    """One open connection to Meko plus the id of your datapack."""

    async def connect(self):
        self.stack = AsyncExitStack()
        headers = {"Authorization": f"Bearer {MEKO_KEY}"}
        read, write, _ = await self.stack.enter_async_context(
            streamablehttp_client(MEKO_URL, headers=headers))
        self.session = await self.stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        setup = await self.call("conversation_create", {"title": "setup"})
        self.setup_conv = setup["id"]
        packs = await self.call("datapack_list", {"conversation_id": self.setup_conv})
        self.dp = next(p["datapack_id"] for p in packs if p["datapack_name"] == DATAPACK_NAME)
        return self

    async def call(self, tool, args):
        return parse(await self.session.call_tool(tool, args))

    async def close(self):
        await self.stack.aclose()

    # --- learned memories (drafts) and approval ---
    async def list_learnings(self):
        """Unapproved memories the agents have learned: [{id, text}, ...]."""
        r = await self.call("memory_get_all", {
            "conversation_id": self.setup_conv, "datapack_id": self.dp, "page_size": 100})
        self.last_raw_listing = r
        if isinstance(r, list):
            items = r
        else:
            items = (r.get("results") or r.get("memories") or r.get("items")
                     or r.get("data") or [])
        if isinstance(items, dict):
            items = items.get("results") or items.get("memories") or list(items.values())
        out = []
        for i in items:
            if not isinstance(i, dict):
                continue
            text = (i.get("data") or i.get("memory") or i.get("text")
                    or i.get("content") or i.get("memory_text") or "")
            out.append({"id": i.get("id") or i.get("memory_id"), "text": text, "raw": i})
        return out

    async def approve(self, memory_ids):
        """Promote drafts into the shared, approved knowledge (one-way in Meko)."""
        return await self.call("memory_promote", {
            "conversation_id": self.setup_conv, "datapack_id": self.dp,
            "memory_ids": list(memory_ids)})

    async def approve_draft(self, memory_id, draft_text):
        """Human approval: rewrite the draft as a clean entry, then promote it."""
        entry = await asyncio.to_thread(make_kb_entry, draft_text)
        update = await self.call("memory_update", {
            "conversation_id": self.setup_conv, "datapack_id": self.dp,
            "memory_id": memory_id, "text": entry})
        promote = await self.approve([memory_id])
        return {"entry": entry, "update": update, "promote": promote}

    async def forget_all_drafts(self):
        return await self.call("memory_delete_all", {
            "conversation_id": self.setup_conv, "datapack_id": self.dp})


# ---------- the agent ----------
class SupportAgent:
    def __init__(self, name, meko):
        self.name = name
        self.meko = meko
        self.conv = None

    async def start(self):
        res = await self.meko.call("conversation_create", {"title": f"{self.name} session"})
        self.conv = res["id"]
        return self

    async def handle(self, ticket):
        t0 = time.time()
        trace = []

        def log(step, detail=""):
            trace.append({"t": round(time.time() - t0, 1), "step": step, "detail": detail})

        log("Ticket received", ticket)
        search = await self.meko.call("context_search", {
            "conversation_id": self.conv, "datapack_id": self.meko.dp, "query": ticket})
        found = search.get("results", {})
        quality = search.get("match_quality", "low")
        good_kb = [c for c in found.get("knowledge_base", []) if is_relevant(c)
                   or c.get("rerank_score", 0) >= 0.5]
        drafts = found.get("memory", [])
        if not good_kb:
            # The combined search has a strict cutoff that can hide approved entries,
            # so also ask the knowledge base directly.
            try:
                direct = await self.meko.call("knowledgebase_search", {
                    "conversation_id": self.conv, "datapack_id": self.meko.dp,
                    "query": ticket})
                hits = direct.get("results", []) if isinstance(direct, dict) else []
                good_kb = [c for c in hits if is_relevant(c)]
                if good_kb:
                    quality = "mid"
                    log("Direct knowledge search",
                        f"found {len(good_kb)} approved entry(ies) the combined search missed")
                elif hits and isinstance(hits[0], dict):
                    top = hits[0]
                    log("Direct knowledge search",
                        f"top hit not relevant enough (rerank={top.get('rerank_score')}, "
                        f"distance={top.get('distance')})")
            except Exception as err:
                log("Direct knowledge search failed", str(err)[:80])
        if not good_kb and drafts:
            same = []
            for m in drafts[:2]:
                if await asyncio.to_thread(same_problem, ticket, m["memory"]):
                    same.append(m)
            log("Checked drafts",
                f"{len(same)} of {min(len(drafts), 2)} saved draft(s) describe the same problem")
            drafts = same
        search_s = round(time.time() - t0, 1)
        log("Searched Meko",
            f"match quality: {quality}; approved knowledge hits: {len(good_kb)}; "
            f"unapproved drafts: {len(drafts)}")

        baseline = 0
        memory_id = None

        if good_kb:
            status = "verified"
            ctx = "\n---\n".join(c["chunk_text"] for c in good_kb)
            log("Decision", "Approved knowledge found -> answering from it")
            prompt = (f"You are a ShopEasy support agent. {RULES}\nUsing ONLY the context, "
                      f"write a short, friendly reply (max 4 sentences).\n\n"
                      f"CONTEXT:\n{ctx}\n\nCUSTOMER TICKET: {ticket}")
            ai = await asyncio.to_thread(ask_llm, prompt)
            if KB_TEXT:
                baseline = await asyncio.to_thread(count_tokens, prompt.replace(ctx, KB_TEXT))

        elif drafts:
            status = "draft_reused"
            ctx = "\n---\n".join(m["memory"] for m in drafts)
            log("Decision", "Only an UNAPPROVED draft found -> reusing it, flagged for review")
            prompt = (f"You are a ShopEasy support agent. {RULES}\nThe context is an "
                      f"UNVERIFIED draft written by another agent. Write a short, friendly "
                      f"reply (max 4 sentences) that offers it as a suggestion and says a "
                      f"specialist will confirm.\n\nCONTEXT:\n{ctx}\n\nCUSTOMER TICKET: {ticket}")
            ai = await asyncio.to_thread(ask_llm, prompt)
            if KB_TEXT:
                baseline = await asyncio.to_thread(count_tokens, prompt.replace(ctx, KB_TEXT))

        else:
            status = "new_draft_saved"
            log("Decision", "Nobody has solved this -> solving it, then saving as a DRAFT")
            prompt = (f"You are a ShopEasy support agent. {RULES}\nNobody has handled this "
                      f"problem before, so give only generic, cautious troubleshooting "
                      f"(no product-specific claims): one-line problem summary, 2-3 generic "
                      f"steps, and say a human specialist should confirm. Max 5 sentences."
                      f"\n\nCUSTOMER TICKET: {ticket}")
            ai = await asyncio.to_thread(ask_llm, prompt)
            added = await self.meko.call("memory_add", {
                "conversation_id": self.conv, "datapack_id": self.meko.dp,
                "text": (f"[DRAFT - unverified, written by {self.name}] "
                         f"Support problem: {ticket}\nSuggested fix: {ai['text']}"),
                "metadata": json.dumps({"learned_by": self.name, "status": "draft"}),
            })
            res = added.get("results") or []
            memory_id = res[0].get("id") if res else None
            log("Saved to Meko", "New fix stored as an unapproved draft")
            await asyncio.sleep(6)  # let Meko index it

        log("Reply written", f"{ai['seconds']}s, {ai['prompt_tokens']} prompt tokens")
        return {
            "agent": self.name,
            "ticket": ticket,
            "status": status,
            "needs_review": status in ("draft_reused", "new_draft_saved"),
            "meko_match": quality,
            "reply": ai["text"],
            "memory_id": memory_id,
            "meko_search_seconds": search_s,
            "ai_seconds": ai["seconds"],
            "total_seconds": round(time.time() - t0, 1),
            "prompt_tokens": ai["prompt_tokens"],
            "baseline_prompt_tokens": baseline,
            "trace": trace,
        }
