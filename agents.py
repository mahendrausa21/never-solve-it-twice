"""agents.py - the shared "brain" for Never Solve It Twice.

Used by the terminal demo and the SupportIQ web dashboard.

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

from dotenv import load_dotenv
from google import genai
from google.genai import types
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

MEKO_URL = "https://mcp.mekodata.ai/mcp"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite")
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "").strip() or None
DATAPACK_NAME = os.environ.get("DATAPACK_NAME", "support-hackathon")
# Only ask the model "same problem?" when Meko itself found the draft at least this similar.
DRAFT_MATCH_MIN_SIMILARITY = float(os.environ.get("DRAFT_MATCH_MIN_SIMILARITY", "0.5"))
KB_FILE = "support_knowledge_base.md"  # used only for the "whole KB pasted in" baseline

llm = genai.Client(
    api_key=GEMINI_KEY,
    http_options=types.HttpOptions(timeout=20000),
) if GEMINI_KEY else None
llm_error_count = 0

KB_TEXT = ""
if os.path.exists(KB_FILE):
    with open(KB_FILE, encoding="utf-8") as f:
        KB_TEXT = f.read()

BENCHMARK_KB_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "support_knowledge_base_100.json")
BENCHMARK_KB_TEXT = ""
if os.path.exists(BENCHMARK_KB_FILE):
    with open(BENCHMARK_KB_FILE, encoding="utf-8") as f:
        BENCHMARK_KB_TEXT = json.dumps(json.load(f), ensure_ascii=False)

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
    except json.JSONDecodeError:
        return {"raw": text}


def _http_status_code(err):
    status = getattr(err, "status_code", None)
    if status is None:
        status = getattr(err, "code", None)
    try:
        return int(status)
    except (TypeError, ValueError):
        return None


def _generate_content(prompt, model):
    for attempt in range(3):
        try:
            try:
                cfg = types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(thinking_budget=0))
                resp = llm.models.generate_content(
                    model=model, contents=prompt, config=cfg)
            except Exception as err:
                if "thinking" not in str(err).lower():
                    raise
                resp = llm.models.generate_content(model=model, contents=prompt)
            return resp
        except Exception as err:
            status = _http_status_code(err)
            if attempt == 2 or (status != 429 and not (status is not None and 500 <= status <= 599)):
                raise
            time.sleep(2 ** attempt)


def ask_llm(prompt):
    """Blocking Gemini call. Returns text, usage counts, model, and elapsed time."""
    global llm_error_count
    t_start = time.time()
    try:
        if llm is None:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        models = [GEMINI_MODEL]
        if GEMINI_FALLBACK_MODEL and GEMINI_FALLBACK_MODEL != GEMINI_MODEL:
            models.append(GEMINI_FALLBACK_MODEL)
        response = None
        used_model = None
        last_error = None
        for model in models:
            try:
                response = _generate_content(prompt, model)
                used_model = model
                break
            except Exception as err:
                last_error = err
        if response is None:
            raise last_error

        u = getattr(response, "usage_metadata", None)
        prompt_reported = getattr(u, "prompt_token_count", None)
        total_reported = getattr(u, "total_token_count", None)
        prompt_tokens = prompt_reported or 0
        total_tokens = total_reported or 0
        completion_tokens = getattr(u, "candidates_token_count", None)
        if completion_tokens is None:
            completion_tokens = max(total_tokens - prompt_tokens, 0)
        return {
            "text": response.text.strip(),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "model": used_model,
            "usage_known": prompt_reported is not None and total_reported is not None,
            "seconds": round(time.time() - t_start, 1),
        }
    except Exception:
        llm_error_count += 1
        raise


def usage_summary(response):
    return {
        key: response[key]
        for key in ("prompt_tokens", "completion_tokens", "total_tokens", "model",
                    "usage_known", "seconds")
        if key in response
    }


def count_tokens(text):
    if llm is None:
        raise RuntimeError("GEMINI_API_KEY is not configured.")
    response = llm.models.count_tokens(model=GEMINI_MODEL, contents=text)
    if response.total_tokens is None:
        raise RuntimeError("Gemini did not return a token count.")
    return response.total_tokens


# Filler words never count as evidence that two tickets are about the same problem.
_FILLER = set("""
the and for was were are you your with that this have has had not but from they them what when
where which while will would could should been being into onto over under again very just also
than then there their about after before because each both more most some such only own same
other another my me it its is in on of to at as be by we our an or if so do did does can get got
""".split())


def quick_same(ticket, memory):
    """Free check: do the ticket and a saved draft share enough *meaningful* words?

    Filler words ("was", "for", "the") are ignored, and at least three meaningful
    words must be shared, so a different problem that merely mentions an order or a
    charge is not mistaken for the same one.
    """
    problem = memory.split("Suggested fix:")[0].split("Support problem:")[-1]
    words = lambda t: {w for w in re.findall(r"[a-z]{3,}", t.lower()) if w not in _FILLER}
    a, b = words(ticket), words(problem)
    shared = a & b
    return len(shared) >= 3 and len(shared) / min(len(a), len(b)) >= 0.6


def is_relevant(chunk):
    """Is a knowledge-base hit relevant enough to answer from?"""
    if not isinstance(chunk, dict) or not chunk.get("chunk_text"):
        return False
    if chunk.get("rerank_score") is not None:
        return chunk["rerank_score"] >= 0.5
    if chunk.get("distance") is not None:
        return chunk["distance"] <= 0.45
    return False


def make_kb_entry(draft_text, on_usage=None):
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
    result = ask_llm(prompt)
    if on_usage:
        on_usage(result)
    return result["text"]


def same_problem(ticket, candidate, on_usage=None):
    """Ask the AI whether a saved draft is about the same problem as the ticket."""
    clean = re.sub(r"^\[DRAFT[^\]]*\]\s*", "", candidate)[:600]
    prompt = (
        "Do the CUSTOMER TICKET and the SAVED ENTRY describe the same underlying customer "
        "problem, so that the same fix would apply even though the wording differs? "
        "A different cause, a different charge or fee, or a different feature is NO. "
        "Answer with exactly one word: YES or NO.\n\n"
        f"CUSTOMER TICKET: {ticket}\n\nSAVED ENTRY: {clean}"
    )
    result = ask_llm(prompt)
    if on_usage:
        on_usage(result)
    return result["text"].strip().upper().startswith("YES")


# ---------- Meko connection ----------
class Meko:
    """One open connection to Meko plus the id of your datapack."""

    async def connect(self):
        api_key = os.environ.get("MEKO_API_KEY")
        if not api_key:
            raise RuntimeError("MEKO_API_KEY is not configured.")
        self.needs_reconnect = False
        self.stack = AsyncExitStack()
        headers = {"Authorization": "Bearer " + api_key}
        try:
            read, write, _ = await self.stack.enter_async_context(
                streamablehttp_client(MEKO_URL, headers=headers))
            self.session = await self.stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            setup = await self.call("conversation_create", {"title": "setup"})
            if not isinstance(setup, dict) or not setup.get("id"):
                raise RuntimeError("Meko did not return an id for its setup conversation.")
            self.setup_conv = setup["id"]
            packs = await self.call("datapack_list", {"conversation_id": self.setup_conv})
            if isinstance(packs, dict):
                packs = packs.get("datapacks") or packs.get("results") or packs.get("items") or []
            if not isinstance(packs, list):
                raise RuntimeError("Meko returned an unsupported datapack-list response.")
            datapack = next((p for p in packs if p.get("datapack_name") == DATAPACK_NAME), None)
            if not datapack or not datapack.get("datapack_id"):
                raise RuntimeError(f"Meko datapack {DATAPACK_NAME!r} was not found or accessible.")
            self.dp = datapack["datapack_id"]
            return self
        except Exception:
            await self.stack.aclose()
            raise

    async def call(self, tool, args):
        retryable = tool in {
            "datapack_list", "context_search", "knowledgebase_search", "memory_get_all",
            "selflearning_status", "selflearning_query_log", "selflearning_shortfalls",
        }
        for attempt in range(3 if retryable else 1):
            try:
                response = await asyncio.wait_for(self.session.call_tool(tool, args), timeout=30)
                break
            except asyncio.TimeoutError:
                if attempt == 2 or not retryable:
                    self.needs_reconnect = True
                    raise TimeoutError(f"Meko {tool} timed out after 30 seconds.")
                await asyncio.sleep(0.5 * (attempt + 1))
            except Exception:
                self.needs_reconnect = True
                raise
        data = parse(response)
        if response.isError or (isinstance(data, dict) and data.get("error")):
            detail = (data.get("detail") or data.get("error")) if isinstance(data, dict) else data
            raise RuntimeError(f"Meko {tool} failed: {detail}")
        return data

    async def create_conversation(self, title):
        result = await self.call("conversation_create", {
            "title": title, "datapack_id": self.dp,
        })
        if not isinstance(result, dict) or not result.get("id"):
            raise RuntimeError("Meko did not return an id for the ticket conversation.")
        return result["id"]

    async def record_ticket(self, conversation_id, ticket, reply, status, trace, ticket_id, agent):
        return await self.call("conversation_add_message", {
            "conversation_id": conversation_id,
            "input": ticket,
            "output": reply,
            "metadata": json.dumps({
                "ticket_id": ticket_id, "status": status, "agent": agent, "trace": trace,
            }),
            "datapack_id": self.dp,
        })

    async def record_token_usage(self, conversation_id, call_name, usage, message_id=None):
        if not usage.get("usage_known"):
            return False
        request = {
            "conversation_id": conversation_id,
            "name": call_name,
            "input_tokens": usage["prompt_tokens"],
            "output_tokens": usage["completion_tokens"],
            "total_tokens": usage["total_tokens"],
            "model": usage.get("model") or GEMINI_MODEL,
            "datapack_id": self.dp,
        }
        if message_id:
            request["message_id"] = message_id
        await self.call("track_token_usage", request)
        return True

    async def close(self):
        await self.stack.aclose()

    # --- learned memories (drafts) and approval ---
    @staticmethod
    def _memory_rows(payload, *keys):
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict):
            return []
        for key in keys:
            items = payload.get(key)
            if isinstance(items, list):
                return items
        return []

    async def list_learnings(self):
        """Unapproved memories the agents have learned: [{id, text}, ...]."""
        r = await self.call("memory_get_all", {
            "conversation_id": self.setup_conv, "datapack_id": self.dp,
            "page_size": 100, "promoted": False})
        self.last_raw_listing = r
        items = self._memory_rows(r, "memories", "results", "items", "data")
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = (item.get("data") or item.get("memory") or item.get("text")
                    or item.get("content") or item.get("memory_text") or "")
            learned_by = (re.search(r"written by ([\w-]+)", text, re.IGNORECASE) or [None, None])[1]
            out.append({
                "id": item.get("id") or item.get("memory_id"),
                "text": text,
                "learned_by": learned_by,
                "learned_at": item.get("created_at"),
                "created_by": item.get("user_name"),
            })
        return out

    async def list_promoted(self):
        result = await self.call("memory_get_all", {
            "conversation_id": self.setup_conv, "datapack_id": self.dp,
            "page_size": 100, "promoted": True})
        items = self._memory_rows(result, "promoted_memories", "memories", "results", "items")
        promoted = []
        for item in items:
            if not isinstance(item, dict):
                continue
            text = (item.get("data") or item.get("memory") or item.get("text")
                    or item.get("content") or item.get("memory_text") or "")
            learned_by = (re.search(r"written by ([\w-]+)", text, re.IGNORECASE) or [None, None])[1]
            promoted.append({
                "id": item.get("id") or item.get("memory_id"),
                "text": text,
                "learned_by": learned_by or item.get("user_name"),
                "learned_at": item.get("created_at"),
                "approved_at": item.get("promoted_to_kb_at"),
                "approved_by": item.get("promoted_by_name"),
            })
        return promoted

    async def list_selflearning(self):
        status = await self.call("selflearning_status", {
            "datapack_id": self.dp, "conversation_id": self.setup_conv, "last": "14d"})
        query_log = await self.call("selflearning_query_log", {
            "datapack_id": self.dp, "conversation_id": self.setup_conv,
            "band": "all", "last": "7d", "page": 1})
        shortfalls = []
        for step_id in ("add_context", "clarify_prompts", "refresh_sources", "check_source_errors"):
            shortfalls.append(await self.call("selflearning_shortfalls", {
                "todo_id": step_id, "datapack_id": self.dp,
                "conversation_id": self.setup_conv, "page": 1}))
        return {"status": status, "query_log": query_log, "shortfalls": shortfalls}

    async def verify_fix(self, log_id):
        return await self.call("selflearning_verify", {
            "datapack_id": self.dp, "log_id": log_id, "conversation_id": self.setup_conv})

    async def approve(self, memory_ids):
        """Promote drafts into the shared, approved knowledge (one-way in Meko)."""
        return await self.call("memory_promote", {
            "conversation_id": self.setup_conv, "datapack_id": self.dp,
            "memory_ids": list(memory_ids)})

    async def approve_draft(self, memory_id, draft_text):
        """Human approval: rewrite the draft as a clean entry, then promote it."""
        usage = []
        entry = await asyncio.to_thread(make_kb_entry, draft_text, usage.append)
        rewrite_conversation = await self.create_conversation(f"Approve draft {memory_id}")
        message = await self.call("conversation_add_message", {
            "conversation_id": rewrite_conversation,
            "input": draft_text,
            "output": entry,
            "metadata": json.dumps({"memory_id": memory_id, "operation": "draft_approval_rewrite"}),
            "datapack_id": self.dp,
        })
        message_id = (
            message.get("message_id") or message.get("id")
            if isinstance(message, dict) else None
        )
        usage_tracked = False
        if usage:
            usage_tracked = await self.record_token_usage(
                rewrite_conversation, "Knowledge base entry rewrite", usage[0], message_id)
        update = await self.call("memory_update", {
            "conversation_id": self.setup_conv, "datapack_id": self.dp,
            "memory_id": memory_id, "text": entry})
        promote = await self.approve([memory_id])
        return {
            "entry": entry, "update": update, "promote": promote,
            "llm_usage": usage[0] if usage else None,
            "usage_tracked": usage_tracked,
        }

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
        if not isinstance(res, dict) or not res.get("id"):
            raise RuntimeError(f"Meko did not return a session id for {self.name}.")
        self.conv = res["id"]
        return self

    async def handle(
        self, ticket, on_event=None, conversation_id=None, include_baseline=True,
        measure_baseline=False,
    ):
        t0 = time.time()
        trace = []
        llm_calls = []
        conv = conversation_id or self.conv

        def log(step, detail="", state="completed"):
            event = {
                "t": round(time.time() - t0, 1),
                "step": step,
                "detail": detail,
                "state": state,
            }
            if state != "running":
                trace.append(event)
            if on_event:
                on_event(event)

        log("Ticket received", ticket)
        log("Memory Agent", "Searching Meko shared context.", "running")
        search = await self.meko.call("context_search", {
            "conversation_id": conv, "datapack_id": self.meko.dp, "query": ticket})
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
                    "conversation_id": conv, "datapack_id": self.meko.dp,
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
                log("Direct knowledge search failed", type(err).__name__)
                raise RuntimeError(
                    f"Meko direct knowledge search failed ({type(err).__name__}).") from err
        if not good_kb and drafts:
            same = [m for m in drafts[:2] if quick_same(ticket, m["memory"])]
            if not same:  # reworded tickets share few words, so ask the model, but only
                # when Meko says the draft is at least reasonably similar (saves a slow call)
                close = [m for m in drafts[:2]
                         if (m.get("raw_similarity_score") is None
                             or m["raw_similarity_score"] >= DRAFT_MATCH_MIN_SIMILARITY)]
                if close:
                    usage = []
                    try:
                        matched = await asyncio.to_thread(
                            same_problem, ticket, close[0]["memory"], usage.append)
                    except Exception:
                        matched = False
                    if usage:
                        llm_calls.append({"name": "Draft match check", **usage_summary(usage[-1])})
                    if matched:
                        same = [close[0]]
            log("Checked drafts",
                f"{len(same)} of {min(len(drafts), 2)} saved draft(s) describe the same problem")
            drafts = same
        search_s = round(time.time() - t0, 1)
        log(
            "Memory Agent",
            f"Found {len(good_kb)} relevant approved result(s) and {len(drafts)} unapproved draft(s).",
        )
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
            log("Resolution Agent", "Writing a customer reply from approved knowledge.", "running")
            ai = await asyncio.to_thread(ask_llm, prompt)
            llm_calls.append({"name": "Support reply", **usage_summary(ai)})
            log("Resolution Agent", "Reply written from approved knowledge.")
            if include_baseline and KB_TEXT:
                baseline = await asyncio.to_thread(count_tokens, prompt.replace(ctx, KB_TEXT))

        elif drafts:
            status = "draft_reused"
            ctx = "\n---\n".join(m["memory"] for m in drafts)
            log("Decision", "Only an UNAPPROVED draft found -> reusing it, flagged for review")
            prompt = (f"You are a ShopEasy support agent. {RULES}\nThe context is an "
                      f"UNVERIFIED draft written by another agent. Write a short, friendly "
                      f"reply (max 4 sentences) that offers it as a suggestion and says a "
                      f"specialist will confirm.\n\nCONTEXT:\n{ctx}\n\nCUSTOMER TICKET: {ticket}")
            log("Resolution Agent", "Writing a review-flagged reply from an unapproved draft.", "running")
            ai = await asyncio.to_thread(ask_llm, prompt)
            llm_calls.append({"name": "Support reply", **usage_summary(ai)})
            log("Resolution Agent", "Review-flagged reply written.")
            if include_baseline and KB_TEXT:
                baseline = await asyncio.to_thread(count_tokens, prompt.replace(ctx, KB_TEXT))

        else:
            status = "new_draft_saved"
            log("Decision", "Nobody has solved this -> solving it, then saving as a DRAFT")
            prompt = (f"You are a ShopEasy support agent. {RULES}\nNobody has handled this "
                      f"problem before, so give only generic, cautious troubleshooting "
                      f"(no product-specific claims): one-line problem summary, 2-3 generic "
                      f"steps, and say a human specialist should confirm. Max 5 sentences."
                      f"\n\nCUSTOMER TICKET: {ticket}")
            log("Resolution Agent", "Generating a cautious new solution.", "running")
            ai = await asyncio.to_thread(ask_llm, prompt)
            llm_calls.append({"name": "Support reply", **usage_summary(ai)})
            log("Resolution Agent", "Proposed solution generated.")
            log("Meko Memory", "Saving proposed solution as an unapproved draft.", "running")
            added = await self.meko.call("memory_add", {
                "conversation_id": conv, "datapack_id": self.meko.dp,
                "text": (f"[DRAFT - unverified, written by {self.name}] "
                         f"Support problem: {ticket}\nSuggested fix: {ai['text']}"),
                "metadata": json.dumps({"learned_by": self.name, "status": "draft"}),
            })
            log("Meko Memory", "New fix stored as an unapproved draft.")
            res = added.get("results") or []
            memory_id = res[0].get("id") if res else None
            await asyncio.sleep(6)  # let Meko index it

        meko_total_seconds = round(time.time() - t0, 1)
        log("Reply written", f"{ai['seconds']}s, {ai['prompt_tokens']} prompt tokens")
        baseline_usage = None
        baseline_error = None
        if measure_baseline:
            if not BENCHMARK_KB_TEXT:
                baseline_error = "BenchmarkKnowledgeBaseUnavailable"
            else:
                baseline_prompt = (
                    f"You are a ShopEasy support agent. {RULES}\nUsing ONLY the complete "
                    "knowledge base below, write a short, friendly reply (max 4 sentences). "
                    "If the answer is not supported, say so and recommend human review.\n\n"
                    f"COMPLETE KNOWLEDGE BASE:\n{BENCHMARK_KB_TEXT}\n\nCUSTOMER TICKET: {ticket}"
                )
                try:
                    baseline_response = await asyncio.to_thread(ask_llm, baseline_prompt)
                    baseline_usage = usage_summary(baseline_response)
                except Exception as err:
                    baseline_error = type(err).__name__
        retrieved = []
        for item in good_kb:
            score = item.get("rerank_score")
            score_type = "rerank"
            if score is None:
                score = item.get("distance")
                score_type = "distance"
            retrieved.append({
                "id": item.get("id") or item.get("memory_id"),
                "score": score,
                "score_type": score_type,
                "source": "approved",
            })
        for item in drafts:
            retrieved.append({
                "id": item.get("id"),
                "score": item.get("raw_similarity_score", item.get("score")),
                "score_type": "similarity",
                "source": "draft",
            })
        return {
            "agent": self.name,
            "ticket": ticket,
            "status": status,
            "needs_review": status in ("draft_reused", "new_draft_saved"),
            "meko_match": quality,
            "retrieved_memories": retrieved,
            "reply": ai["text"],
            "memory_id": memory_id,
            "meko_search_seconds": search_s,
            "ai_seconds": ai["seconds"],
            "total_seconds": meko_total_seconds,
            "prompt_tokens": ai["prompt_tokens"],
            "baseline_prompt_tokens": baseline,
            "baseline_usage": baseline_usage,
            "baseline_error": baseline_error,
            "llm_calls": llm_calls,
            "trace": trace,
        }
