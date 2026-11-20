"""server.py - web dashboard for Never Solve It Twice.

Run:   python server.py
Then open http://localhost:8000 in your browser. External service credentials are
required for live ticket processing; the dashboard still starts if Meko is unavailable.
"""
import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import agents
from agents import Meko, SupportAgent, ask_llm, usage_summary

HERE = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.environ.get("SUPPORTIQ_HISTORY_FILE", os.path.join(HERE, "data", "history.jsonl"))
BENCHMARK_RESULTS_FILE = os.environ.get(
    "SUPPORTIQ_BENCHMARK_RESULTS_FILE", os.path.join(HERE, "data", "benchmark_results.json"))
BENCHMARK_FIXTURE_FILE = os.path.join(HERE, "data", "benchmark_tickets.json")
DEMO_STORY_FIXTURE_FILE = os.path.join(HERE, "data", "demo_story_tickets.json")
state = {
    "history": [], "meko": None, "agents": {}, "meko_error": None,
    "history_error": None, "benchmark_error": None,
}
lock = asyncio.Lock()  # one Meko request at a time keeps the connection simple
connection_lock = asyncio.Lock()
history_lock = asyncio.Lock()
TRIAGE_CATEGORIES = {
    "Account & access",
    "Payments & billing",
    "Orders & delivery",
    "Returns & refunds",
    "App & notifications",
    "Checkout & discounts",
    "Products & rewards",
    "Other",
}
PRIORITIES = {"low", "normal", "high", "urgent"}


@asynccontextmanager
async def lifespan(app):
    load_history()
    await connect_meko()
    print("SupportIQ started. Open http://localhost:8000 in your browser.")
    yield
    if state["meko"] is not None:
        await state["meko"].close()


def load_history():
    state["history"] = []
    state["history_error"] = None
    try:
        with open(HISTORY_FILE, encoding="utf-8") as history_file:
            for line_number, line in enumerate(history_file, start=1):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    state["history_error"] = f"Invalid history record at line {line_number}."
                    continue
                if isinstance(row, dict):
                    state["history"].append(row)
    except FileNotFoundError:
        return
    except OSError as err:
        state["history_error"] = f"Could not read ticket history ({type(err).__name__})."


async def persist_history(row):
    async with history_lock:
        try:
            await asyncio.to_thread(
                append_history_line,
                json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            )
            state["history_error"] = None
            return True
        except OSError as err:
            state["history_error"] = f"Could not save ticket history ({type(err).__name__})."
            return False


def append_history_line(line):
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as history_file:
        history_file.write(line + "\n")


async def connect_meko():
    async with connection_lock:
        if state["meko"] is not None and not state["meko"].needs_reconnect:
            return True
        async with lock:
            previous = state["meko"]
            if previous is not None:
                state["meko"] = None
                state["agents"] = {}
                try:
                    await previous.close()
                except Exception as err:
                    print(f"Meko connection cleanup failed ({type(err).__name__}).")
            meko = Meko()
            try:
                await meko.connect()
                agents = {
                    "agent1-solver": await SupportAgent("agent1-solver", meko).start(),
                    "agent2-helper": await SupportAgent("agent2-helper", meko).start(),
                }
            except (Exception, asyncio.CancelledError) as err:  # the MCP client can raise CancelledError when Meko is unreachable
                if hasattr(meko, "stack"):
                    try:
                        await meko.close()
                    except Exception as close_err:
                        print(f"Meko connection cleanup failed ({type(close_err).__name__}).")
                state["meko"] = None
                state["agents"] = {}
                state["meko_error"] = type(err).__name__
                print(f"Meko connection unavailable ({type(err).__name__}); requests can retry.")
                return False
            state["meko"] = meko
            state["agents"] = agents
            state["meko_error"] = None
            return True


app = FastAPI(lifespan=lifespan)


