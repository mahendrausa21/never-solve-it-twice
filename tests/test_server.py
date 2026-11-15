import asyncio
import json

import server


def test_triage_uses_keywords_without_model_usage():
    result = asyncio.run(server.triage_ticket("Urgent: please refund my order; charged twice."))

    assert result == {
        "category": "Returns & refunds",
        "intent": "Urgent: please refund my order; charged twice.",
        "priority": "urgent",
        "seconds": 0.0,
        "usage": None,
        "status": "completed",
        "error": None,
    }


def test_triage_defaults_to_other_for_unmatched_ticket():
    result = asyncio.run(server.triage_ticket("The sky is blue."))

    assert result["category"] == "Other"
    assert result["priority"] == "normal"


def test_history_is_persisted_and_reloaded(tmp_path, monkeypatch):
    history_file = tmp_path / "history.jsonl"
    monkeypatch.setattr(server, "HISTORY_FILE", str(history_file))
    row = {"ticket_id": "TKT-TEST", "status": "verified", "ticket": "A fictional issue"}

    assert asyncio.run(server.persist_history(row))
    server.load_history()

    assert server.state["history"] == [row]
    assert server.state["history_error"] is None


def test_history_reports_corrupt_records(tmp_path, monkeypatch):
    history_file = tmp_path / "history.jsonl"
    history_file.write_text('{"ticket_id":"TKT-OK"}\nnot-json\n', encoding="utf-8")
    monkeypatch.setattr(server, "HISTORY_FILE", str(history_file))

    server.load_history()

    assert [row["ticket_id"] for row in server.state["history"]] == ["TKT-OK"]
    assert server.state["history_error"] == "Invalid history record at line 2."


def test_selflearning_payload_redacts_queries_and_emails():
    result = server.sanitize_learning_data({
        "status": {"quality": "high"},
        "query_log": [{"query_text": "private text", "email_address": "person@example.test", "band": "high"}],
    })

    assert result == {"status": {"quality": "high"}, "query_log": [{"band": "high"}]}


def test_meko_connection_failure_is_nonfatal(monkeypatch):
    class BrokenMeko:
        async def connect(self):
            raise RuntimeError("no connection")

    old_meko, old_agents, old_error = (
        server.state["meko"], server.state["agents"], server.state["meko_error"])
    server.state["meko"] = None
    server.state["agents"] = {}
    monkeypatch.setattr(server, "Meko", BrokenMeko)
    try:
        connected = asyncio.run(server.connect_meko())
        assert connected is False
        assert server.state["meko"] is None
        assert server.state["meko_error"] == "RuntimeError"
    finally:
        server.state["meko"] = old_meko
        server.state["agents"] = old_agents
        server.state["meko_error"] = old_error


def test_health_reports_meko_reconnect_pending():
    class ReconnectingMeko:
        needs_reconnect = True

    old_meko, old_error = server.state["meko"], server.state["meko_error"]
    server.state["meko"] = ReconnectingMeko()
    server.state["meko_error"] = None
    try:
        result = server.health()
        assert result["meko"] == "disconnected"
        assert result["meko_error"] == "ReconnectPending"
    finally:
        server.state["meko"] = old_meko
        server.state["meko_error"] = old_error


def test_health_reports_llm_errors_and_stored_ticket_error_rate(monkeypatch):
    monkeypatch.setattr(server.agents, "llm_error_count", 3)
    monkeypatch.setitem(server.state, "history", [
        {"status": "verified"},
        {"status": "error"},
        {"status": "new_draft_saved"},
        {"status": "error"},
    ])

    result = server.health()

    assert result["llm_error_count"] == 3
    assert result["ticket_error_rate"] == 0.5


def test_health_ticket_error_rate_is_null_without_history(monkeypatch):
    monkeypatch.setitem(server.state, "history", [])

    assert server.health()["ticket_error_rate"] is None


