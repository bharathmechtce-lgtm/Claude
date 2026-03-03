#!/usr/bin/env python3
"""
Full test: ALL orders across 25 scenarios — Haiku only.
Paces API calls to stay within rate limits.

v2: Fixes SAP truth filtering, message routing, deduplication.
"""

import json
import os
import re
import sys
import time
import requests
from datetime import datetime
from difflib import SequenceMatcher
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# Shared modules
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, REPO_ROOT)
from src.core.prompts import build_system_prompt as _build_shared_prompt, EXTRACTION_PROMPT
from testing.eval.scorer_utils import (
    extract_json, score_order, filter_testable_items,
    item_mentioned_in_messages, fuzzy_match, auto_respond,
)

# Load .env
env_path = os.path.join(PROJECT_ROOT, ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not API_KEY:
    print("ERROR: No ANTHROPIC_API_KEY found")
    sys.exit(1)

MODEL_ID = "claude-haiku-4-5-20251001"
INPUT_COST = 0.80   # per 1M tokens
OUTPUT_COST = 4.0    # per 1M tokens

with open(os.path.join(SCRIPT_DIR, "test_scenarios.json")) as f:
    ALL_SCENARIOS = json.load(f)["scenarios"]

with open(os.path.join(SCRIPT_DIR, "product_catalog.json")) as f:
    CATALOG = json.load(f)

CATALOG_BY_CODE = {p["item_code"]: p for p in CATALOG}

# Words to skip when matching item names to messages (units, sizes, packaging)
SIZE_WORDS = {
    "1kg", "2kg", "3kg", "5kg", "10kg", "20kg", "500gms", "100gms", "140gms",
    "750gms", "650g", "400gms", "768gms", "875gms", "960gm", "800gm", "623g",
    "1ltr", "2ltr", "750ml", "330ml", "300ml", "500ml", "100ml", "4000ml",
    "pcs", "gms", "bag", "box", "nos", "pkt", "btl", "can", "jar",
    "pouch", "tin", "the", "and", "for", "with", "inch",
}


# Common company/org words to skip when extracting location keywords
_COMMON_ORG_WORDS = {
    "good", "food", "concept", "hospitality", "services", "limited",
    "entertainment", "private", "ltd", "llp", "pvt", "the", "and", "for",
    "bellona", "prasuk", "jain", "worldwide", "liberty", "monarch", "snow",
    "world", "innercircle", "hotel", "bar", "cafe", "restaurant",
}


def _ship_to_keywords(ship_to_name):
    """Extract distinctive location keywords from a ship-to name."""
    words = set()
    for w in re.findall(r'[a-zA-Z]{3,}', ship_to_name.lower()):
        if w not in _COMMON_ORG_WORDS:
            words.add(w)
    return words


def _msg_match_score(msg, ship_words):
    """Score how well a message matches a set of ship-to keywords."""
    loc = msg.get("location", "").lower()
    msg_text = msg.get("text", "").lower()
    combined = loc + " " + msg_text
    combined_words = set(re.findall(r'[a-zA-Z]{3,}', combined))

    score = 0
    for w in ship_words:
        if len(w) < 4:
            continue
        if w in combined_words:
            score += len(w)  # longer words = higher score
        else:
            # Fuzzy match
            for tw in combined_words:
                if len(tw) >= 4 and SequenceMatcher(None, w, tw).ratio() > 0.7:
                    score += len(w) * 0.7
                    break
    return score


# ── Improved message routing for multi-location scenarios ──
def find_relevant_messages(scenario, target_ship_to):
    """Find messages relevant to a specific ship-to address.

    Uses exclusive routing: each message goes to its BEST matching ship-to,
    preventing one ship-to from grabbing all messages via a shared word.
    """
    order_msgs = [m for m in scenario["original_group_messages"]
                  if m["type"] in ("order", "order_addition")]

    if not order_msgs:
        return []

    # Single ship-to in SAP: all messages are relevant
    ship_tos_in_sap = set(i["ship_to_code"] for i in scenario["sap_truth"])
    if len(ship_tos_in_sap) == 1:
        return order_msgs

    # Build keyword sets for ALL ship-tos
    all_ship_keywords = {}
    for st in ship_tos_in_sap:
        all_ship_keywords[st] = _ship_to_keywords(st)

    # Detect "shared geography" words — words that appear in most/all messages
    # These are useless for routing (e.g., "parel" when all restaurants are in Parel)
    all_msg_texts = [m.get("text", "").lower() + " " + m.get("location", "").lower()
                     for m in order_msgs]
    all_kw = set()
    for kw_set in all_ship_keywords.values():
        all_kw.update(kw_set)
    shared_words = set()
    for kw in all_kw:
        if len(kw) < 4:
            continue
        msg_count = sum(1 for t in all_msg_texts if kw in t)
        if msg_count >= len(order_msgs) * 0.7 and len(order_msgs) >= 3:
            shared_words.add(kw)

    # Remove shared words from all keyword sets
    if shared_words:
        for st in all_ship_keywords:
            all_ship_keywords[st] = all_ship_keywords[st] - shared_words

    target_words = all_ship_keywords.get(target_ship_to, set())

    # EXCLUSIVE ROUTING: For each message, find which ship-to it BEST matches.
    # Only include messages where target_ship_to is the best (or tied-best) match.
    relevant = []
    relevant_groups = set()

    for m in order_msgs:
        # Score this message against ALL ship-tos
        scores = {}
        for st, kw in all_ship_keywords.items():
            scores[st] = _msg_match_score(m, kw)

        my_score = scores.get(target_ship_to, 0)
        best_score = max(scores.values()) if scores else 0

        if my_score <= 0:
            continue

        # Include only if this ship-to is the best match (or tied for best)
        if my_score >= best_score:
            relevant.append(m)
            if m.get("order_group"):
                relevant_groups.add(m["order_group"])

    # Also include additions from the same order_group
    if relevant_groups:
        for m in order_msgs:
            if m not in relevant and m.get("order_group") in relevant_groups:
                relevant.append(m)

    # Fallback when exclusive routing found nothing for this ship-to
    if not relevant:
        # Case 1: Shared geography removed all distinctive keywords for this ship-to
        # but OTHER ship-tos still have keywords → this ship-to is indistinguishable
        # from all messages → skip (e.g., S10 L.PAREL where "parel" is in every msg)
        if shared_words and not target_words:
            other_have_kw = any(
                kw_set for st, kw_set in all_ship_keywords.items()
                if st != target_ship_to and kw_set
            )
            if other_have_kw:
                return []  # Skip — this ship-to lost all keywords to shared geography

        # Case 2: Send only UNASSIGNED messages (those that don't clearly belong
        # to another ship-to). This prevents cross-contamination while still
        # capturing orders that don't mention a location explicitly.
        unassigned = []
        for m in order_msgs:
            scores = {}
            for st, kw in all_ship_keywords.items():
                scores[st] = _msg_match_score(m, kw)
            best = max(scores.values()) if scores else 0
            if best <= 0:
                unassigned.append(m)  # No ship-to claimed this message

        if unassigned:
            relevant = unassigned
        else:
            # All messages are assigned to other ship-tos → send all as last resort
            relevant = order_msgs

    return relevant


# ── Discover all orders ──
def discover_all_orders():
    orders = []
    for sc in ALL_SCENARIOS:
        ship_tos = set()
        for item in sc["sap_truth"]:
            ship_tos.add(item["ship_to_code"])
        for st in sorted(ship_tos):
            items = [i for i in sc["sap_truth"] if i["ship_to_code"] == st]
            orders.append({
                "scenario_id": sc["scenario_id"],
                "difficulty": sc["difficulty"],
                "chat_name": sc["chat_name"],
                "ship_to": st,
                "item_count": len(items),
                "item_codes": [i["item_code"] for i in items],
            })
    return orders


# ── System prompt ──
def build_system_prompt(scenario, target_ship_to):
    """Build the system prompt using the shared prompt builder."""
    return _build_shared_prompt(
        customer_context={
            "card_codes": scenario["card_codes"],
            "card_names": scenario["card_names"],
            "ship_to_addresses": scenario["ship_to_addresses"],
        },
        product_catalog=scenario.get("historical_patterns", []),
        catalog_by_code=CATALOG_BY_CODE,
    )


# ── API ──
def call_api(messages, system_prompt):
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": MODEL_ID,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": messages,
                },
                timeout=120,
            )
            data = resp.json()
            if resp.status_code == 429:
                wait = min(2 ** (attempt + 1), 30)
                print(f"      Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            if resp.status_code != 200:
                err = data.get("error", {}).get("message", str(data))
                return f"[API ERROR: {err}]", 0, 0
            text = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]
            return text, data["usage"]["input_tokens"], data["usage"]["output_tokens"]
        except requests.exceptions.Timeout:
            if attempt < 2:
                print(f"      Timeout, retrying ({attempt+1}/3)...")
                time.sleep(3)
                continue
            return "[TIMEOUT]", 0, 0
        except Exception as e:
            return f"[ERROR: {e}]", 0, 0
    return "[FAILED after retries]", 0, 0


