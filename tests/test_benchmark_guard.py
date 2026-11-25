import sys

import pytest

from scripts import run_benchmark


def test_benchmark_refuses_a_datapack_that_already_has_knowledge(monkeypatch, capsys):
    def fake_request(_base_url, path, _payload=None):
        if path == "/api/health":
            return {"datapack_name": "supportiq-benchmark-x", "meko": "connected", "model": "m"}
        if path == "/api/memory/lifecycle":
            return {"approved": [{"id": "a1"}], "drafts": []}
        raise AssertionError("no ticket may be sent to a non-empty datapack: " + path)

    monkeypatch.setattr(run_benchmark, "request_json", fake_request)
    monkeypatch.setattr(sys, "argv", ["run_benchmark", "--confirm-datapack", "supportiq-benchmark-x"])

    with pytest.raises(SystemExit):
        run_benchmark.main()

    assert "not empty" in capsys.readouterr().err
