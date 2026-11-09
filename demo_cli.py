"""demo_cli.py - the full story in the terminal (the web dashboard comes next).

  1. Agent 1: known problem          -> answered from the knowledge base (verified)
  2. Agent 1: NEW problem            -> solves it, saves an unapproved DRAFT
  3. Agent 2: same problem           -> reuses the draft, flagged for human review
  4. A human APPROVES the draft      -> promoted into shared knowledge
  5. Agent 2: same problem again     -> now answered from APPROVED knowledge (verified)

Run:
    set RESET=1        (optional: forget old drafts first)
    python demo_cli.py
"""
import asyncio
import json
import os

from agents import Meko, SupportAgent


# Each scenario is a NEW problem nobody has approved yet.
# (Approving is permanent in Meko, so use a different scenario for each fresh run.)
SCENARIOS = {
    "wallet": {
        "keyword": "wallet",
        "first": "My in-app wallet balance disappeared after I updated the app.",
        "reuse": "The money in my app wallet is missing since the latest update.",
        "after_approval": "My wallet shows zero balance after the app update.",
    },
    "points": {  # already approved in an earlier test run
        "keyword": "points",
        "first": "Loyalty points were not added after my purchase.",
        "reuse": "I bought something yesterday but my reward points balance did not go up.",
        "after_approval": "I made an order but no reward points were credited to my account.",
    },
    "gift": {  # already approved in earlier test runs
        "keyword": "gift card",
        "first": "The app crashes whenever I try to apply a gift card at checkout.",
        "reuse": "Checkout keeps crashing on the gift card screen in your app.",
        "after_approval": "My app crashes at checkout when I apply a gift card.",
    },
}
SC = SCENARIOS[os.environ.get("SCENARIO", "wallet")]


def show(r):
    print(f"\n>>> {r['agent']}: {r['ticket']}")
    for step in r["trace"]:
        print(f"    [{step['t']:>5}s] {step['step']}: {step['detail']}")
    flag = "  *** NEEDS HUMAN REVIEW ***" if r["needs_review"] else ""
    print(f"    Status: {r['status']}{flag}")
    print(f"    Reply: {r['reply'][:350]}")


async def main():
    meko = await Meko().connect()
    try:
        if os.environ.get("RESET") == "1":
            await meko.forget_all_drafts()
            print("RESET: cleared learned drafts.")
            await asyncio.sleep(3)

        a1 = await SupportAgent("agent1-solver", meko).start()
        a2 = await SupportAgent("agent2-helper", meko).start()
        results = []

        for agent, ticket in [
            (a1, "My refund hasn't arrived and it has been 9 days."),
            (a1, SC["first"]),
            (a2, SC["reuse"]),
        ]:
            r = await agent.handle(ticket)
            results.append(r)
            show(r)

        print("\n" + "=" * 70)
        print("HUMAN REVIEW: drafts waiting for approval")
        print("=" * 70)
        learnings = await meko.list_learnings()
        for i, item in enumerate(learnings):
            print(f"  [{i}] {item['text'][:110]}...")
        if not learnings or not any(x["text"] for x in learnings):
            print("  (could not read the drafts - send me the text below)")
            print(json.dumps(meko.last_raw_listing, indent=2)[:1500])

        gift = [x for x in learnings if SC["keyword"] in x["text"].lower()]
        if gift:
            print("\nHuman approves the draft...")
            done = await meko.approve_draft(gift[0]["id"], gift[0]["text"])
            print("Clean approved entry:\n" + done["entry"])
            print("Promote result:", json.dumps(done["promote"])[:200])
            print("Waiting for Meko to index the approved entry...")
            waited = 0
            for _ in range(8):
                await asyncio.sleep(10)
                waited += 10
                chk_conv = await meko.call("conversation_create", {"title": "index check"})
                chk = await meko.call("knowledgebase_search", {
                    "conversation_id": chk_conv["id"], "datapack_id": meko.dp,
                    "query": SC["after_approval"]})
                hits = chk.get("results", []) if isinstance(chk, dict) else []
                if any("Approved fixes" in (h.get("chunk_text") or "") for h in hits):
                    print(f"Approved entry is searchable after about {waited}s")
                    break
            else:
                print("Approved entry still not searchable after 80s")

        r = await a2.handle(SC["after_approval"])
        results.append(r)
        show(r)

        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        for r in results:
            base = r["baseline_prompt_tokens"]
            extra = f" (vs {base} if whole KB pasted in)" if base else ""
            print(f"{r['agent']:14} | {r['status']:16} | prompt tokens {r['prompt_tokens']}{extra}")

        with open("results.json", "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)
    finally:
        await meko.close()


asyncio.run(main())
