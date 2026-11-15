"""Run the 60-case lifecycle benchmark on a fresh, isolated Meko datapack."""

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data" / "benchmark_tickets.json"
KNOWLEDGE_FIXTURE = ROOT / "data" / "support_knowledge_base_100.json"
RESULT_FILE = ROOT / "data" / "benchmark_results.json"


class BenchmarkError(RuntimeError):
    pass


def request_json(base_url, path, body=None, timeout=180):
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, headers=headers,
        method="GET" if body is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        try:
            details = json.loads(err.read().decode("utf-8"))
            message = details.get("detail", "HTTP request failed")
        except (json.JSONDecodeError, UnicodeDecodeError):
            message = "HTTP request failed"
        raise BenchmarkError(f"{path} returned HTTP {err.code}: {message}") from err
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
        raise BenchmarkError(f"{path} failed ({type(err).__name__}).") from err


def run_case(base_url, case):
    started = time.monotonic()
    result = request_json(
        base_url, "/api/ticket",
        {"ticket": case["ticket"], "measure_baseline": True},
    )
    return {
        "ticket_id": result.get("ticket_id"),
        "actual_status": result.get("status", "unknown"),
        "total_seconds": result.get("total_seconds"),
        "token_usage": result.get("token_usage"),
        "llm_calls": result.get("llm_calls") or [],
        "baseline_reply_usage": result.get("baseline_usage"),
        "baseline_error": result.get("baseline_error"),
        "retrieved_memories": result.get("retrieved_memories") or [],
        "memory_id": result.get("memory_id"),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def usage_total(call):
    if not isinstance(call, dict) or call.get("usage_known") is not True:
        return None
    value = call.get("total_tokens")
    return value if isinstance(value, int) and value >= 0 else None


def complete_call_total(calls):
    if not calls:
        return None
    totals = [usage_total(call) for call in calls]
    return sum(totals) if all(value is not None for value in totals) else None


def token_metrics(rows, metric):
    values = [
        row[metric] for row in rows
        if isinstance(row.get(metric), int) and row[metric] >= 0
    ]
    return {
        "median_per_ticket": statistics.median(values) if values else None,
        "total_tokens": sum(values) if values else None,
        "measured_tickets": len(values),
    }


def rate(rows, stage, predicate):
    sample = [row for row in rows if row.get("stage") == stage]
    hits = sum(bool(predicate(row)) for row in sample)
    return {
        "hits": hits,
        "sample_size": len(sample),
        "rate": hits / len(sample) if sample else None,
    }


def make_summary(run_id, started_at, completed_at, model, datapack_name, planned_count, rows, kb_size):
    outcomes = Counter(row.get("actual_status", "unknown") for row in rows)
    latencies = [
        float(row["total_seconds"]) for row in rows
        if isinstance(row.get("total_seconds"), (int, float))
    ]
    passed = sum(row.get("passed") is True for row in rows)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "datapack_name": datapack_name,
        "started_at": started_at,
        "completed_at": completed_at,
        "model": model,
        "sample_size": planned_count,
        "completed_cases": len(rows),
        "passed_cases": passed,
        "knowledge_base": {
            "entries": kb_size,
            "source": "data/support_knowledge_base_100.json",
        },
        "token_metrics": {
            "meko": token_metrics(rows, "meko_total_tokens"),
            "baseline": token_metrics(rows, "baseline_total_tokens"),
            "baseline_method": (
                "Measured full-knowledge-base Gemini reply; common triage, "
                "same-problem checks, and approval rewrites are included."
            ),
        },
        "latency": {
            "median_seconds": statistics.median(latencies) if latencies else None,
            "total_seconds": sum(latencies) if latencies else None,
            "measured_tickets": len(latencies),
        },
        "retrieval_hit_rate": rate(
            rows, "verified",
            lambda row: row.get("actual_status") == "verified" and bool(row.get("retrieved_memories")),
        ),
        "reuse_rate": rate(
            rows, "reuse", lambda row: row.get("actual_status") == "draft_reused",
        ),
        "false_reuse_rate": rate(
            rows, "seed", lambda row: row.get("actual_status") == "draft_reused",
        ),
        "outcome_counts": dict(outcomes),
        "cases": rows,
    }


