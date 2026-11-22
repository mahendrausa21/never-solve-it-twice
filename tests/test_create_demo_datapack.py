import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from scripts import create_demo_datapack as cdp


class FakeSession:
    calls = []

    def __init__(self, *_args):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def initialize(self):
        return None

    async def call_tool(self, name, arguments):
        FakeSession.calls.append((name, arguments))
        payloads = {
            "conversation_create": {"id": "conv-1"},
            "datapack_list": {"datapacks": []},
            "datapack_create": {"datapack_id": "pack-1"},
        }
        text = json.dumps(payloads[name])
        return SimpleNamespace(content=[SimpleNamespace(text=text)], isError=False)


@asynccontextmanager
async def fake_client(*_args, **_kwargs):
    yield (None, None, None)


def test_datapack_list_and_create_receive_a_conversation_id(monkeypatch):
    # Meko answers "conversation_id_required" to datapack_list without one.
    monkeypatch.setenv("MEKO_API_KEY", "test-key")
    monkeypatch.setattr(cdp, "streamablehttp_client", fake_client)
    monkeypatch.setattr(cdp, "ClientSession", FakeSession)
    FakeSession.calls = []

    datapack_id = asyncio.run(cdp.create_datapack("supportiq-benchmark-test", False))

    assert datapack_id == "pack-1"
    by_name = {name: args for name, args in FakeSession.calls}
    assert by_name["datapack_list"]["conversation_id"] == "conv-1"
    assert by_name["datapack_create"]["conversation_id"] == "conv-1"
    assert by_name["datapack_create"]["name"] == "supportiq-benchmark-test"
