import asyncio
from types import SimpleNamespace

import pytest

import agents


def _usage(text):
    return {"text": text, "prompt_tokens": 40, "completion_tokens": 20, "total_tokens": 60,
            "model": "test-model", "usage_known": True, "seconds": 0.5}


def test_approved_knowledge_is_rewritten_by_the_model(monkeypatch):
    class FakeMeko:
        dp = "test-datapack"

        async def call(self, tool, _args):
            assert tool == "context_search"
            return {
                "results": {
                    "knowledge_base": [{
                        "chunk_text": "Section: KB > Title\n\nProblem: x. Fix: restart the app.",
                        "rerank_score": 0.8,
                    }],
                    "memory": [],
                },
                "match_quality": "high",
            }

    prompts = []
    monkeypatch.setattr(
        agents, "ask_llm",
        lambda prompt: prompts.append(prompt) or _usage("Please restart the app."))

    result = asyncio.run(agents.SupportAgent("test-agent", FakeMeko()).handle(
        "A repeated ticket", include_baseline=False))

    assert result["status"] == "verified"
    assert result["reply"] == "Please restart the app."
    assert "restart the app" in prompts[0]
    assert result["prompt_tokens"] == 40
    assert [c["name"] for c in result["llm_calls"]] == ["Support reply"]


def test_reworded_ticket_is_matched_to_a_draft_by_the_model(monkeypatch):
    draft = ("[DRAFT - unverified, written by agent1-solver] Support problem: I was charged "
             "twice for the same order.\nSuggested fix: Check the bank statement for pending "
             "charges.")

    class FakeMeko:
        dp = "test-datapack"

        async def call(self, tool, _args):
            if tool == "context_search":
                return {"results": {"knowledge_base": [], "memory": [{"memory": draft}]},
                        "match_quality": "mid"}
            if tool == "knowledgebase_search":
                return {"results": []}
            raise AssertionError("unexpected Meko tool: " + tool)

    answers = iter(["YES", "A colleague suggests checking pending charges. A specialist will confirm."])
    monkeypatch.setattr(agents, "ask_llm", lambda prompt: _usage(next(answers)))

    result = asyncio.run(agents.SupportAgent("test-agent", FakeMeko()).handle(
        "My payment went through two times for one purchase", include_baseline=False))

    assert result["status"] == "draft_reused"
    assert result["needs_review"] is True
    assert [c["name"] for c in result["llm_calls"]] == ["Draft match check", "Support reply"]


def test_ask_llm_retries_transient_errors_with_exponential_backoff(monkeypatch):
    class TemporaryError(Exception):
        code = 503

    class FakeModels:
        def __init__(self):
            self.calls = 0

        def generate_content(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise TemporaryError("temporary failure")
            return SimpleNamespace(
                text=" answer ",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=1,
                    candidates_token_count=1,
                    total_token_count=2,
                ),
            )

    models = FakeModels()
    sleeps = []
    monkeypatch.setattr(agents, "llm", SimpleNamespace(models=models))
    monkeypatch.setattr(agents, "GEMINI_FALLBACK_MODEL", None)
    monkeypatch.setattr(agents.time, "sleep", sleeps.append)
    timestamps = iter((10.0, 12.0))
    monkeypatch.setattr(agents.time, "time", lambda: next(timestamps))

    result = agents.ask_llm("test")

    assert models.calls == 2
    assert sleeps == [1]
    assert result["text"] == "answer"
    assert result["model"] == agents.GEMINI_MODEL
    assert result["seconds"] == 2.0