class TicketIn(BaseModel):
    ticket: str = Field(min_length=1, max_length=4000)
    # Optional for backwards compatibility. The dashboard leaves routing to the server.
    agent: str | None = None
    measure_baseline: bool = False


class ApproveIn(BaseModel):
    id: str
    text: str


class MemorySearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)


class VerifyIn(BaseModel):
    log_id: str = Field(min_length=1, max_length=200)


def sanitize_learning_data(value):
    """Keep aggregate learning signals without forwarding raw customer text or emails."""
    blocked = {
        "query", "queries", "querytext", "userquery", "userqueries", "searchquery",
        "searchterm", "input", "inputtext", "ticket", "tickettext", "prompt",
        "prompttext", "email", "emailaddress", "rawquery", "rawqueries",
    }
    if isinstance(value, dict):
        return {
            key: sanitize_learning_data(item)
            for key, item in value.items()
            if key.lower().replace("_", "") not in blocked
        }
    if isinstance(value, list):
        return [sanitize_learning_data(item) for item in value]
    return value


KEYWORDS = [
    ("Returns & refunds", ["return", "refund", "label", "exchange"]),
    ("Account & access", ["log in", "login", "password", "sign in", "otp", "account"]),
    ("Payments & billing", ["payment", "charge", "wallet", "card", "billing"]),
    ("Orders & delivery", ["order", "delivery", "shipping", "package", "tracking"]),
]


async def triage_ticket(ticket):
    """Instant keyword triage: no model call, no tokens."""
    low = ticket.lower()
    category = next((c for c, words in KEYWORDS
                     if c in TRIAGE_CATEGORIES and any(w in low for w in words)), "Other")
    urgent = any(w in low for w in ("urgent", "fraud", "stolen", "charged twice"))
    return {"category": category, "intent": ticket[:50].strip(), "priority": "urgent" if urgent else "normal",
            "seconds": 0.0, "usage": None, "status": "completed", "error": None}


@app.get("/")
def home():
    return FileResponse(os.path.join(HERE, "index.html"))


