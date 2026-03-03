#!/usr/bin/env python3
"""
Retest ONLY the previously failed Sonnet scenarios with the improved prompts.
Runs the 7 failed (scenario, ship_to) pairs from the last test.
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

MODEL_ID = os.environ.get("RETEST_MODEL", "claude-haiku-4-5-20251001")

# Load data
with open(os.path.join(SCRIPT_DIR, "test_scenarios.json")) as f:
    ALL_SCENARIOS = json.load(f)["scenarios"]

with open(os.path.join(SCRIPT_DIR, "product_catalog.json")) as f:
    CATALOG = json.load(f)

CATALOG_BY_CODE = {p["item_code"]: p for p in CATALOG}

# The 7 failed (scenario_id, ship_to) pairs from previous Sonnet run
FAILED_ORDERS = [
    (5,  "URBAN GOURMET INDIA PVT LTD (27 BAKE HOUSE)"),
    (10, "BELLONA HOSPITALITY SERVICES LTD-L.PAREL"),
    (13, "OBEROI TOWER (NARIMAN POINT)"),
    (18, "PJH PALLADIUM"),
    (22, "CREMURE"),
    (24, "KOA CAFÉ & BAR (VASHI)"),
    (24, "THE GAME PALACIO"),
]


def build_system_prompt(scenario, target_ship_to):
    card_codes = ", ".join(scenario["card_codes"])
    card_names = ", ".join(scenario["card_names"])
    ship_addresses = scenario["ship_to_addresses"]

    prompt = f"""You are a WhatsApp order assistant for TJUK, a food distribution company in Mumbai.
You are chatting 1-on-1 with a customer via WhatsApp. Be helpful, concise, and natural.

LANGUAGE RULES:
- Customers may write in English, Hindi, Marathi, Gujarati, or Hinglish (mixed Hindi-English).
  Understand ALL of these languages.
- Reply in the SAME language the customer uses. If they write in Hindi, reply in Hindi.
  If they mix Hindi and English, reply in Hinglish. Default to English if unclear.
- NEVER reply in Arabic or any non-Indian language. This is a Mumbai-based business —
  the languages are English, Hindi, Marathi, Gujarati, and Hinglish only.

CUSTOMER CONTEXT:
  Customer: {card_codes} — {card_names}
  Ship-to Addresses:
"""
    for addr in ship_addresses:
        prompt += f"    - {addr}\n"

    prompt += """
YOUR BEHAVIOR:
1. When the customer sends an order, acknowledge it naturally ("Got it!" / "Noted!" etc.)
2. Read the items and quantities they mention — confirm what you understood
3. If location/outlet is missing, ask for it
4. If a product name is ambiguous, ask for clarification
5. Handle "add" messages by merging into the current order
6. Handle "cancel" / "remove" messages by updating the order
7. Keep a RUNNING ORDER in your head — after each interaction, you know the full order state
8. Be conversational but efficient — these are busy restaurant/hotel managers

ANTI-HALLUCINATION RULES (CRITICAL — follow these strictly):
- ONLY include items the customer EXPLICITLY mentioned or asked for
- NEVER infer, suggest, or add items the customer did not ask for
- NEVER add items "they might also need" or "usually ordered together"
- If the customer says "5kg amul butter" — that is ONE item (amul butter). Do NOT add cheese, ghee, or anything else
- When extracting the order to JSON, list ONLY the items from the conversation. Zero extras
- If in doubt whether the customer asked for something, DO NOT include it — ask instead
- Count your output items against the customer's message. If you have MORE items than the customer mentioned, you are hallucinating — remove the extras

QUANTITY CONVERSION RULES (customers speak in cases/kg, SAP records in PCS):
  CASE/BOX: "X case" → quantity = X × PackSize (from catalogue)
  KG: "X kg" → quantity = X ÷ UnitWeight (from catalogue)
  DIRECT (no conversion — just count as PCS):
    "X pcs/btl/pkt/nos/block/bulk/tin/bag" → quantity = X PCS
    Example: "24 block" = 24 PCS. Do NOT multiply blocks by pack_size or unit_weight.
    Example: "12 btl" = 12 PCS. Do NOT multiply bottles by anything.
  IMPORTANT: "block" means individual units (e.g. ice cream blocks). 1 block = 1 PCS always.

QUANTITY SANITY CHECK:
- After converting, compare the result against the historical order patterns below
- If the converted quantity is more than 3x or less than 0.3x the customer's median for that item, flag it
- For first-time items (no history), accept the quantity as-is

