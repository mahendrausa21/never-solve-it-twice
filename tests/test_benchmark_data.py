from scripts.generate_benchmark_data import build_benchmark_tickets, build_knowledge


def test_knowledge_fixture_has_one_hundred_unique_records():
    entries = build_knowledge()

    assert len(entries) == 100
    assert len({entry["id"] for entry in entries}) == 100
    assert len({entry["problem"] for entry in entries}) == 100
    assert all(entry["verified_fix"] and entry["escalate_to"] for entry in entries)


def test_benchmark_fixture_has_twenty_complete_lifecycle_flows():
    tickets = build_benchmark_tickets()
    expected = {
        "seed": "new_draft_saved",
        "reuse": "draft_reused",
        "verified": "verified",
    }

    assert len(tickets) == 60
    assert len({ticket["case_id"] for ticket in tickets}) == 60
    groups = {}
    for ticket in tickets:
        groups.setdefault(ticket["group_id"], []).append(ticket)
    assert len(groups) == 20
    for group in groups.values():
        assert [ticket["stage"] for ticket in group] == list(expected)
        assert [ticket["expected_status"] for ticket in group] == list(expected.values())
