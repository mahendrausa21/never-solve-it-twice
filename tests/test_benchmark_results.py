import json

import server
from scripts.run_benchmark import make_summary, row_from_result


def test_benchmark_result_endpoint_reads_one_json_summary(tmp_path, monkeypatch):
    results_file = tmp_path / "benchmark_results.json"
    summary = {"run_id": "RUN-TEST", "sample_size": 3}
    results_file.write_text(json.dumps(summary), encoding="utf-8")
    monkeypatch.setattr(server, "BENCHMARK_RESULTS_FILE", str(results_file))

    response = server.benchmark_results()

    assert response == {
        "results": [summary],
        "summary": summary,
        "available": True,
        "error": None,
    }


def test_benchmark_result_endpoint_reports_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "BENCHMARK_RESULTS_FILE", str(tmp_path / "missing.json"))

    assert server.benchmark_results() == {
        "results": [],
        "summary": None,
        "available": False,
        "error": None,
    }


def test_benchmark_totals_include_each_measured_call_and_leave_unknowns_empty():
    known = lambda name, total: {
        "name": name, "total_tokens": total, "usage_known": True,
    }
    case = {
        "case_id": "BENCH-003", "group_id": "FLOW-01", "stage": "verified",
        "topic": "Fictional example", "expected_status": "verified",
    }
    actual = {
        "actual_status": "verified", "ticket_id": "TKT-TEST", "total_seconds": 2.5,
        "elapsed_seconds": 2.8, "token_usage": {"total_tokens": 60},
        "llm_calls": [known("Ticket triage", 10), known("Support reply", 30),
                      known("Draft match check", 5)],
        "baseline_reply_usage": known("Full knowledge base reply", 70),
        "baseline_error": None, "retrieved_memories": [{"id": "memory-1"}],
    }

    row = row_from_result("RUN-TEST", case, actual, "2026-01-01T00:00:00Z", known("Knowledge rewrite", 20))
    summary = make_summary(
        "RUN-TEST", "2026-01-01T00:00:00Z", "2026-01-01T00:01:00Z",
        "test-model", "supportiq-benchmark-test", 1, [row], 100,
    )

    assert row["meko_total_tokens"] == 65
    assert row["baseline_total_tokens"] == 105
    assert summary["token_metrics"]["meko"] == {
        "median_per_ticket": 65, "total_tokens": 65, "measured_tickets": 1,
    }
    assert summary["retrieval_hit_rate"] == {"hits": 1, "sample_size": 1, "rate": 1.0}
    assert summary["knowledge_base"]["entries"] == 100

    actual["llm_calls"][0]["usage_known"] = False
    row = row_from_result("RUN-TEST", case, actual, "2026-01-01T00:00:00Z", None)
    assert row["meko_total_tokens"] is None
    assert row["baseline_total_tokens"] is None