async def process_ticket(body, publish=None):
    ticket = body.ticket.strip()
    if not ticket:
        raise HTTPException(400, "Describe the customer's issue before submitting.")
    request_started = datetime.now(timezone.utc)
    ticket_id = f"TKT-{uuid.uuid4().hex[:8].upper()}"
    if not await connect_meko():
        failed_at = datetime.now(timezone.utc).isoformat()
        failure = {
            "ticket_id": ticket_id, "ticket": ticket, "status": "error",
            "error_type": state["meko_error"] or "MekoUnavailable",
            "created_at": request_started.isoformat(), "completed_at": failed_at,
            "trace": [{
                "t": round((datetime.now(timezone.utc) - request_started).total_seconds(), 1),
                "at": failed_at, "step": "Meko connection", "detail": "Connection unavailable.",
                "state": "failed",
            }],
        }
        state["history"].append(failure)
        await persist_history(failure)
        raise HTTPException(503, "Meko is unavailable. Check the server configuration and retry.")
    if not state["agents"]:
        raise HTTPException(503, "No support agents are available.")
    if body.agent is not None and body.agent not in state["agents"]:
        raise HTTPException(400, "The requested agent is not available.")
    def make_event(step, detail, elapsed, event_state="completed"):
        elapsed = round(elapsed, 1)
        event = {
            "t": elapsed,
            "at": (request_started + timedelta(seconds=elapsed)).isoformat(),
            "step": step,
            "detail": detail,
            "state": event_state,
        }
        if publish:
            publish({"type": "step", **event})
        return event

    request_event = make_event("Request received", f"Ticket {ticket_id} entered the support workflow.", 0)
    make_event("Triage Agent", "Classifying by keywords.", 0, "running")
    triage = await triage_ticket(ticket)
    triage_step = "Triage Agent" if triage["status"] == "completed" else "Triage fallback"
    triage_detail = (
        f"{triage['category']} · {triage['intent']} · {triage['priority']} priority"
        if triage["status"] == "completed"
        else f"Classification unavailable ({triage['error']}); using Other / normal."
    )
    triage_event = make_event(
        triage_step, triage_detail, triage["seconds"],
        "warning" if triage["status"] == "fallback" else "completed")
    if body.agent is not None:
        agent = state["agents"][body.agent]
    else:
        counts = {
            name: sum(row.get("agent") == name for row in state["history"])
            for name in state["agents"]
        }
        agent_name = min(counts, key=counts.get)
        agent = state["agents"][agent_name]
    agent_offset = 0.0
    assigned_event = make_event(
        "Orchestrator",
        f"Assigned to {agent.name} using least-used-agent routing.",
        triage["seconds"],
    )

    def forward_agent_event(event):
        make_event(
            event["step"],
            event["detail"],
            agent_offset + event["t"],
            event.get("state", "completed"),
        )

    try:
        async with lock:
            agent_offset = (datetime.now(timezone.utc) - request_started).total_seconds()
            conversation_id = await state["meko"].create_conversation(f"SupportIQ {ticket_id}")
            if body.measure_baseline:
                result = await agent.handle(
                    ticket, on_event=forward_agent_event, conversation_id=conversation_id,
                    include_baseline=False, measure_baseline=True)
            else:
                result = await agent.handle(
                    ticket, on_event=forward_agent_event, conversation_id=conversation_id,
                    include_baseline=False)
    except Exception as err:
        failed_at = datetime.now(timezone.utc).isoformat()
        failure = {
            "ticket_id": ticket_id, "ticket": ticket, "status": "error",
            "error_type": type(err).__name__, "agent": agent.name,
            "category": triage["category"], "intent": triage["intent"],
            "priority": triage["priority"], "created_at": request_started.isoformat(),
            "completed_at": failed_at, "trace": [
                request_event, triage_event,
                {"t": round((datetime.now(timezone.utc) - request_started).total_seconds(), 1),
                 "at": failed_at, "step": "Request failed",
                 "detail": f"Workflow stopped ({type(err).__name__}).", "state": "failed"},
            ],
        }
        state["history"].append(failure)
        await persist_history(failure)
        raise HTTPException(500, f"{type(err).__name__}: {err}") from err
    result["ticket_id"] = ticket_id
    result["created_at"] = request_started.isoformat()
    result["completed_at"] = datetime.now(timezone.utc).isoformat()
    result["category"] = triage["category"]
    result["intent"] = triage["intent"]
    result["priority"] = triage["priority"]
    result["triage_seconds"] = triage["seconds"]
    result["triage_status"] = triage["status"]
    result["triage_error"] = triage["error"]
    result["total_seconds"] = round(result["total_seconds"] + agent_offset, 1)
    for event in result["trace"]:
        event["t"] = round(event["t"] + agent_offset, 1)
        event["at"] = (request_started + timedelta(seconds=event["t"])).isoformat()
    result["trace"] = [request_event, triage_event, assigned_event, *result["trace"]]
    llm_calls = []
    if triage["usage"]:
        llm_calls.append({"name": "Ticket triage", **usage_summary(triage["usage"])})
    llm_calls.extend(result.get("llm_calls", []))
    result["llm_calls"] = llm_calls
    usage_known = [call for call in llm_calls if call.get("usage_known")]
    result["token_usage"] = {
        "prompt_tokens": sum(call["prompt_tokens"] for call in usage_known),
        "completion_tokens": sum(call["completion_tokens"] for call in usage_known),
        "total_tokens": sum(call["total_tokens"] for call in usage_known),
        "measured_calls": len(usage_known),
        "unmeasured_calls": len(llm_calls) - len(usage_known),
    }
    result["knowledge_source"] = {
        "verified": "approved_knowledge",
        "draft_reused": "unapproved_draft",
        "new_draft_saved": "new_draft",
    }.get(result["status"], "unknown")
    result["meko_conversation_id"] = conversation_id
    tracking_errors = []
    recorded_calls = 0
    message_id = None
    async with lock:
        try:
            message = await state["meko"].record_ticket(
                conversation_id, ticket, result["reply"], result["status"], result["trace"],
                ticket_id, agent.name)
            if isinstance(message, dict):
                message_id = message.get("message_id") or message.get("id")
            result["meko_trace_recorded"] = True
        except Exception as err:
            result["meko_trace_recorded"] = False
            tracking_errors.append(f"conversation trace: {type(err).__name__}")
        for call in llm_calls:
            try:
                if await state["meko"].record_token_usage(
                        conversation_id, call["name"], call, message_id=message_id):
                    recorded_calls += 1
            except Exception as err:
                tracking_errors.append(f"{call['name']}: {type(err).__name__}")
    result["token_usage"]["tracked_calls"] = recorded_calls
    result["token_usage"]["tracking_errors"] = tracking_errors
    result["persistence_warning"] = None
    state["history"].append(result)
    if not await persist_history(result):
        result["persistence_warning"] = state["history_error"]
    return result


