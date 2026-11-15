"""Generate deterministic, fictional SupportIQ knowledge and workflow fixtures."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

TOPICS = [
    ("Payments & billing", "Wallet balance missing", [
        "The wallet balance is blank after signing in on a new phone.",
        "A wallet top-up is shown as pending after the payment step.",
        "The wallet balance changed after a completed purchase.",
        "A refund returned to the wallet is not visible in the balance.",
        "The wallet history and displayed balance do not match.",
    ], "Ask the customer to refresh the wallet and compare the latest wallet activity with the displayed balance. Do not promise funds until the transaction status is confirmed.", "Billing team if the wallet ledger and balance still disagree."),
    ("Checkout & discounts", "Gift card not applied", [
        "A gift card is accepted but the order total does not change.",
        "The gift card field disappears before checkout is completed.",
        "A gift card balance remains after the customer used it on an order.",
        "The checkout page rejects a gift card with an available balance.",
        "A gift card is applied to the wrong eligible item in the cart.",
    ], "Confirm the code is entered in the gift-card field and review the eligible items and remaining balance shown at checkout. Do not request the full gift-card code in an unsecured channel.", "Payments team if the displayed balance and checkout total disagree."),
    ("Products & rewards", "Reward points missing", [
        "Reward points are not shown after an order was marked complete.",
        "The points history lists a purchase but the points balance did not change.",
        "Only part of the points for an eligible order appears in the account.",
        "Points from a returned purchase remain in the rewards balance.",
        "A customer cannot see the points estimate before placing an order.",
    ], "Review the order and rewards activity together, including any return or cancellation status. Explain that only the points shown by the account are confirmed.", "Rewards team when the order and rewards ledger remain inconsistent."),
    ("Orders & delivery", "Split shipment tracking", [
        "One parcel arrived but the second package in a split shipment has no update.",
        "Two packages from one order show different delivery dates.",
        "A shipment notification lists fewer packages than the order page.",
        "The second tracking number in an order is not clickable.",
        "A split shipment is marked complete while one parcel is missing.",
    ], "Compare each package and tracking number separately on the order page. Ask the customer to report which package is missing; do not treat one delivered parcel as proof that all parcels arrived.", "Logistics team if a package has no usable tracking or is reported missing."),
    ("App & notifications", "Order alerts not received", [
        "The app no longer sends an alert when an order changes status.",
        "Only delivery alerts are missing while other app notifications arrive.",
        "Order alerts are delayed until the customer opens the app.",
        "A customer receives duplicate alerts for a single order update.",
        "Notifications stopped after the customer changed phones.",
    ], "Check the app's notification preferences and the phone's notification permissions, then reopen the app and review the alert settings. Do not claim a device setting was changed.", "App support if settings are enabled but alerts remain unreliable."),
    ("Account & access", "Two-step code delayed", [
        "A sign-in verification code arrives after it has expired.",
        "The customer selected email verification but received no code.",
        "A second verification code invalidates the first one.",
        "The verification screen returns to sign-in after code entry.",
        "The customer cannot choose the previously available verification method.",
    ], "Ask the customer to request one new code, use only the newest message, and check the delivery channel's spam or blocked folders. Never ask them to share a verification code.", "Account security team if the customer cannot access any verification method."),
    ("Returns & refunds", "Return pickup rescheduling", [
        "The courier missed the scheduled return collection.",
        "A return pickup was scheduled for an address the customer can no longer access.",
        "The return page shows a pickup date that has already passed.",
        "The customer needs to change the return pickup time window.",
        "A second pickup attempt was missed after the first reschedule.",
    ], "Review the return status and available pickup options, then help the customer select a currently available slot. Do not promise a collection time that is not shown.", "Logistics team after repeated missed collections or when no slot is available."),
    ("Checkout & discounts", "Cart emptied during checkout", [
        "The shopping cart is empty after returning from the payment screen.",
        "Items disappear from the cart when the customer changes delivery address.",
        "The cart contents reset after the app is updated.",
        "A saved cart cannot be restored after the customer signs in again.",
        "The checkout page removes an item while loading shipping options.",
    ], "Ask the customer to sign in, reopen the cart, and add back any missing items before retrying checkout. Confirm the final item list and total before payment.", "Checkout support if the cart repeatedly clears during a single session."),
    ("Payments & billing", "Saved card not selectable", [
        "A saved card appears in the account but not at checkout.",
        "The payment page shows an old card that the customer removed.",
        "The customer cannot choose a saved card after changing the billing address.",
        "A saved card is selectable on web but missing in the mobile app.",
        "The checkout page asks for a new card after the customer selected a saved one.",
    ], "Confirm the customer is signed into the same account and review the payment methods available on the current checkout screen. Do not request complete card details.", "Payments team if the saved method remains unavailable in the same account."),
    ("Account & access", "Profile email update", [
        "A customer changed their profile email but the old address still appears.",
        "The email verification link opens a page that says the request is expired.",
        "The customer cannot find the email-change confirmation message.",
        "The profile shows the new email but receipts still go to the previous one.",
        "An email update is pending and the customer cannot submit another request.",
    ], "Check whether the profile marks the new address as verified, and ask the customer to use the latest confirmation message. Do not change account ownership without the approved identity check.", "Account security team if the customer cannot verify ownership of either address."),
    ("Payments & billing", "Invoice address correction", [
        "An invoice shows the customer's previous billing address.",
        "The customer needs a corrected invoice after updating profile details.",
        "The invoice is missing a company name entered before checkout.",
        "A downloaded invoice does not reflect the latest billing address.",
        "The customer cannot edit the billing address on a completed order.",
    ], "Review the order invoice options and the billing details associated with that order. Explain which fields can be corrected; do not alter tax details without verification.", "Finance team if the invoice requires a tax or legal correction."),
    ("Products & rewards", "Product image mismatch", [
        "The product thumbnail differs from the selected color in the order.",
        "The product page shows a different image after choosing a size.",
        "A customer received a product that does not match the listing photos.",
        "The image gallery does not load for one product variant.",
        "The cart thumbnail shows a different version from the product page.",
    ], "Compare the product name, selected variant, and order details. If the delivered item differs from the confirmed order, follow the wrong-item return workflow.", "Catalog team for listing-image errors; returns team for a mismatched delivered item."),
    ("Subscriptions", "Subscription pause request", [
        "A customer wants to pause a subscription without cancelling it.",
        "The pause control is missing from the subscription settings.",
        "A subscription resumed before the customer expected.",
        "The account displays a paused subscription as active.",
        "The customer cannot change the date when a paused subscription resumes.",
    ], "Review the subscription status and the pause options available in the account. Confirm the next scheduled action before making a change.", "Subscriptions team if the displayed status and scheduled action disagree."),
    ("Orders & delivery", "Delivery instruction update", [
        "A customer needs to add a delivery note after placing an order.",
        "The saved delivery instruction did not appear on the latest order.",
        "A customer wants to remove an outdated entry instruction.",
        "The delivery note field is unavailable after the order shipped.",
        "A delivery instruction is visible on the profile but not on the order.",
    ], "Check whether the order is still eligible for instruction changes and distinguish profile defaults from order-specific notes. Do not promise a carrier can honor a late change.", "Logistics team if the order has shipped or the instruction cannot be changed."),
    ("App & notifications", "App language preference", [
        "The app returns to the previous language after sign-in.",
        "Only some screens change after the customer selects another language.",
        "A language preference differs between the mobile app and website.",
        "The language selector is unavailable on the account settings page.",
        "The customer sees mixed-language order notifications.",
    ], "Confirm the language selected in the current app session, then sign out and back in if the preference is not retained. Avoid promising translations that are not available.", "App support if the preference repeatedly resets or content remains mixed."),
    ("Returns & refunds", "Exchange size selection", [
        "The exchange page does not list the size the customer needs.",
        "A selected exchange size disappears before submission.",
        "The customer wants to exchange an item for a different color.",
        "An exchange request is pending but no replacement details appear.",
        "The customer cannot change an exchange selection after submitting it.",
    ], "Review the available exchange options and current request status. If the desired variant is unavailable, explain the return and repurchase options shown by the account.", "Returns team if an exchange is stuck or the selected variant is unavailable."),
    ("Checkout & discounts", "Shipping option unavailable", [
        "No shipping methods appear after the customer enters a delivery address.",
        "A previously available delivery option disappears at checkout.",
        "The checkout page shows pickup only for a delivery address.",
        "A shipping option is listed but cannot be selected.",
        "The delivery estimate changes after the customer selects shipping.",
    ], "Confirm the address is complete and review the options currently shown for the cart. Explain the displayed estimate and do not guarantee an unavailable service.", "Logistics team if no valid shipping option appears for a supported address."),
    ("Products & rewards", "Product restock alert", [
        "A customer cannot enable a restock notification for an unavailable item.",
        "A restock alert arrived after the selected variant sold out again.",
        "The customer receives alerts for the wrong product size.",
        "The restock notification button is missing on one product page.",
        "A customer wants to remove a previously created restock alert.",
    ], "Confirm the selected product variant and the alert preference shown on its page. A notification does not reserve inventory or guarantee availability.", "Catalog team if the alert is tied to the wrong variant or cannot be managed."),
    ("Orders & delivery", "Pickup order not ready", [
        "The customer arrived for pickup but the order is not marked ready.",
        "A pickup-ready message does not include the location details.",
        "The pickup status changed back to processing.",
        "A customer cannot find the pickup code in the order details.",
        "The pickup location says it cannot find the order number.",
    ], "Check the latest pickup status and location shown on the order. Ask the customer to wait for the ready confirmation before collecting the order.", "Logistics team if the location cannot locate an order marked ready."),
    ("Privacy & account", "Account data download", [
        "A customer cannot find the account data download option.",
        "The data export request is shown as pending with no next step.",
        "A downloaded account archive cannot be opened.",
        "The customer needs to submit another data export request.",
        "The export link is no longer available in the account page.",
    ], "Review the privacy settings and current export request status. Do not send account data through an unverified channel or claim a request is complete without a returned status.", "Privacy team if the export is unavailable or the request status is unclear."),
]

BENCHMARK_VARIANTS = [
    ("seed", "I am having this problem: {problem}"),
    ("reuse", "This is happening to me too: {problem}"),
    ("verified", "I followed up because the same issue persists: {problem}"),
]


def build_knowledge():
    rows = []
    for category, title, symptoms, fix, escalation in TOPICS:
        for symptom in symptoms:
            rows.append({
                "id": f"KB-{len(rows) + 1:03d}",
                "category": category,
                "title": title,
                "problem": symptom,
                "verified_fix": fix,
                "escalate_to": escalation,
                "section": "Approved fixes",
            })
    if len(rows) != 100:
        raise ValueError(f"Expected 100 knowledge records, got {len(rows)}.")
    return rows


def build_benchmark_tickets():
    rows = []
    for index, (category, title, symptoms, _, _) in enumerate(TOPICS, start=1):
        problem = symptoms[0].lower()
        for stage, template in BENCHMARK_VARIANTS:
            rows.append({
                "case_id": f"BENCH-{len(rows) + 1:03d}",
                "group_id": f"FLOW-{index:02d}",
                "stage": stage,
                "ticket": template.format(problem=problem),
                "category": category,
                "topic": title,
                "expected_status": {
                    "seed": "new_draft_saved",
                    "reuse": "draft_reused",
                    "verified": "verified",
                }[stage],
            })
    if len(rows) != 60:
        raise ValueError(f"Expected 60 benchmark tickets, got {len(rows)}.")
    return rows


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    write_json(DATA / "support_knowledge_base_100.json", build_knowledge())
    write_json(DATA / "benchmark_tickets.json", build_benchmark_tickets())
    print("Generated 100 fictional knowledge entries and 60 benchmark tickets.")


if __name__ == "__main__":
    main()