def run_one(order_info):
    sid = order_info["scenario_id"]
    ship_to = order_info["ship_to"]
    scenario = next(s for s in ALL_SCENARIOS if s["scenario_id"] == sid)

    # Get relevant messages for this ship-to
    relevant_msgs = find_relevant_messages(scenario, ship_to)

    # Build the order text from relevant messages
    order_text = "\n".join(m["text"] for m in relevant_msgs)
    all_msg_text = order_text

    # Get SAP target items for this ship-to
    raw_target_items = [i for i in scenario["sap_truth"] if i["ship_to_code"] == ship_to]
    if not raw_target_items:
        return None

    # Filter to only testable items (those mentioned in messages)
    target_items = filter_testable_items(raw_target_items, all_msg_text)

    if not target_items:
        # No testable items — skip this order
        return None

    system_prompt = build_system_prompt(scenario, ship_to)
    card_names = ", ".join(scenario["card_names"])

    first_message = f"Hi, this is {card_names}.\n\n{order_text}"
    ship_tos_in_sap = set(i["ship_to_code"] for i in scenario["sap_truth"])
    if len(ship_tos_in_sap) > 1:
        first_message += f"\n\nDelivery to: {ship_to}"

    messages = []
    total_in = total_out = turns = corrections_sent = 0
    conv_log = []

    def send(user_msg):
        nonlocal total_in, total_out, turns
        messages.append({"role": "user", "content": user_msg})
        conv_log.append({"role": "customer", "text": user_msg[:300]})
        # Pace: wait between API calls to avoid rate limits
        time.sleep(2)
        resp, tok_in, tok_out = call_api(messages, system_prompt)
        total_in += tok_in
        total_out += tok_out
        turns += 1
        messages.append({"role": "assistant", "content": resp})
        conv_log.append({"role": "bot", "text": resp[:300]})
        return resp

    # Turn 1: Send order
    bot_resp = send(first_message)

    # Natural conversation loop — respond to what the bot asks (Fix 4)
    for turn in range(6):
        response_text, should_extract = auto_respond(bot_resp, ship_to, turn)
        bot_resp = send(response_text)
        if should_extract:
            break

    # Turn 3: Extract JSON
    extraction_resp = send(EXTRACTION_PROMPT)
    llm_json = extract_json(extraction_resp)

    if not llm_json:
        extraction_resp = send("Please output the order as valid JSON only. No other text.")
        llm_json = extract_json(extraction_resp)

    # Score
    first_score = score_order(llm_json, target_items, ship_to)

    # Correction round if needed
    final_score = first_score
    if not first_score["complete_order"] and llm_json:
        corrections = []
        for d in first_score["details"]:
            if d["status"] == "QTY_MISMATCH":
                corrections.append(
                    f"For {d['sap_item'][:40]}: I need {d['sap_qty']:.0f} PCS, not {d['llm_qty']:.0f}")
            elif d["status"] == "MISSED":
                corrections.append(
                    f"You're missing: {d['sap_item'][:40]} — I need {d['sap_qty']:.0f} PCS")
            elif d["status"] == "EXTRA":
                corrections.append(
                    f"Remove {d['llm_item'][:40]} — I didn't order that")

        if corrections:
            corrections_sent = 1
            send("Please correct:\n" + "\n".join(corrections))
            re_resp = send(EXTRACTION_PROMPT)
            llm_json_2 = extract_json(re_resp)
            if llm_json_2:
                final_score = score_order(llm_json_2, target_items, ship_to)
                llm_json = llm_json_2

    cost = (total_in / 1_000_000) * INPUT_COST + (total_out / 1_000_000) * OUTPUT_COST

    return {
        "scenario_id": f"S{sid:02d}",
        "difficulty": order_info["difficulty"],
        "chat_name": order_info["chat_name"],
        "ship_to": ship_to,
        "target_count": final_score["target_count"],
        "raw_sap_count": len(raw_target_items),
        "filtered_out": len(raw_target_items) - len(target_items),
        "llm_count": final_score["llm_count"],
        "matched": final_score["matched"],
        "qty_correct": final_score["qty_correct"],
        "extra": final_score["extra"],
        "missed": final_score["missed"],
        "ship_to_correct": final_score["ship_to_correct"],
        "complete_order": final_score["complete_order"],
        "turns": turns,
        "corrections_sent": corrections_sent,
        "tokens_in": total_in,
        "tokens_out": total_out,
        "cost": cost,
        "details": final_score["details"],
        "conversation": conv_log,
        "llm_json": llm_json,
    }