@app.post("/api/ticket")
async def handle_ticket(body: TicketIn):
    return await process_ticket(body)


@app.post("/api/ticket/stream")
async def stream_ticket(body: TicketIn):
    async def events():
        queue = asyncio.Queue()

        async def run():
            try:
                result = await process_ticket(body, publish=queue.put_nowait)
                queue.put_nowait({"type": "complete", "result": result})
            except HTTPException as err:
                queue.put_nowait({"type": "error", "detail": err.detail})
            except Exception as err:
                queue.put_nowait({"type": "error", "detail": f"{type(err).__name__}: {err}"})

        task = asyncio.create_task(run())
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event["type"] in {"complete", "error"}:
                break
        await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/health")
def health():
    reconnect_pending = state["meko"] is not None and state["meko"].needs_reconnect
    meko_connected = state["meko"] is not None and not reconnect_pending
    history = state["history"]
    ticket_error_rate = (
        round(sum(row.get("status") == "error" for row in history) / len(history), 4)
        if history else None
    )
    return {
        "api": "connected",
        "meko": "connected" if meko_connected else "disconnected",
        "agents": len(state["agents"]),
        "model": os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"),
        "llm_error_count": agents.llm_error_count,
        "ticket_error_rate": ticket_error_rate,
        "datapack_name": os.environ.get("DATAPACK_NAME", "support-hackathon"),
        "meko_error": state["meko_error"] or ("ReconnectPending" if reconnect_pending else None),
        "history_error": state["history_error"],
    }


@app.get("/api/drafts")
async def drafts():
    if not await connect_meko():
        raise HTTPException(503, "Meko is unavailable. Check the server configuration and retry.")
    try:
        async with lock:
            items = await state["meko"].list_learnings()
    except Exception as err:
        raise HTTPException(502, f"Meko drafts unavailable ({type(err).__name__}).") from err
    return [{
        "id": item["id"], "text": item["text"], "learned_by": item.get("learned_by"),
        "learned_at": item.get("learned_at"), "created_by": item.get("created_by"),
    } for item in items if item.get("text")]


@app.get("/api/memory/lifecycle")
async def memory_lifecycle():
    if not await connect_meko():
        raise HTTPException(503, "Meko is unavailable. Check the server configuration and retry.")
    try:
        async with lock:
            drafts_list = [item for item in await state["meko"].list_learnings()
                           if str(item.get("text", "")).lstrip().startswith("[DRAFT")]
            approved_list = await state["meko"].list_promoted()
    except Exception as err:
        raise HTTPException(502, f"Meko lifecycle data unavailable ({type(err).__name__}).") from err
    return {
        "drafts": [{
            "id": item.get("id"), "text": item.get("text", ""),
            "learned_by": item.get("learned_by"), "learned_at": item.get("learned_at"),
            "created_by": item.get("created_by"),
        } for item in drafts_list],
        "approved": approved_list,
    }


