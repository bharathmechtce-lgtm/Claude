#!/usr/bin/env python3
"""
Conversational Bot Test — Active Tester

Tests the WhatsApp Order Bot by acting as a customer who KNOWS the answer.
The tester:
  1. Introduces themselves (customer name)
  2. Sends the order (mimicking real customer style)
  3. Answers bot questions (ship-to, clarifications)
  4. Checks bot's understanding via JSON extraction
  5. Corrects any mistakes the bot made
  6. Re-extracts and scores

Runs 13 FULLY AUTO orders × 3 models = 39 conversations.
Outputs JSON results + Excel summary with KPIs.
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
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── API Keys from environment variables ──
SONNET_KEY = os.environ.get("SONNET_API_KEY", "")
HAIKU_KEY = os.environ.get("HAIKU_API_KEY", "")
FLASH_KEY = os.environ.get("FLASH_API_KEY", "")

MODELS = [
    {
        "name": "Sonnet",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5-20250929",
        "api_key": SONNET_KEY,
        "input_cost_per_mtok": 3.0,
        "output_cost_per_mtok": 15.0,
    },
    {
        "name": "Haiku",
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5-20251001",
        "api_key": HAIKU_KEY,
        "input_cost_per_mtok": 0.80,
        "output_cost_per_mtok": 4.0,
    },
    {
        "name": "Flash",
        "provider": "google",
        "model_id": "gemini-2.0-flash",
        "api_key": FLASH_KEY,
        "input_cost_per_mtok": 0.10,
        "output_cost_per_mtok": 0.40,
    },
]

# ── Load data ──
with open(os.path.join(SCRIPT_DIR, "test_scenarios.json")) as f:
    ALL_SCENARIOS = json.load(f)["scenarios"]

with open(os.path.join(SCRIPT_DIR, "product_catalog.json")) as f:
    CATALOG = json.load(f)

CATALOG_BY_CODE = {p["item_code"]: p for p in CATALOG}


# ── Define the 13 FULLY AUTO orders ──
# Each is (scenario_id, ship_to_code, benchmark_item_codes)
# benchmark_item_codes = SAP items that were matched to customer messages (AUTO)
def identify_auto_orders():
    """Identify the 13 FULLY AUTO orders from the benchmark."""
    from openpyxl import load_workbook
    wb = load_workbook(os.path.join(SCRIPT_DIR, "Conversation_Benchmark.xlsx"))
    ws = wb["Line Items"]

    orders = defaultdict(lambda: {"auto": 0, "hil": 0, "check": 0, "sap_codes": []})
    for row in ws.iter_rows(min_row=2, values_only=True):
        scenario = row[0]
        ship_to = row[10]
        pred = row[17]
        sap_code = row[7]
        key = (scenario, ship_to)
        orders[key][pred.lower()] += 1
        if pred == "AUTO":
            orders[key]["sap_codes"].append(sap_code)

    auto_orders = []
    for key in sorted(orders.keys()):
        scenario, ship_to = key
        o = orders[key]
        if o["hil"] == 0 and o["check"] == 0 and o["auto"] > 0:
            sid = int(scenario[1:])
            auto_orders.append({
                "scenario_id": sid,
                "ship_to": ship_to,
                "benchmark_codes": o["sap_codes"],
            })
    return auto_orders


# ── API callers ──
def call_anthropic_conv(messages, system_prompt, model_id, api_key):
    """Call Anthropic with conversation history."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
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


def call_google_conv(messages, system_prompt, model_id, api_key):
    """Call Google Gemini with conversation history."""
    # Convert messages to Gemini format
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": {"maxOutputTokens": 4096},
        },
        timeout=120,
    )
    if resp.status_code != 200:
        try:
            data = resp.json()
            err = data.get("error", {}).get("message", str(data))
        except (ValueError, json.JSONDecodeError):
            err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        return f"[API ERROR: {err}]", 0, 0
    data = resp.json()
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        text = f"[PARSE ERROR: {str(data)[:200]}]"
    usage = data.get("usageMetadata", {})
    return text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)


def call_model(messages, system_prompt, model_cfg):
    """Dispatch to the right API."""
    if model_cfg["provider"] == "anthropic":
        return call_anthropic_conv(
            messages, system_prompt, model_cfg["model_id"], model_cfg["api_key"])
    elif model_cfg["provider"] == "google":
        return call_google_conv(
            messages, system_prompt, model_cfg["model_id"], model_cfg["api_key"])