def main():
    all_orders = discover_all_orders()

    print("=" * 70)
    print(f"  FULL TEST v2: {len(all_orders)} orders × Haiku")
    print(f"  {MODEL_ID}")
    print(f"  Fixes: SAP filtering, message routing, dedup")
    print("=" * 70)

    results = []
    skipped = 0
    for i, order in enumerate(all_orders):
        sid = order["scenario_id"]
        ship_to = order["ship_to"]
        diff = order["difficulty"]

        print(f"\n  [{i+1}/{len(all_orders)}] S{sid:02d} ({diff}) | {ship_to[:50]} | {order['item_count']} raw SAP items")

        try:
            result = run_one(order)
            if result is None:
                print(f"    SKIP: No testable items in messages")
                skipped += 1
                continue
            results.append(result)

            status = "PASS" if result["complete_order"] else "FAIL"
            filtered_note = ""
            if result["filtered_out"] > 0:
                filtered_note = f" (filtered {result['filtered_out']} untestable)"
            print(f"    {status} | Match: {result['matched']}/{result['target_count']}{filtered_note} "
                  f"| Qty: {result['qty_correct']}/{result['matched']} "
                  f"| Extra: {result['extra']} | ${result['cost']:.4f}")

            # Print details for failures
            if not result["complete_order"]:
                for d in result["details"]:
                    if d["status"] != "MATCH":
                        label = d.get("llm_item", d.get("sap_item", "?"))[:45]
                        print(f"      {d['status']}: {label}")

        except Exception as e:
            print(f"    ERROR: {str(e)[:80]}")
            import traceback
            traceback.print_exc()
            results.append({
                "scenario_id": f"S{sid:02d}", "difficulty": diff,
                "chat_name": order["chat_name"], "ship_to": ship_to,
                "complete_order": False, "error": str(e),
                "target_count": order["item_count"], "raw_sap_count": order["item_count"],
                "filtered_out": 0, "llm_count": 0,
                "matched": 0, "qty_correct": 0, "extra": 0,
                "missed": order["item_count"], "ship_to_correct": False,
                "turns": 0, "corrections_sent": 0,
                "tokens_in": 0, "tokens_out": 0, "cost": 0,
                "details": [], "conversation": [], "llm_json": None,
            })

    # Save raw
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(SCRIPT_DIR, f"full_haiku_results_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print(f"  RESULTS SUMMARY v2 — {MODEL_ID}")
    print(f"{'=' * 70}")

    n = len(results)
    passed = sum(1 for r in results if r["complete_order"])
    total_target = sum(r["target_count"] for r in results)
    total_matched = sum(r["matched"] for r in results)
    total_qty = sum(r["qty_correct"] for r in results)
    total_extra = sum(r["extra"] for r in results)
    total_missed = sum(r["missed"] for r in results)
    total_llm = sum(r["llm_count"] for r in results)
    total_cost = sum(r["cost"] for r in results)
    total_filtered = sum(r.get("filtered_out", 0) for r in results)

    print(f"\n  Orders tested:    {n} (skipped {skipped} with no testable items)")
    print(f"  Complete PASS:    {passed}/{n} ({passed*100//n if n else 0}%)")
    print(f"  Product recall:   {total_matched}/{total_target} ({total_matched*100//total_target if total_target else 0}%)")
    print(f"  Qty accuracy:     {total_qty}/{total_matched} ({total_qty*100//total_matched if total_matched else 0}%)")
    print(f"  Extra (halluc):   {total_extra}")
    print(f"  Missed:           {total_missed}")
    print(f"  SAP items filtered: {total_filtered} (not in messages)")
    print(f"  Total cost:       ${total_cost:.4f}")

    # By difficulty
    for diff in ["EASY", "MEDIUM", "HARD"]:
        dr = [r for r in results if r["difficulty"] == diff]
        if not dr:
            continue
        dp = sum(1 for r in dr if r["complete_order"])
        dn = len(dr)
        dm = sum(r["matched"] for r in dr)
        dt = sum(r["target_count"] for r in dr)
        dq = sum(r["qty_correct"] for r in dr)
        de = sum(r["extra"] for r in dr)
        print(f"\n  {diff}:  {dp}/{dn} PASS  |  recall {dm}/{dt}  |  qty {dq}/{dm if dm else 1}  |  extra {de}")

    print(f"\n  Results: {out_path}")


if __name__ == "__main__":
    main()