PRODUCT MATCHING RULES:
- Match customer text to the PRODUCT CATALOGUE below using item_code and item_name
- Do NOT invent item codes — only use codes from the catalogue
- If a customer's text could match multiple items, pick the closest name match
- If match confidence is low, ASK for clarification rather than guessing
- If you cannot find a match, say so — do NOT fabricate a product or code
- GENERIC TERMS: When a customer uses a generic term WITHOUT specifying a brand, do NOT
  default to one specific brand. Instead, ask which product they want by listing the
  matching options from the catalogue. Use context to narrow down sensibly:
  - "water bottle" / "pani" → list water/sparkling water brands (NOT sauce bottles)
  - "soda" → list soda brands only
  - "juice" → list juice brands only
  - "bottle" alone → use surrounding context (if ordering drinks, show drink bottles;
    if ordering sauces, show sauce bottles). If still ambiguous, ask.

PRODUCT CATALOGUE (items this customer typically orders):
"""
    for h in scenario["historical_patterns"][:30]:
        cat = CATALOG_BY_CODE.get(h["item_code"], {})
        pack = cat.get("pack_size", 1)
        weight = cat.get("unit_weight_kg", 0)
        median = h.get("median_qty", "N/A")
        prompt += (f"  {h['item_code']} | {h['item_name'][:50]} | "
                   f"PackSize={pack} | UnitWeight={weight}kg | "
                   f"ordered {h['order_count']}x | typical_qty={median}\n")

    prompt += """
ORDER CONFIRMATION:
- When the customer seems done, show a COMPLETE ORDER SUMMARY
- Format: numbered list with item name, quantity, and delivery location
- Ask: "Please confirm this order, or let me know if any changes are needed"

Respond naturally as a WhatsApp assistant. Keep responses SHORT (2-4 lines max).
Do NOT output JSON unless specifically asked.
"""
    return prompt


EXTRACTION_PROMPT = """Please output the COMPLETE order as structured JSON.
Include ALL items from the entire conversation (additions included, cancellations removed).

CRITICAL: ONLY include items the customer EXPLICITLY ordered. Do NOT add any items
that were not mentioned by the customer. Count the items in your output — they must
match the number of distinct products the customer asked for. If you have MORE items
than the customer mentioned, you are hallucinating — remove the extras.

Output ONLY this JSON:
{
  "orders": [
    {
      "ship_to": "EXACT ADDRESS NAME",
      "lines": [
        {
          "item_code": "ITEM_CODE_FROM_CATALOGUE",
          "item_name": "MATCHED_CATALOGUE_NAME",
          "quantity": 72,
          "uom": "PCS",
          "original_text": "what customer wrote",
          "conversion_applied": "3 case × 24 pcs/case = 72 PCS"
        }
      ]
    }
  ]
}