# ── System prompt builder ──
def build_system_prompt(scenario, target_ship_to):
    """Build system prompt for the bot."""
    card_codes = ", ".join(scenario["card_codes"])
    card_names = ", ".join(scenario["card_names"])
    ship_addresses = scenario["ship_to_addresses"]

    prompt = f"""You are a WhatsApp order assistant for TJUK, a food distribution company in Mumbai.
You are chatting 1-on-1 with a customer via WhatsApp. Be helpful, concise, and natural.

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
  CASE/BOX: "X case" or "X box" → quantity = X × PackSize (from catalogue)
    Example: "3 box" of Kinley Soda (PackSize=24) → 3 × 24 = 72 PCS
    Example: "1 box" of Amul Butter 500GMS (PackSize=20) → 1 × 20 = 20 PCS
    Example: "1 box" of Dlecta Cream Cheese (PackSize=8) → 1 × 8 = 8 PCS

  KG: "X kg" → quantity = X ÷ UnitWeight (from catalogue)
    Example: "5 kg" of Amul Butter 500GMS (UnitWeight=0.5kg) → 5 ÷ 0.5 = 10 PCS
    Example: "3 kg" of Amul Cheese Block 1KG (UnitWeight=1.0kg) → 3 ÷ 1.0 = 3 PCS

  DIRECT (no conversion — just count as PCS):
    "X pcs/btl/pkt/nos/block/bulk/tin/bag" → quantity = X PCS
    Example: "24 block" = 24 PCS. Do NOT multiply blocks by pack_size or unit_weight.
    Example: "12 btl" = 12 PCS. Do NOT multiply bottles by anything.
    Example: "15 pkt" = 15 PCS.
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


# ── Extraction prompt ──
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


# ── JSON extraction ──
def extract_json(text):
    """Extract JSON from model response."""
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


# ── Scoring ──
def fuzzy(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def score_order(llm_json, target_items, target_ship_to):
    """
    Score LLM output against target SAP items.
    Returns detailed scoring dict.
    """
    result = {
        "target_count": len(target_items),
        "llm_count": 0,
        "matched": 0,
        "qty_correct": 0,
        "extra": 0,
        "missed": 0,
        "ship_to_correct": False,
        "complete_order": False,
        "details": [],
    }

    if not llm_json or "orders" not in llm_json:
        result["missed"] = len(target_items)
        result["details"] = [{"status": "MISSED", "sap": t["description"],
                              "sap_qty": t["quantity"]} for t in target_items]
        return result

    # Flatten LLM lines
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
                "conversion": line.get("conversion_applied", ""),
            })
    result["llm_count"] = len(llm_lines)

    # Check ship-to
    for st in llm_ship_tos:
        if fuzzy(st, target_ship_to) >= 0.5:
            result["ship_to_correct"] = True
            break

    # Bipartite matching: score ALL (llm_line, sap_target) pairs, then
    # greedily assign from highest score to lowest (item_code matches = 1.0)
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

    all_pairs.sort(key=lambda x: -x[0])  # highest score first
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

        qty_ok = False
        if sap_qty > 0:
            qty_ok = abs(llm_qty - sap_qty) / sap_qty <= 0.05
        elif abs(llm_qty - sap_qty) < 0.01:
            qty_ok = True

        if qty_ok:
            result["qty_correct"] += 1

        result["details"].append({
            "status": "MATCH" if qty_ok else "QTY_MISMATCH",
            "llm_item": ll["item_name"],
            "llm_code": ll["item_code"],
            "llm_qty": llm_qty,
            "sap_item": tgt["description"],
            "sap_code": tgt["item_code"],
            "sap_qty": sap_qty,
            "match_score": score,
        })

    result["matched"] = len(matched_target)

    # Extra items (not in target)
    for li, ll in enumerate(llm_lines):
        if li not in matched_llm:
            result["extra"] += 1
            result["details"].append({
                "status": "EXTRA",
                "llm_item": ll["item_name"],
                "llm_code": ll["item_code"],
                "llm_qty": ll.get("quantity", 0),
                "sap_item": "-", "sap_code": "-", "sap_qty": 0,
                "match_score": 0,
            })

    # Missed items
    for ti, tgt in enumerate(target_items):
        if ti not in matched_target:
            result["missed"] += 1
            result["details"].append({
                "status": "MISSED",
                "llm_item": "-", "llm_code": "-", "llm_qty": 0,
                "sap_item": tgt["description"],
                "sap_code": tgt["item_code"],
                "sap_qty": tgt["quantity"],
                "match_score": 0,
            })

    # Complete order accuracy
    result["complete_order"] = (
        result["matched"] == result["target_count"]
        and result["qty_correct"] == result["matched"]
        and result["extra"] == 0
        and result["ship_to_correct"]
    )

    return result


def build_correction_message(score_result, target_items):
    """Build a correction message based on mismatches."""
    corrections = []

    for d in score_result["details"]:
        if d["status"] == "QTY_MISMATCH":
            corrections.append(
                f"For {d['sap_item'][:40]}: I need {d['sap_qty']:.0f} PCS, "
                f"not {d['llm_qty']:.0f}")
        elif d["status"] == "MISSED":
            corrections.append(
                f"You're missing: {d['sap_item'][:40]} — I need {d['sap_qty']:.0f} PCS")

    extras = [d for d in score_result["details"] if d["status"] == "EXTRA"]
    if extras:
        for e in extras:
            corrections.append(
                f"Remove {e['llm_item'][:40]} — I didn't order that")

    if not corrections:
        return None

    msg = "Please correct:\n" + "\n".join(corrections)
    return msg


# ── Main conversation runner ──
def run_conversation(order_info, model_cfg):
    """
    Run one conversation: tester (customer) vs bot (model).
    Returns result dict with all KPIs.
    """
    sid = order_info["scenario_id"]
    ship_to = order_info["ship_to"]
    scenario = next(s for s in ALL_SCENARIOS if s["scenario_id"] == sid)

    # Target SAP items for this ship_to (only the ones in our benchmark)
    benchmark_codes = set(order_info["benchmark_codes"])
    target_items = [
        i for i in scenario["sap_truth"]
        if i["ship_to_code"] == ship_to and i["item_code"] in benchmark_codes
    ]

    if not target_items:
        # Fallback: take all SAP items for this ship_to
        target_items = [i for i in scenario["sap_truth"]
                        if i["ship_to_code"] == ship_to]

    system_prompt = build_system_prompt(scenario, ship_to)
    card_names = ", ".join(scenario["card_names"])

    # Get raw customer messages for this scenario
    order_msgs = [m for m in scenario["original_group_messages"]
                  if m["type"] in ("order", "order_addition")]

    # Build initial customer message: introduce + send order
    # Filter to messages relevant to this ship_to
    relevant_msgs = []
    for m in order_msgs:
        loc = m.get("location", "").lower()
        text_lower = m["text"].lower()
        ship_lower = ship_to.lower()

        # Include if: message mentions this ship_to location, or scenario has
        # only one ship_to, or message has no location (general)
        if len(scenario["ship_to_addresses"]) == 1:
            relevant_msgs.append(m)
        elif loc and any(w in ship_lower for w in loc.split() if len(w) > 3):
            relevant_msgs.append(m)
        elif not loc and not any(
            m2.get("location") for m2 in order_msgs
        ):
            relevant_msgs.append(m)

    if not relevant_msgs:
        relevant_msgs = order_msgs[:2]

    # Compose the first customer message
    order_text = "\n".join(m["text"] for m in relevant_msgs)
    first_message = f"Hi, this is {card_names}.\n\n{order_text}"

    # If ship-to has specific location, add it
    if len(scenario["ship_to_addresses"]) > 1:
        # Derive a natural location mention from ship_to
        first_message += f"\n\nDelivery to: {ship_to}"

    # Track conversation
    messages = []
    total_in = 0
    total_out = 0
    turns = 0
    corrections_sent = 0

    log = []  # conversation log

    def send(user_msg):
        nonlocal total_in, total_out, turns
        messages.append({"role": "user", "content": user_msg})
        log.append({"role": "customer", "text": user_msg})
        resp, tok_in, tok_out = call_model(messages, system_prompt, model_cfg)
        total_in += tok_in
        total_out += tok_out
        turns += 1
        messages.append({"role": "assistant", "content": resp})
        log.append({"role": "bot", "text": resp})
        return resp

    # ── Turn 1: Send order ──
    print(f"    Turn 1: Sending order...")
    bot_resp = send(first_message)
    print(f"    Bot: {bot_resp[:100]}...")

    # ── Turn 2: If bot asks a question, answer it ──
    if "?" in bot_resp:
        # Bot asked something — provide ship-to or clarification
        answer = f"Ship to: {ship_to}"
        print(f"    Turn 2: Answering bot question...")
        bot_resp = send(answer)
        print(f"    Bot: {bot_resp[:100]}...")

    # ── Turn 2.5: Request order summary for confirmation ──
    print(f"    Turn 2.5: Requesting order summary...")
    summary_resp = send("That's it. Please show me the complete order summary for confirmation.")
    print(f"    Bot summary: {summary_resp[:150]}...")

    # ── Turn 2.6: Confirm the order ──
    confirm_resp = send("Confirmed. Looks good.")
    print(f"    Bot confirm: {confirm_resp[:100]}...")

    # ── Turn 3: Extract JSON ──
    print(f"    Turn 3: Requesting JSON extraction...")
    extraction_resp = send(EXTRACTION_PROMPT)

    llm_json = extract_json(extraction_resp)
    if not llm_json:
        print(f"    WARNING: Failed to parse JSON, retrying...")
        extraction_resp = send("Please output the order as valid JSON only. No other text.")
        llm_json = extract_json(extraction_resp)

    # ── Score first attempt ──
    first_score = score_order(llm_json, target_items, ship_to)
    print(f"    First score: {first_score['matched']}/{first_score['target_count']} products, "
          f"{first_score['qty_correct']} qty correct, "
          f"complete={first_score['complete_order']}")

    # ── Turn 4: If not perfect, send corrections ──
    final_score = first_score
    if not first_score["complete_order"] and llm_json:
        correction = build_correction_message(first_score, target_items)
        if correction:
            corrections_sent += 1
            print(f"    Turn 4: Sending corrections...")
            send(correction)

            # ── Turn 5: Re-extract after correction ──
            print(f"    Turn 5: Re-extracting JSON...")
            re_extraction = send(EXTRACTION_PROMPT)
            llm_json_2 = extract_json(re_extraction)
            if llm_json_2:
                final_score = score_order(llm_json_2, target_items, ship_to)
                llm_json = llm_json_2
                print(f"    Final score: {final_score['matched']}/{final_score['target_count']} products, "
                      f"{final_score['qty_correct']} qty correct, "
                      f"complete={final_score['complete_order']}")

    # ── Compute cost ──
    cost = (total_in / 1_000_000) * model_cfg["input_cost_per_mtok"] + \
           (total_out / 1_000_000) * model_cfg["output_cost_per_mtok"]

    return {
        "scenario_id": f"S{sid:02d}",
        "ship_to": ship_to,
        "chat_name": scenario["chat_name"],
        "model": model_cfg["name"],
        "target_count": final_score["target_count"],
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
        "conversation": log,
        "llm_json": llm_json,
        "details": final_score["details"],
    }


# ── Excel output ──
def build_results_excel(all_results, output_path):
    """Build the results Excel with 3 sheets."""
    wb = Workbook()

    hfont = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    hfill = PatternFill(start_color="2F4F4F", end_color="2F4F4F", fill_type="solid")
    wrap = Alignment(wrap_text=True, vertical="top")
    border = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin"))
    green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    yellow = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")

    def style_header(ws, n):
        for c in range(1, n + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = hfont
            cell.fill = hfill
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            cell.border = border

    # ── Sheet 1: Per-Order Detail ──
    ws1 = wb.active
    ws1.title = "Per-Order Detail"
    h1 = ["Scenario", "Ship-to", "Model", "Items Sent", "Items Captured",
          "Product Matches", "Qty Correct", "Corrections by Tester",
          "Extra (Hallucinated)", "Ship-to Correct", "Turns",
          "Corrections Sent", "Tokens In", "Tokens Out", "Cost ($)",
          "Complete Order"]
    ws1.append(h1)
    style_header(ws1, len(h1))

    for r in all_results:
        row = [
            r["scenario_id"], r["ship_to"][:45], r["model"],
            r["target_count"], r["llm_count"], r["matched"],
            r["qty_correct"], r["corrections_sent"], r["extra"],
            "YES" if r["ship_to_correct"] else "NO", r["turns"],
            r["corrections_sent"], r["tokens_in"], r["tokens_out"],
            f"{r['cost']:.4f}",
            "PASS" if r["complete_order"] else "FAIL",
        ]
        ws1.append(row)

    for row in ws1.iter_rows(min_row=2, max_row=ws1.max_row):
        complete = row[15].value
        row[15].fill = green if complete == "PASS" else red
        for cell in row:
            cell.alignment = wrap
            cell.border = border

    widths1 = [8, 40, 10, 8, 10, 10, 8, 12, 12, 10, 6, 10, 10, 10, 10, 10]
    for i, w in enumerate(widths1, 1):
        ws1.column_dimensions[get_column_letter(i)].width = w

    # ── Sheet 2: Per-Model Summary ──
    ws2 = wb.create_sheet("Per-Model Summary")
    h2 = ["Model", "Orders Tested", "Complete Order Accuracy %",
          "Product Recall %", "Product Precision %", "Qty Accuracy %",
          "Ship-to Accuracy %", "Hallucination Rate %",
          "Avg Turns", "Avg Corrections",
          "Zero-Correction Orders", "Total Tokens", "Total Cost ($)",
          "Cost per Order ($)"]
    ws2.append(h2)
    style_header(ws2, len(h2))

    for model_name in ["Sonnet", "Haiku", "Flash"]:
        mr = [r for r in all_results if r["model"] == model_name]
        if not mr:
            continue
        n = len(mr)
        complete = sum(1 for r in mr if r["complete_order"])
        total_target = sum(r["target_count"] for r in mr)
        total_matched = sum(r["matched"] for r in mr)
        total_llm = sum(r["llm_count"] for r in mr)
        total_qty = sum(r["qty_correct"] for r in mr)
        total_extra = sum(r["extra"] for r in mr)
        ship_correct = sum(1 for r in mr if r["ship_to_correct"])
        zero_corr = sum(1 for r in mr if r["corrections_sent"] == 0)
        total_tok = sum(r["tokens_in"] + r["tokens_out"] for r in mr)
        total_cost = sum(r["cost"] for r in mr)

        recall = total_matched / total_target * 100 if total_target else 0
        precision = total_matched / total_llm * 100 if total_llm else 0
        qty_acc = total_qty / total_matched * 100 if total_matched else 0
        ship_acc = ship_correct / n * 100
        halluc_rate = total_extra / total_llm * 100 if total_llm else 0

        row = [
            model_name, n, f"{complete / n * 100:.0f}%",
            f"{recall:.0f}%", f"{precision:.0f}%", f"{qty_acc:.0f}%",
            f"{ship_acc:.0f}%", f"{halluc_rate:.0f}%",
            f"{sum(r['turns'] for r in mr) / n:.1f}",
            f"{sum(r['corrections_sent'] for r in mr) / n:.1f}",
            zero_corr, f"{total_tok:,}",
            f"${total_cost:.4f}", f"${total_cost / n:.4f}",
        ]
        ws2.append(row)

    for row in ws2.iter_rows(min_row=2, max_row=ws2.max_row):
        for cell in row:
            cell.alignment = wrap
            cell.border = border

    widths2 = [10, 10, 18, 12, 12, 12, 12, 14, 10, 12, 14, 12, 12, 12]
    for i, w in enumerate(widths2, 1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # ── Sheet 3: Head-to-Head ──
    ws3 = wb.create_sheet("Head-to-Head")
    h3 = ["Scenario", "Ship-to", "Target Items",
          "Sonnet Complete", "Sonnet Recall", "Sonnet Qty%", "Sonnet Turns", "Sonnet Cost",
          "Haiku Complete", "Haiku Recall", "Haiku Qty%", "Haiku Turns", "Haiku Cost",
          "Flash Complete", "Flash Recall", "Flash Qty%", "Flash Turns", "Flash Cost",
          "Best Model"]
    ws3.append(h3)
    style_header(ws3, len(h3))

    # Group results by order
    by_order = defaultdict(dict)
    for r in all_results:
        key = (r["scenario_id"], r["ship_to"])
        by_order[key][r["model"]] = r

    for key in sorted(by_order.keys()):
        scenario_id, ship_to = key
        models_data = by_order[key]
        target = list(models_data.values())[0]["target_count"] if models_data else 0

        row = [scenario_id, ship_to[:40], target]

        best_model = None
        best_score = -1
        for mname in ["Sonnet", "Haiku", "Flash"]:
            if mname in models_data:
                r = models_data[mname]
                complete = "PASS" if r["complete_order"] else "FAIL"
                recall = f"{r['matched']}/{r['target_count']}"
                qty_pct = f"{r['qty_correct']}/{r['matched']}" if r["matched"] else "0/0"
                score = r["matched"] * 10 + r["qty_correct"]
                if score > best_score:
                    best_score = score
                    best_model = mname
                row.extend([complete, recall, qty_pct, r["turns"], f"${r['cost']:.4f}"])
            else:
                row.extend(["N/A", "N/A", "N/A", "N/A", "N/A"])

        row.append(best_model or "N/A")
        ws3.append(row)

    for row in ws3.iter_rows(min_row=2, max_row=ws3.max_row):
        for cell in row:
            cell.alignment = wrap
            cell.border = border
        # Color complete/fail cells
        for col_idx in [3, 8, 13]:  # Sonnet/Haiku/Flash Complete columns (0-indexed)
            cell = row[col_idx]
            if cell.value == "PASS":
                cell.fill = green
            elif cell.value == "FAIL":
                cell.fill = red

    widths3 = [8, 35, 8] + [10, 8, 8, 6, 8] * 3 + [10]
    for i, w in enumerate(widths3, 1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    wb.save(output_path)


# ── Main ──
def main():
    print("=" * 70)
    print("  Conversational Bot Test — 13 FULLY AUTO orders × 3 models")
    print("=" * 70)

    auto_orders = identify_auto_orders()
    print(f"\nIdentified {len(auto_orders)} FULLY AUTO orders")
    for ao in auto_orders:
        print(f"  S{ao['scenario_id']:02d} | {ao['ship_to'][:45]} | {len(ao['benchmark_codes'])} items")

    all_results = []
    total_tests = len(auto_orders) * len(MODELS)
    test_num = 0

    for model_cfg in MODELS:
        print(f"\n{'=' * 70}")
        print(f"  MODEL: {model_cfg['name']}")
        print(f"{'=' * 70}")

        for ao in auto_orders:
            test_num += 1
            sid = ao["scenario_id"]
            ship_to = ao["ship_to"]

            print(f"\n  [{test_num}/{total_tests}] S{sid:02d} | {ship_to[:40]} | {model_cfg['name']}")

            try:
                result = run_conversation(ao, model_cfg)
                all_results.append(result)

                status = "PASS" if result["complete_order"] else "FAIL"
                print(f"    Result: {status} | "
                      f"Products: {result['matched']}/{result['target_count']} | "
                      f"Qty: {result['qty_correct']}/{result['matched']} | "
                      f"Turns: {result['turns']} | Cost: ${result['cost']:.4f}")

            except Exception as e:
                print(f"    ERROR: {str(e)[:100]}")
                all_results.append({
                    "scenario_id": f"S{sid:02d}", "ship_to": ship_to,
                    "chat_name": "", "model": model_cfg["name"],
                    "target_count": len(ao["benchmark_codes"]),
                    "llm_count": 0, "matched": 0, "qty_correct": 0,
                    "extra": 0, "missed": len(ao["benchmark_codes"]),
                    "ship_to_correct": False, "complete_order": False,
                    "turns": 0, "corrections_sent": 0,
                    "tokens_in": 0, "tokens_out": 0, "cost": 0,
                    "conversation": [], "llm_json": None,
                    "details": [], "error": str(e),
                })

            # Rate limiting
            time.sleep(1)

    # Save raw results
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    raw_path = os.path.join(SCRIPT_DIR, f"conv_test_raw_{ts}.json")
    with open(raw_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nRaw results: {raw_path}")

    # Build Excel
    xlsx_path = os.path.join(SCRIPT_DIR, f"Conv_Test_Results_{ts}.xlsx")
    build_results_excel(all_results, xlsx_path)
    print(f"Excel results: {xlsx_path}")

    # Print summary
    print(f"\n{'=' * 70}")
    print(f"  SUMMARY")
    print(f"{'=' * 70}")
    for model_name in ["Sonnet", "Haiku", "Flash"]:
        mr = [r for r in all_results if r["model"] == model_name]
        n = len(mr)
        complete = sum(1 for r in mr if r["complete_order"])
        total_cost = sum(r["cost"] for r in mr)
        total_matched = sum(r["matched"] for r in mr)
        total_target = sum(r["target_count"] for r in mr)
        total_qty = sum(r["qty_correct"] for r in mr)

        print(f"\n  {model_name}:")
        print(f"    Complete Order Accuracy: {complete}/{n} ({complete * 100 // n if n else 0}%)")
        print(f"    Product Recall: {total_matched}/{total_target}")
        print(f"    Qty Accuracy: {total_qty}/{total_matched if total_matched else 1}")
        print(f"    Total Cost: ${total_cost:.4f}")


if __name__ == "__main__":
    main()