def test_ask_llm_uses_optional_fallback_after_primary_retries(monkeypatch):
    class TemporaryError(Exception):
        status_code = 429

    class FakeModels:
        def __init__(self):
            self.models = []

        def generate_content(self, model, **_kwargs):
            self.models.append(model)
            if model == "primary-model":
                raise TemporaryError("rate limited")
            return SimpleNamespace(
                text="fallback answer",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=1,
                    candidates_token_count=1,
                    total_token_count=2,
                ),
            )

    client = SimpleNamespace(models=FakeModels())
    monkeypatch.setattr(agents, "llm", client)
    monkeypatch.setattr(agents, "GEMINI_MODEL", "primary-model")
    monkeypatch.setattr(agents, "GEMINI_FALLBACK_MODEL", "fallback-model")
    monkeypatch.setattr(agents.time, "sleep", lambda _seconds: None)

    result = agents.ask_llm("test")

    assert client.models.models == ["primary-model"] * 3 + ["fallback-model"]
    assert result["model"] == "fallback-model"
    assert result["text"] == "fallback answer"


def test_ask_llm_does_not_retry_non_transient_errors_and_counts_failure(monkeypatch):
    class InvalidRequest(Exception):
        code = 400

    class FakeModels:
        calls = 0

        def generate_content(self, **_kwargs):
            self.calls += 1
            raise InvalidRequest("invalid request")

    models = FakeModels()
    monkeypatch.setattr(agents, "llm", SimpleNamespace(models=models))
    monkeypatch.setattr(agents, "GEMINI_FALLBACK_MODEL", None)
    monkeypatch.setattr(agents, "llm_error_count", 0)

    with pytest.raises(InvalidRequest):
        agents.ask_llm("test")

    assert models.calls == 1
    assert agents.llm_error_count == 1


def test_model_match_check_is_skipped_when_meko_similarity_is_low(monkeypatch):
    draft = ("[DRAFT - unverified, written by agent1-solver] Support problem: Printer is offline."
             "\nSuggested fix: Restart the printer.")

    class FakeMeko:
        dp = "test-datapack"

        async def call(self, tool, _args):
            if tool == "context_search":
                return {"results": {"knowledge_base": [],
                                    "memory": [{"memory": draft,
                                                "raw_similarity_score": 0.2}]},
                        "match_quality": "low"}
            if tool == "knowledgebase_search":
                return {"results": []}
            if tool == "memory_add":
                return {"results": [{"id": "m2"}]}
            return {}

    calls = []
    monkeypatch.setattr(agents, "ask_llm",
                        lambda prompt: calls.append(prompt) or _usage("A cautious new answer."))
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(agents.asyncio, "sleep", no_sleep)

    result = asyncio.run(agents.SupportAgent("test-agent", FakeMeko()).handle(
        "The app asks for my phone number again", include_baseline=False))

    assert result["status"] == "new_draft_saved"
    assert [c["name"] for c in result["llm_calls"]] == ["Support reply"]
    assert len(calls) == 1


def test_reused_draft_id_is_recorded_for_pairing(monkeypatch):
    draft = ("[DRAFT - unverified, written by agent1-solver] Support problem: Printer is offline."
             "\nSuggested fix: Restart the printer.")

    class FakeMeko:
        dp = "test-datapack"

        async def call(self, tool, _args):
            if tool == "context_search":
                return {"results": {"knowledge_base": [],
                                    "memory": [{"id": "m1", "memory": draft,
                                                "raw_similarity_score": 0.8}]},
                        "match_quality": "mid"}
            if tool == "knowledgebase_search":
                return {"results": []}
            return {}

    monkeypatch.setattr(agents, "ask_llm", lambda prompt: _usage("YES"))
    result = asyncio.run(agents.SupportAgent("test-agent", FakeMeko()).handle(
        "My printer is offline again", include_baseline=False))

    assert result["status"] == "draft_reused"
    assert {"id": "m1", "source": "draft"}.items() <= result["retrieved_memories"][0].items()


def test_quick_same_ignores_filler_words_and_needs_three_shared_words():
    old = ("[DRAFT - unverified, written by agent2-helper] Support problem: I was charged twice "
           "for the same order.\nSuggested fix: Check the statement.")
    assert not agents.quick_same(
        "My order arrived in two separate boxes and I was charged delivery for both.", old)
    assert agents.quick_same(
        "Customer charged twice for the order and saw the duplicate order charge twice", old)