Rules:
- ALL quantities MUST be in PCS after conversion
- Match products to the catalogue using item codes — do NOT invent codes
- Include conversion notes showing your math
- If you can't match a product, use item_code "UNKNOWN"
- NEVER include items the customer did not ask for
"""


def call_anthropic(messages, system_prompt):
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
    if resp.status_code != 200:
        err = data.get("error", {}).get("message", str(data))
        return f"[API ERROR: {err}]", 0, 0
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]
    return text, data["usage"]["input_tokens"], data["usage"]["output_tokens"]


def extract_json(text):
    if not text:
        return None
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    start = text.find('{')
    if start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        pass
                    break
    return None


def fuzzy(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def score_order(llm_json, target_items, target_ship_to):
    result = {
        "target_count": len(target_items),
        "llm_count": 0, "matched": 0, "qty_correct": 0,
        "extra": 0, "missed": 0, "ship_to_correct": False,
        "complete_order": False, "details": [],
    }
    if not llm_json or "orders" not in llm_json:
        result["missed"] = len(target_items)
        return result

    llm_lines = []
    llm_ship_tos = []
    for order in llm_json.get("orders", []):
        ship = order.get("ship_to", "")
        llm_ship_tos.append(ship)
        for line in order.get("lines", []):
            llm_lines.append({
                "item_code": line.get("item_code", "UNKNOWN"),
                "item_name": line.get("item_name", ""),
                "quantity": line.get("quantity", 0),
                "ship_to": ship,
            })
    result["llm_count"] = len(llm_lines)

    for st in llm_ship_tos:
        if fuzzy(st, target_ship_to) >= 0.5:
            result["ship_to_correct"] = True
            break

    all_pairs = []
    for li, ll in enumerate(llm_lines):
        for ti, tgt in enumerate(target_items):
            if ll["item_code"] and ll["item_code"] != "UNKNOWN" and \
               ll["item_code"] == tgt["item_code"]:
                score = 1.0
            else:
                score = max(
                    fuzzy(ll.get("item_name", ""), tgt["description"]),
                    fuzzy(ll.get("item_code", ""), tgt["item_code"]),
                )
            all_pairs.append((score, li, ti))

    all_pairs.sort(key=lambda x: -x[0])
    matched_target = set()
    matched_llm = set()

    for score, li, ti in all_pairs:
        if li in matched_llm or ti in matched_target:
            continue
        if score < 0.4:
            continue
        matched_target.add(ti)
        matched_llm.add(li)
        ll = llm_lines[li]
        tgt = target_items[ti]
        try:
            llm_qty = float(ll["quantity"])
        except (ValueError, TypeError):
            llm_qty = 0
        sap_qty = float(tgt["quantity"])
        qty_ok = abs(llm_qty - sap_qty) / sap_qty <= 0.05 if sap_qty > 0 else abs(llm_qty - sap_qty) < 0.01
        if qty_ok:
            result["qty_correct"] += 1
        result["details"].append({
            "status": "MATCH" if qty_ok else "QTY_MISMATCH",
            "llm_item": ll["item_name"], "llm_code": ll["item_code"],
            "llm_qty": llm_qty, "sap_item": tgt["description"],
            "sap_code": tgt["item_code"], "sap_qty": sap_qty,
        })

    result["matched"] = len(matched_target)
    for li, ll in enumerate(llm_lines):
        if li not in matched_llm:
            result["extra"] += 1
            result["details"].append({
                "status": "EXTRA", "llm_item": ll["item_name"],
                "llm_code": ll["item_code"], "llm_qty": ll.get("quantity", 0),
            })
    for ti, tgt in enumerate(target_items):
        if ti not in matched_target:
            result["missed"] += 1
            result["details"].append({
                "status": "MISSED", "sap_item": tgt["description"],
                "sap_code": tgt["item_code"], "sap_qty": tgt["quantity"],
            })

    result["complete_order"] = (
        result["matched"] == result["target_count"]
        and result["qty_correct"] == result["matched"]
        and result["extra"] == 0
        and result["ship_to_correct"]
    )
    return result


def get_benchmark_codes(scenario_id, ship_to):
    """Get the benchmark item codes from the previous results."""
    with open(os.path.join(SCRIPT_DIR, "conv_test_raw_rescored.json")) as f:
        prev = json.load(f)
    for r in prev:
        sid_num = int(r["scenario_id"][1:])
        if sid_num == scenario_id and r["ship_to"] == ship_to and r["model"] == "Sonnet":
            # Extract target item codes from details
            codes = []
            for d in r.get("details", []):
                if "sap_code" in d and d["sap_code"] != "-":
                    codes.append(d["sap_code"])
            return codes
    return []


def run_one(scenario_id, ship_to):
    scenario = next(s for s in ALL_SCENARIOS if s["scenario_id"] == scenario_id)

    # Get benchmark codes from previous test
    benchmark_codes = set(get_benchmark_codes(scenario_id, ship_to))

    # Target SAP items
    target_items = [
        i for i in scenario["sap_truth"]
        if i["ship_to_code"] == ship_to and (not benchmark_codes or i["item_code"] in benchmark_codes)
    ]
    if not target_items:
        target_items = [i for i in scenario["sap_truth"] if i["ship_to_code"] == ship_to]

    system_prompt = build_system_prompt(scenario, ship_to)
    card_names = ", ".join(scenario["card_names"])

    # Get order messages
    order_msgs = [m for m in scenario["original_group_messages"]
                  if m["type"] in ("order", "order_addition")]

    relevant_msgs = []
    for m in order_msgs:
        loc = m.get("location", "").lower()
        ship_lower = ship_to.lower()
        if len(scenario["ship_to_addresses"]) == 1:
            relevant_msgs.append(m)
        elif loc and any(w in ship_lower for w in loc.split() if len(w) > 3):
            relevant_msgs.append(m)
        elif not loc and not any(m2.get("location") for m2 in order_msgs):
            relevant_msgs.append(m)
    if not relevant_msgs:
        relevant_msgs = order_msgs[:2]

    order_text = "\n".join(m["text"] for m in relevant_msgs)
    first_message = f"Hi, this is {card_names}.\n\n{order_text}"
    if len(scenario["ship_to_addresses"]) > 1:
        first_message += f"\n\nDelivery to: {ship_to}"

    messages = []
    total_in = total_out = turns = 0
    log = []

    def send(user_msg):
        nonlocal total_in, total_out, turns
        messages.append({"role": "user", "content": user_msg})
        log.append({"role": "customer", "text": user_msg[:200]})
        resp, tok_in, tok_out = call_anthropic(messages, system_prompt)
        total_in += tok_in
        total_out += tok_out
        turns += 1
        messages.append({"role": "assistant", "content": resp})
        log.append({"role": "bot", "text": resp[:200]})
        return resp

    # Turn 1: Send order
    print(f"    Turn 1: Sending order...")
    bot_resp = send(first_message)
    print(f"    Bot: {bot_resp[:120]}...")

    # Turn 2: Answer questions
    if "?" in bot_resp:
        answer = f"Ship to: {ship_to}"
        print(f"    Turn 2: Answering question...")
        bot_resp = send(answer)
        print(f"    Bot: {bot_resp[:120]}...")

    # Turn 2.5: Request summary
    print(f"    Turn 2.5: Requesting summary...")
    summary = send("That's it. Please show me the complete order summary for confirmation.")
    print(f"    Summary: {summary[:150]}...")

    # Turn 2.6: Confirm
    send("Confirmed. Looks good.")

    # Turn 3: Extract JSON
    print(f"    Turn 3: Extracting JSON...")
    extraction_resp = send(EXTRACTION_PROMPT)
    llm_json = extract_json(extraction_resp)

    if not llm_json:
        print(f"    WARNING: JSON parse failed, retrying...")
        extraction_resp = send("Please output the order as valid JSON only. No other text.")
        llm_json = extract_json(extraction_resp)

    # Score first attempt
    first_score = score_order(llm_json, target_items, ship_to)

    # Correction if needed
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
            print(f"    Turn 4: Sending corrections...")
            send("Please correct:\n" + "\n".join(corrections))
            print(f"    Turn 5: Re-extracting...")
            re_resp = send(EXTRACTION_PROMPT)
            llm_json_2 = extract_json(re_resp)
            if llm_json_2:
                final_score = score_order(llm_json_2, target_items, ship_to)
                llm_json = llm_json_2

    return {
        "scenario_id": f"S{scenario_id:02d}",
        "ship_to": ship_to,
        "target_count": final_score["target_count"],
        "llm_count": final_score["llm_count"],
        "matched": final_score["matched"],
        "qty_correct": final_score["qty_correct"],
        "extra": final_score["extra"],
        "missed": final_score["missed"],
        "ship_to_correct": final_score["ship_to_correct"],
        "complete_order": final_score["complete_order"],
        "turns": turns,
        "details": final_score["details"],
        "conversation": log,
        "llm_json": llm_json,
    }


def main():
    print("=" * 70)
    print(f"  RETEST: 7 previously failed scenarios — Model: {MODEL_ID}")
    print("  Using improved anti-hallucination prompts")
    print("=" * 70)

    results = []
    for i, (sid, ship_to) in enumerate(FAILED_ORDERS):
        print(f"\n  [{i+1}/7] S{sid:02d} | {ship_to}")
        try:
            result = run_one(sid, ship_to)
            results.append(result)
            status = "PASS" if result["complete_order"] else "FAIL"
            print(f"    => {status} | Products: {result['matched']}/{result['target_count']} | "
                  f"Qty: {result['qty_correct']}/{result['matched']} | "
                  f"Extra: {result['extra']} | Turns: {result['turns']}")
            if not result["complete_order"]:
                for d in result["details"]:
                    print(f"       {d['status']}: {d.get('llm_item', d.get('sap_item', '?'))[:50]}")
        except Exception as e:
            print(f"    ERROR: {e}")
            results.append({
                "scenario_id": f"S{sid:02d}", "ship_to": ship_to,
                "complete_order": False, "error": str(e),
                "target_count": 0, "matched": 0, "qty_correct": 0,
                "extra": 0, "missed": 0, "turns": 0,
            })
        time.sleep(1)

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(SCRIPT_DIR, f"retest_results_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  RETEST SUMMARY")
    print(f"{'=' * 70}")
    passed = sum(1 for r in results if r["complete_order"])
    total = len(results)
    total_extra = sum(r["extra"] for r in results)
    total_matched = sum(r["matched"] for r in results)
    total_target = sum(r["target_count"] for r in results)
    total_qty = sum(r["qty_correct"] for r in results)

    print(f"  Complete Orders: {passed}/{total} ({passed*100//total if total else 0}%)")
    print(f"  Product Recall:  {total_matched}/{total_target}")
    print(f"  Qty Correct:     {total_qty}/{total_matched}")
    print(f"  Hallucinated:    {total_extra} extra items")
    print(f"\n  Previous: 0/7 PASS (all failed)")
    print(f"  Now:      {passed}/7 PASS")
    print(f"\n  Results saved: {out_path}")


if __name__ == "__main__":
    main()