@app.post("/api/memory/search")
async def search_memory(body: MemorySearchIn):
    query = body.query.strip()
    if not query:
        raise HTTPException(400, "Enter a search term.")
    if not await connect_meko():
        raise HTTPException(503, "Meko is unavailable. Check the server configuration and retry.")
    agent = next(iter(state["agents"].values()), None)
    if agent is None:
        raise HTTPException(503, "No support agent is available.")
    try:
        async with lock:
            result = await state["meko"].call("context_search", {
                "conversation_id": agent.conv,
                "datapack_id": state["meko"].dp,
                "query": query,
            })
    except Exception as err:
        raise HTTPException(502, f"Meko search failed: {type(err).__name__}: {err}") from err
    return {
        "match_quality": result.get("match_quality", "unknown"),
        "results": result.get("results", {}),
    }


@app.post("/api/approve")
async def approve(body: ApproveIn):
    if not await connect_meko():
        raise HTTPException(503, "Meko is unavailable. Check the server configuration and retry.")
    try:
        async with lock:
            done = await state["meko"].approve_draft(body.id, body.text)
    except Exception as err:
        raise HTTPException(500, f"{type(err).__name__}: {err}")
    return {
        "entry": done["entry"],
        "llm_usage": usage_summary(done["llm_usage"]) if done.get("llm_usage") else None,
        "usage_tracked": done.get("usage_tracked", False),
    }


@app.get("/api/selflearning")
async def selflearning():
    if not await connect_meko():
        raise HTTPException(503, "Meko is unavailable. Check the server configuration and retry.")
    try:
        async with lock:
            result = await state["meko"].list_selflearning()
    except Exception as err:
        raise HTTPException(502, f"Meko self-learning data unavailable ({type(err).__name__}).") from err
    return sanitize_learning_data(result)


@app.post("/api/selflearning/verify")
async def verify_selflearning(body: VerifyIn):
    if not await connect_meko():
        raise HTTPException(503, "Meko is unavailable. Check the server configuration and retry.")
    try:
        async with lock:
            result = await state["meko"].verify_fix(body.log_id)
    except Exception as err:
        raise HTTPException(502, f"Meko verification failed ({type(err).__name__}).") from err
    return sanitize_learning_data(result)


@app.get("/api/metrics")
def metrics():
    return state["history"]


@app.get("/api/benchmark/results")
def benchmark_results():
    state["benchmark_error"] = None
    try:
        with open(BENCHMARK_RESULTS_FILE, encoding="utf-8") as results_file:
            result = json.load(results_file)
    except FileNotFoundError:
        return {"results": [], "summary": None, "available": False, "error": None}
    except (OSError, json.JSONDecodeError) as err:
        state["benchmark_error"] = f"Could not read benchmark results ({type(err).__name__})."
        return {"results": [], "summary": None, "available": False, "error": state["benchmark_error"]}
    if not isinstance(result, dict):
        state["benchmark_error"] = "Benchmark results must contain one JSON object."
        return {"results": [], "summary": None, "available": False, "error": state["benchmark_error"]}
    return {"results": [result], "summary": result, "available": True, "error": None}


@app.get("/api/benchmark/fixtures")
def benchmark_fixtures():
    try:
        with open(BENCHMARK_FIXTURE_FILE, encoding="utf-8") as fixture_file:
            rows = json.load(fixture_file)
        with open(DEMO_STORY_FIXTURE_FILE, encoding="utf-8") as story_file:
            rows.extend(json.load(story_file))
    except (OSError, json.JSONDecodeError) as err:
        raise HTTPException(500, f"Ticket fixtures unavailable ({type(err).__name__}).") from err
    return rows


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