def test_unavailable_meko_creates_a_persisted_error_record(monkeypatch, tmp_path):
    async def unavailable():
        return False

    old_history, old_error = server.state["history"], server.state["meko_error"]
    server.state["history"] = []
    server.state["meko_error"] = "TimeoutError"
    monkeypatch.setattr(server, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(server, "connect_meko", unavailable)
    try:
        try:
            asyncio.run(server.process_ticket(server.TicketIn(ticket="A fictional issue")))
        except server.HTTPException as err:
            assert err.status_code == 503
        else:
            raise AssertionError("Expected the unavailable Meko response to fail.")
        record = server.state["history"][0]
        assert record["status"] == "error"
        assert record["error_type"] == "TimeoutError"
        assert "Meko connection" in record["trace"][0]["step"]
        assert (tmp_path / "history.jsonl").exists()
    finally:
        server.state["history"] = old_history
        server.state["meko_error"] = old_error


def test_ticket_tracks_all_measured_gemini_calls(monkeypatch, tmp_path):
    class FakeMeko:
        needs_reconnect = False

        async def create_conversation(self, title):
            assert title.startswith("SupportIQ TKT-")
            return "conversation-id"

        async def record_ticket(self, conversation_id, ticket, reply, status, trace, ticket_id, agent):
            assert conversation_id == "conversation-id"
            assert ticket == "A sample ticket"
            assert reply == "A cautious response."
            assert status == "verified"
            assert ticket_id.startswith("TKT-")
            assert agent == "agent1-solver"
            assert trace
            return {"id": "message-id"}

        async def record_token_usage(self, conversation_id, name, usage, message_id=None):
            assert conversation_id == "conversation-id"
            assert message_id == "message-id"
            return usage["usage_known"]

    class FakeAgent:
        name = "agent1-solver"

        async def handle(self, ticket, on_event, conversation_id, include_baseline):
            assert ticket == "A sample ticket"
            assert conversation_id == "conversation-id"
            assert include_baseline is False
            return {
                "agent": self.name, "ticket": ticket, "status": "verified",
                "needs_review": False, "meko_match": "high", "retrieved_memories": [],
                "reply": "A cautious response.", "memory_id": None,
                "meko_search_seconds": 0.1, "ai_seconds": 0.2, "total_seconds": 0.3,
                "prompt_tokens": 30, "baseline_prompt_tokens": 0,
                "llm_calls": [{
                    "name": "Support reply", "prompt_tokens": 30, "completion_tokens": 5,
                    "total_tokens": 35, "model": "test-model", "usage_known": True,
                    "seconds": 0.2,
                }],
                "trace": [{"t": 0.1, "step": "Searched Meko", "detail": "One approved hit."}],
            }

    monkeypatch.setattr(server, "HISTORY_FILE", str(tmp_path / "history.jsonl"))
    monkeypatch.setattr(server, "connect_meko", lambda: asyncio.sleep(0, result=True))
    monkeypatch.setattr(server, "triage_ticket", lambda _ticket: asyncio.sleep(0, result={
        "category": "Other", "intent": "sample", "priority": "normal",
        "seconds": 0.1, "status": "completed", "error": None,
        "usage": {
            "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12,
            "model": "test-model", "usage_known": True, "seconds": 0.1,
        },
    }))
    old_history, old_meko, old_agents = (
        server.state["history"], server.state["meko"], server.state["agents"])
    server.state["history"] = []
    server.state["meko"] = FakeMeko()
    server.state["agents"] = {"agent1-solver": FakeAgent()}
    try:
        result = asyncio.run(server.process_ticket(server.TicketIn(ticket="A sample ticket")))
    finally:
        server.state["history"] = old_history
        server.state["meko"] = old_meko
        server.state["agents"] = old_agents

    assert result["token_usage"]["prompt_tokens"] == 40
    assert result["token_usage"]["completion_tokens"] == 7
    assert result["token_usage"]["total_tokens"] == 47
    assert result["token_usage"]["measured_calls"] == 2
    assert result["token_usage"]["tracked_calls"] == 2
    assert result["meko_trace_recorded"] is True
    assert json.loads((tmp_path / "history.jsonl").read_text(encoding="utf-8"))["ticket_id"] == result["ticket_id"]