def row_from_result(run_id, case, actual, created_at, approval_usage):
    calls = actual["llm_calls"]
    approval_call = None
    if case["stage"] == "verified":
        approval_call = (
            {"name": "Knowledge base entry rewrite", **approval_usage}
            if isinstance(approval_usage, dict)
            else None
        )
    meko_calls = list(calls)
    if case["stage"] == "verified":
        meko_calls.append(approval_call)
    meko_total = complete_call_total(meko_calls)

    common_calls = [
        call for call in calls
        if isinstance(call, dict) and call.get("name") != "Support reply"
    ]
    baseline_calls = [*common_calls, actual.get("baseline_reply_usage")]
    if case["stage"] == "verified":
        baseline_calls.append(approval_call)
    baseline_total = complete_call_total(baseline_calls)

    return {
        "run_id": run_id,
        "case_id": case["case_id"],
        "group_id": case["group_id"],
        "stage": case["stage"],
        "topic": case["topic"],
        "expected_status": case["expected_status"],
        "actual_status": actual["actual_status"],
        "passed": actual["actual_status"] == case["expected_status"],
        "ticket_id": actual["ticket_id"],
        "total_seconds": actual["total_seconds"],
        "elapsed_seconds": actual["elapsed_seconds"],
        "token_usage": actual["token_usage"],
        "llm_calls": calls,
        "baseline_reply_usage": actual.get("baseline_reply_usage"),
        "baseline_error": actual.get("baseline_error"),
        "approval_rewrite_usage": approval_usage,
        "meko_total_tokens": meko_total,
        "baseline_total_tokens": baseline_total,
        "retrieved_memories": actual["retrieved_memories"],
        "error_type": None,
        "created_at": created_at,
    }


def failed_row(run_id, case, created_at, error):
    return {
        "run_id": run_id,
        "case_id": case["case_id"],
        "group_id": case["group_id"],
        "stage": case["stage"],
        "topic": case["topic"],
        "expected_status": case["expected_status"],
        "actual_status": "error",
        "passed": False,
        "ticket_id": None,
        "total_seconds": None,
        "elapsed_seconds": None,
        "token_usage": None,
        "llm_calls": [],
        "baseline_reply_usage": None,
        "baseline_error": None,
        "approval_rewrite_usage": None,
        "meko_total_tokens": None,
        "baseline_total_tokens": None,
        "retrieved_memories": [],
        "error_type": type(error).__name__,
        "created_at": created_at,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--confirm-datapack", required=True,
        help="Must match the app's isolated demo/benchmark datapack.",
    )
    parser.add_argument("--limit-groups", type=int, default=20, choices=range(1, 21))
    args = parser.parse_args()

    health = request_json(args.base_url, "/api/health")
    datapack_name = health.get("datapack_name")
    if datapack_name != args.confirm_datapack:
        parser.error("The running app's datapack does not match --confirm-datapack.")
    if not isinstance(datapack_name, str) or not any(
        marker in datapack_name.lower() for marker in ("demo", "benchmark")
    ):
        parser.error("Refusing to benchmark a datapack not named as an isolated demo or benchmark pack.")
    if health.get("meko") != "connected":
        parser.error("Meko is not connected; start the app with the isolated datapack configured.")

    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    knowledge = json.loads(KNOWLEDGE_FIXTURE.read_text(encoding="utf-8"))
    grouped = defaultdict(list)
    for case in cases:
        grouped[case["group_id"]].append(case)
    groups = list(grouped.values())[:args.limit_groups]
    planned_cases = [case for group in groups for case in group]
    run_id = f"RUN-{uuid.uuid4().hex[:12].upper()}"
    started_at = datetime.now(timezone.utc).isoformat()
    rows = []

    for group in groups:
        draft = None
        approval_usage = None
        stop_reason = None
        for case in group:
            created_at = datetime.now(timezone.utc).isoformat()
            try:
                if case["stage"] == "verified":
                    if draft is None:
                        raise BenchmarkError(stop_reason or "No draft was available for approval.")
                    approval = request_json(args.base_url, "/api/approve", {
                        "id": draft["id"], "text": draft["text"],
                    })
                    approval_usage = approval.get("llm_usage")

                actual = run_case(args.base_url, case)
                if case["stage"] == "seed":
                    drafts = request_json(args.base_url, "/api/drafts")
                    draft_id = actual["memory_id"]
                    draft = next(
                        (item for item in drafts if draft_id and str(item.get("id")) == str(draft_id)),
                        None,
                    )
                    if draft is None:
                        stop_reason = "The new draft could not be matched by its returned Meko memory id."
                row = row_from_result(run_id, case, actual, created_at, approval_usage)
            except Exception as err:
                stop_reason = f"{case['stage']} step failed ({type(err).__name__})."
                row = failed_row(run_id, case, created_at, err)
            rows.append(row)
            number = len(rows)
            print(f"{number:02}/{len(planned_cases)} {case['case_id']} {case['stage']}: "
                  f"{row['actual_status']} ({'pass' if row['passed'] else 'check'})")
            if stop_reason and case["stage"] == "seed":
                break

    summary = make_summary(
        run_id, started_at, datetime.now(timezone.utc).isoformat(),
        health.get("model"), datapack_name, len(planned_cases), rows, len(knowledge),
    )
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Benchmark run {run_id} wrote one result file: {RESULT_FILE}")
    print(f"Measured token tickets — Meko: {summary['token_metrics']['meko']['measured_tickets']}; "
          f"full-KB baseline: {summary['token_metrics']['baseline']['measured_tickets']}.")


if __name__ == "__main__":
    main()
