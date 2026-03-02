#!/usr/bin/env python3
"""
Two-Agent Simulation: Opus 4.6 (Customer) vs Haiku 4.5 (Bot)

Tests the WhatsApp Order Bot with a realistic 4-step conversational flow:
  Step 1: Customer mentions name → Bot welcomes, asks for location
  Step 2: Customer confirms location → Bot confirms (double-checks if ambiguous)
  Step 3: Customer sends order items → Bot confirms products, handles matching
  Step 4: Customer confirms → Bot completes order

Opus 4.6 plays the customer NATURALLY (not scripted) based on scenario data.
Haiku 4.5 plays the bot using the same system prompt as production.

Scores results against SAP ground truth.
"""

import json
import os
import re
import sys
import time
import requests
from datetime import datetime
from difflib import SequenceMatcher

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

# Models
CUSTOMER_MODEL = "claude-opus-4-6"       # Opus 4.6 = customer
BOT_MODEL = "claude-haiku-4-5-20251001"  # Haiku 4.5 = bot

# Cost rates (per 1M tokens)
COSTS = {
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
}

# Load data
with open(os.path.join(SCRIPT_DIR, "test_scenarios.json")) as f:
    ALL_SCENARIOS = json.load(f)["scenarios"]

with open(os.path.join(SCRIPT_DIR, "product_catalog.json")) as f:
    CATALOG = json.load(f)

CATALOG_BY_CODE = {p["item_code"]: p for p in CATALOG}


# ── API caller ──
def call_claude(messages, system_prompt, model_id):
    """Call Claude API. Returns (text, input_tokens, output_tokens)."""
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
                    "model": model_id,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": messages,
                },
                timeout=180,
            )
            data = resp.json()
            if resp.status_code == 429:
                wait = min(2 ** (attempt + 2), 30)
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


# ── Build bot system prompt (same as production) ──
def build_bot_system_prompt(scenario, target_ship_to):
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
1. When the customer first messages you with their name, WELCOME them warmly
2. If they have more than one ship-to address, ASK which location this order is for
3. Read the items and quantities they mention — confirm what you understood
4. If a product name is ambiguous, ask for clarification with options
5. Handle "add" messages by merging into the current order
6. Handle "cancel" / "remove" messages by updating the order
7. Keep a RUNNING ORDER — after each interaction, you know the full order state
8. Be conversational but efficient — these are busy restaurant/hotel managers

ANTI-HALLUCINATION RULES (CRITICAL):
- ONLY include items the customer EXPLICITLY mentioned
- NEVER infer, suggest, or add items not asked for
- If in doubt, ASK rather than guess
- Count your output items against customer's message. MORE items = hallucinating

QUANTITY CONVERSION RULES (customers speak in cases/kg/box, SAP records in PCS):

  CASE/BOX: "X case" or "X box" → quantity = X × PackSize (from catalogue)
    Example: "3 box" of Kinley Soda (PackSize=24) → 3 × 24 = 72 PCS
    Example: "1 box" of Amul Butter 500GMS (PackSize=20) → 1 × 20 = 20 PCS

  KG: "X kg" → quantity = X ÷ UnitWeight (from catalogue)
    Example: "5 kg" of Amul Butter 500GMS (UnitWeight=0.5kg) → 5 ÷ 0.5 = 10 PCS

  DIRECT (no conversion):
    "X pcs/btl/pkt/nos/block/bulk/tin/bag" → quantity = X PCS
  IMPORTANT: "block" means individual units. 1 block = 1 PCS always.

PRODUCT MATCHING RULES:
- Match customer text to the PRODUCT CATALOGUE below
- Do NOT invent item codes
- If match confidence is low, ASK for clarification
- If you cannot find a match, say so

PRODUCT CATALOGUE (items this customer typically orders):
"""
    for h in scenario.get("historical_patterns", [])[:40]:
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
- Format: numbered list with item name, quantity (in PCS), and delivery location
- Ask: "Please confirm this order, or let me know if any changes are needed"
- Only after customer confirms should you consider the order final

Respond naturally as a WhatsApp assistant. Keep responses SHORT (2-4 lines max).
Do NOT output JSON unless specifically asked.
"""
    return prompt


# ── Build customer system prompt for Opus ──
def build_customer_system_prompt(scenario, target_ship_to, target_items, order_text):
    card_names = ", ".join(scenario["card_names"])
    ship_addresses = scenario["ship_to_addresses"]

    # Build the items the customer needs to order
    items_desc = []
    for item in target_items:
        cat = CATALOG_BY_CODE.get(item["item_code"], {})
        items_desc.append(
            f"  - {item['description']} (qty: {item['quantity']:.0f} PCS, "
            f"item_code: {item['item_code']})"
        )

    prompt = f"""You are playing a CUSTOMER of TJUK, a food distribution company in Mumbai.
You are texting the TJUK order bot on WhatsApp to place an order.

YOUR IDENTITY:
  Name: {card_names}
  Your delivery location: {target_ship_to}
  You have {len(ship_addresses)} possible delivery locations: {', '.join(ship_addresses[:5])}{'...' if len(ship_addresses) > 5 else ''}

YOUR ORDER (what you need to order):
{chr(10).join(items_desc)}

ORIGINAL MESSAGE STYLE (how real customers text):
{order_text}

INSTRUCTIONS — Follow this 4-step flow EXACTLY:

STEP 1 (FIRST MESSAGE ONLY):
- Send your name/company name to start the conversation
- Example: "Hi, this is {card_names}"
- Do NOT send the order yet — just introduce yourself

STEP 2 (WHEN BOT ASKS FOR LOCATION):
- Confirm your delivery location: {target_ship_to}
- If bot asks to double-check between similar locations, confirm clearly
- If bot does NOT ask for location (only 1 address), just proceed

STEP 3 (SEND YOUR ORDER):
- Send your order items naturally, the way a busy restaurant manager would text on WhatsApp
- Use the ORIGINAL MESSAGE STYLE above as reference for how to phrase things
- You can send items in one message or split across messages
- Use natural units (kg, box, btl, pcs, block) — NOT the PCS conversion
- Be brief and natural — no formal language

STEP 4 (CONFIRM):
- When the bot shows an order summary, review it
- If it looks correct, confirm with something like "Yes confirmed" or "Looks good"
- If something is wrong, point it out and correct it
- Once confirmed, you're done

CRITICAL RULES:
- Be NATURAL — text like a busy Indian restaurant manager on WhatsApp
- Use SHORT messages (1-3 lines max)
- Use the same units/style as the original messages
- Do NOT mention item codes — customers don't know codes
- Do NOT say "PCS" — say kg, box, btl, block etc. naturally
- If bot asks a question, answer it directly
- Stay in character throughout
"""
    return prompt


# ── Discover orders ──
def discover_orders():
    """Find all testable orders from scenarios."""
    orders = []
    for sc in ALL_SCENARIOS:
        ship_tos = set()
        for item in sc["sap_truth"]:
            ship_tos.add(item["ship_to_code"])
        for st in sorted(ship_tos):
            items = [i for i in sc["sap_truth"] if i["ship_to_code"] == st]
            # Get order messages
            order_msgs = [m for m in sc["original_group_messages"]
                          if m["type"] in ("order", "order_addition")]
            orders.append({
                "scenario_id": sc["scenario_id"],
                "difficulty": sc["difficulty"],
                "chat_name": sc["chat_name"],
                "ship_to": st,
                "item_count": len(items),
                "order_text": "\n".join(m["text"] for m in order_msgs),
            })
    return orders


# ── JSON extraction ──
EXTRACTION_PROMPT = """Please output the COMPLETE order as structured JSON.
Include ALL items from the conversation (additions included, cancellations removed).

CRITICAL: ONLY include items the customer EXPLICITLY ordered. Do NOT add extras.

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
- ALL quantities in PCS after conversion
- Use PackSize for case/box: qty = X × PackSize
- Use UnitWeight for kg: qty = X ÷ UnitWeight
- For pcs/btl/pkt/nos/block: use number directly
- Match to catalogue using item codes — do NOT invent codes
- NEVER include items not ordered
"""


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


# ── Scoring ──
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
        qty_ok = abs(llm_qty - sap_qty) / sap_qty <= 0.10 if sap_qty > 0 else abs(llm_qty - sap_qty) < 0.01
        if qty_ok:
            result["qty_correct"] += 1
        result["details"].append({
            "status": "MATCH" if qty_ok else "QTY_MISMATCH",
            "llm_item": ll["item_name"][:50], "llm_code": ll["item_code"],
            "llm_qty": llm_qty, "sap_item": tgt["description"][:50],
            "sap_code": tgt["item_code"], "sap_qty": sap_qty,
        })

    result["matched"] = len(matched_target)
    for li, ll in enumerate(llm_lines):
        if li not in matched_llm:
            result["extra"] += 1
            result["details"].append({
                "status": "EXTRA", "llm_item": ll["item_name"][:50],
                "llm_code": ll["item_code"], "llm_qty": ll.get("quantity", 0),
            })
    for ti, tgt in enumerate(target_items):
        if ti not in matched_target:
            result["missed"] += 1
            result["details"].append({
                "status": "MISSED", "sap_item": tgt["description"][:50],
                "sap_code": tgt["item_code"], "sap_qty": tgt["quantity"],
            })

    result["complete_order"] = (
        result["matched"] == result["target_count"]
        and result["qty_correct"] == result["matched"]
        and result["extra"] == 0
        and result["ship_to_correct"]
    )
    return result


# ── SAP truth filtering (reuse from run_all_haiku) ──
SIZE_WORDS = {
    "1kg", "2kg", "3kg", "5kg", "10kg", "20kg", "500gms", "100gms", "140gms",
    "750gms", "650g", "400gms", "768gms", "875gms", "960gm", "800gm", "623g",
    "1ltr", "2ltr", "750ml", "330ml", "300ml", "500ml", "100ml", "4000ml",
    "pcs", "gms", "bag", "box", "nos", "pkt", "btl", "can", "jar",
    "pouch", "tin", "the", "and", "for", "with", "inch",
}

known_brands = {
    "amul", "pillsbury", "baskin", "davinci", "tulua", "sankalp", "gooddot",
    "kissan", "maggi", "nescafe", "hershey", "hersheys", "mccain", "mccains",
    "gowardhan", "dlecta", "delecta", "indibites", "manama", "perrier",
    "veeba", "signature", "sugam", "switz", "mother", "dairy",
    "tata", "knorr", "solas", "swiss", "sprite", "coke", "kinley",
    "real", "dabur", "epigamia", "fiamma", "nestle", "plant", "power",
    "schweppes", "golden", "crown",
}

very_generic = {"free", "low", "fat", "cut", "mix", "plus", "pure",
                "bulk", "block", "hot", "red", "bag", "raw"}


def _fuzzy_word_in_text(word, text_words):
    if word in text_words:
        return True
    for tw in text_words:
        if len(tw) >= 4 and len(word) >= 4:
            if SequenceMatcher(None, word, tw).ratio() > 0.75:
                return True
    return False


def item_in_messages(item_desc, item_code, all_msg_text):
    msg_lower = all_msg_text.lower()
    msg_words = set(re.findall(r'[a-zA-Z]{3,}', msg_lower))

    desc_words = re.findall(r'[a-zA-Z]{3,}', item_desc.lower())
    desc_words = [w for w in desc_words if w not in SIZE_WORDS]

    if not desc_words:
        return False

    brand_words = [w for w in desc_words if w in known_brands]
    product_words = [w for w in desc_words if w not in known_brands]

    if brand_words:
        brand_in_msg = any(_fuzzy_word_in_text(bw, msg_words) for bw in brand_words)
        if brand_in_msg:
            if len(product_words) <= 2:
                return True
            if any(_fuzzy_word_in_text(pw, msg_words) for pw in product_words
                   if pw not in very_generic and len(pw) >= 4):
                return True

    msg_lines = [line.strip().lower() for line in all_msg_text.split('\n') if line.strip()]
    for line in msg_lines:
        line_words = set(re.findall(r'[a-zA-Z]{3,}', line))
        if not line_words:
            continue
        prod_matches = sum(1 for w in product_words
                           if w not in very_generic
                           and _fuzzy_word_in_text(w, line_words))
        brand_matches = sum(1 for w in brand_words
                            if _fuzzy_word_in_text(w, line_words))
        if brand_matches >= 1 and prod_matches >= 1:
            return True
        if prod_matches >= 2:
            return True
        for w in product_words:
            if len(w) >= 6 and w not in very_generic:
                if _fuzzy_word_in_text(w, line_words):
                    return True

    return False


def filter_testable_items(target_items, all_msg_text):
    testable = []
    seen_codes = {}
    for item in target_items:
        desc = item.get("description", "")
        code = item.get("item_code", "")
        if item_in_messages(desc, code, all_msg_text):
            if code in seen_codes:
                continue
            seen_codes[code] = len(testable)
            testable.append(item)
    return testable


# ── Run one 2-agent conversation ──
def run_two_agent(order_info):
    sid = order_info["scenario_id"]
    ship_to = order_info["ship_to"]
    scenario = next(s for s in ALL_SCENARIOS if s["scenario_id"] == sid)

    # Get target items
    raw_target_items = [i for i in scenario["sap_truth"] if i["ship_to_code"] == ship_to]
    if not raw_target_items:
        return None

    order_text = order_info["order_text"]
    target_items = filter_testable_items(raw_target_items, order_text)
    if not target_items:
        return None

    # Build prompts
    bot_prompt = build_bot_system_prompt(scenario, ship_to)
    customer_prompt = build_customer_system_prompt(
        scenario, ship_to, target_items, order_text)

    # Conversation state
    bot_messages = []       # messages from bot's perspective
    customer_messages = []  # messages from customer's perspective
    conv_log = []
    total_bot_in = total_bot_out = 0
    total_cust_in = total_cust_out = 0
    step = 1

    def customer_says(instruction=""):
        """Opus generates a customer message."""
        nonlocal total_cust_in, total_cust_out
        # Add instruction as a system-level hint
        msgs = list(customer_messages)
        if instruction:
            msgs.append({"role": "user", "content": f"[INSTRUCTION: {instruction}]"})
        else:
            msgs.append({"role": "user", "content": "[YOUR TURN: Send your next message as the customer.]"})

        time.sleep(1)
        text, tok_in, tok_out = call_claude(msgs, customer_prompt, CUSTOMER_MODEL)
        total_cust_in += tok_in
        total_cust_out += tok_out

        # Clean up any meta-text
        text = text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]

        # Update both conversation histories
        customer_messages.append({"role": "user", "content": "[YOUR TURN]"})
        customer_messages.append({"role": "assistant", "content": text})

        return text

    def bot_responds(customer_text):
        """Haiku responds to customer message."""
        nonlocal total_bot_in, total_bot_out
        bot_messages.append({"role": "user", "content": customer_text})

        time.sleep(1)
        text, tok_in, tok_out = call_claude(bot_messages, bot_prompt, BOT_MODEL)
        total_bot_in += tok_in
        total_bot_out += tok_out

        bot_messages.append({"role": "assistant", "content": text})

        # Also add to customer's view
        customer_messages.append({"role": "user", "content": f"[BOT REPLIED]: {text}"})

        return text

    # ══════════════════════════════════════════════════════════
    # STEP 1: Customer introduces themselves
    # ══════════════════════════════════════════════════════════
    print(f"    Step 1: Customer introduction...")
    cust_msg = customer_says("STEP 1: Introduce yourself with your company name. Just say hi and your name. Do NOT send the order yet.")
    conv_log.append({"step": 1, "role": "customer", "text": cust_msg})
    print(f"      Customer: {cust_msg[:100]}")

    bot_resp = bot_responds(cust_msg)
    conv_log.append({"step": 1, "role": "bot", "text": bot_resp})
    print(f"      Bot: {bot_resp[:120]}")

    # ══════════════════════════════════════════════════════════
    # STEP 2: Location confirmation
    # ══════════════════════════════════════════════════════════
    print(f"    Step 2: Location confirmation...")
    if "?" in bot_resp or len(scenario["ship_to_addresses"]) > 1:
        cust_msg = customer_says("STEP 2: The bot asked for your location or you need to provide it. Confirm your delivery location.")
        conv_log.append({"step": 2, "role": "customer", "text": cust_msg})
        print(f"      Customer: {cust_msg[:100]}")

        bot_resp = bot_responds(cust_msg)
        conv_log.append({"step": 2, "role": "bot", "text": bot_resp})
        print(f"      Bot: {bot_resp[:120]}")

        # If bot double-checks location (similar names)
        if "?" in bot_resp:
            cust_msg = customer_says("STEP 2b: The bot is double-checking. Confirm your exact location clearly.")
            conv_log.append({"step": 2, "role": "customer", "text": cust_msg})
            print(f"      Customer: {cust_msg[:100]}")

            bot_resp = bot_responds(cust_msg)
            conv_log.append({"step": 2, "role": "bot", "text": bot_resp})
            print(f"      Bot: {bot_resp[:120]}")
    else:
        conv_log.append({"step": 2, "role": "system", "text": "Single location — skipped"})

    # ══════════════════════════════════════════════════════════
    # STEP 3: Customer sends order
    # ══════════════════════════════════════════════════════════
    print(f"    Step 3: Order items...")
    cust_msg = customer_says("STEP 3: Now send your order. Send all the items you need, using natural language and units (kg, box, btl, block etc). Be brief like a WhatsApp message.")
    conv_log.append({"step": 3, "role": "customer", "text": cust_msg})
    print(f"      Customer: {cust_msg[:150]}")

    bot_resp = bot_responds(cust_msg)
    conv_log.append({"step": 3, "role": "bot", "text": bot_resp})
    print(f"      Bot: {bot_resp[:200]}")

    # If bot asks for clarification, customer responds
    clarification_rounds = 0
    while "?" in bot_resp and clarification_rounds < 3:
        clarification_rounds += 1
        cust_msg = customer_says("STEP 3b: The bot asked a question about your order. Answer it clearly and directly.")
        conv_log.append({"step": 3, "role": "customer", "text": cust_msg})
        print(f"      Customer: {cust_msg[:100]}")

        bot_resp = bot_responds(cust_msg)
        conv_log.append({"step": 3, "role": "bot", "text": bot_resp})
        print(f"      Bot: {bot_resp[:150]}")

    # Ask for summary
    cust_msg = customer_says("STEP 3c: Say 'that's it' or 'done' to signal you're done ordering. Ask the bot to show a summary.")
    conv_log.append({"step": 3, "role": "customer", "text": cust_msg})
    print(f"      Customer: {cust_msg[:100]}")

    bot_resp = bot_responds(cust_msg)
    conv_log.append({"step": 3, "role": "bot", "text": bot_resp})
    print(f"      Bot summary: {bot_resp[:250]}")

    # ══════════════════════════════════════════════════════════
    # STEP 4: Customer confirms
    # ══════════════════════════════════════════════════════════
    print(f"    Step 4: Confirmation...")
    cust_msg = customer_says("STEP 4: The bot showed your order summary. If it looks right, confirm it. If something is off, correct it.")
    conv_log.append({"step": 4, "role": "customer", "text": cust_msg})
    print(f"      Customer: {cust_msg[:100]}")

    bot_resp = bot_responds(cust_msg)
    conv_log.append({"step": 4, "role": "bot", "text": bot_resp})
    print(f"      Bot: {bot_resp[:120]}")

    # ══════════════════════════════════════════════════════════
    # EXTRACTION: Ask bot for JSON
    # ══════════════════════════════════════════════════════════
    print(f"    Extracting JSON...")
    extraction_resp = bot_responds(EXTRACTION_PROMPT)
    llm_json = extract_json(extraction_resp)

    if not llm_json:
        extraction_resp = bot_responds("Please output the order as valid JSON only. No other text.")
        llm_json = extract_json(extraction_resp)

    # Score
    first_score = score_order(llm_json, target_items, ship_to)

    # Correction round if needed
    final_score = first_score
    corrections_sent = 0
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
            bot_responds("Please correct:\n" + "\n".join(corrections))
            re_resp = bot_responds(EXTRACTION_PROMPT)
            llm_json_2 = extract_json(re_resp)
            if llm_json_2:
                final_score = score_order(llm_json_2, target_items, ship_to)
                llm_json = llm_json_2

    # Costs
    bot_cost = (total_bot_in / 1e6) * COSTS[BOT_MODEL]["input"] + \
               (total_bot_out / 1e6) * COSTS[BOT_MODEL]["output"]
    cust_cost = (total_cust_in / 1e6) * COSTS[CUSTOMER_MODEL]["input"] + \
                (total_cust_out / 1e6) * COSTS[CUSTOMER_MODEL]["output"]

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
        "clarification_rounds": clarification_rounds,
        "corrections_sent": corrections_sent,
        "bot_tokens_in": total_bot_in,
        "bot_tokens_out": total_bot_out,
        "bot_cost": bot_cost,
        "customer_tokens_in": total_cust_in,
        "customer_tokens_out": total_cust_out,
        "customer_cost": cust_cost,
        "total_cost": bot_cost + cust_cost,
        "details": final_score["details"],
        "conversation": conv_log,
        "llm_json": llm_json,
    }


# ── Main ──
def main():
    # Select a representative subset: 2 EASY + 2 MEDIUM + 2 HARD = 6 orders
    # Pick single-ship-to scenarios for cleaner testing
    all_orders = discover_orders()

    # Pick representative scenarios
    selected = []
    for diff in ["EASY", "MEDIUM", "HARD"]:
        candidates = [o for o in all_orders if o["difficulty"] == diff
                       and o["item_count"] >= 2 and o["item_count"] <= 12]
        # Pick first 2 that have reasonable item counts
        selected.extend(candidates[:2])

    if not selected:
        print("ERROR: No suitable test scenarios found")
        sys.exit(1)

    print("=" * 70)
    print(f"  TWO-AGENT SIMULATION: Opus 4.6 (Customer) vs Haiku 4.5 (Bot)")
    print(f"  {len(selected)} orders selected (2 EASY + 2 MEDIUM + 2 HARD)")
    print("=" * 70)
    for i, o in enumerate(selected):
        print(f"  {i+1}. S{o['scenario_id']:02d} ({o['difficulty']}) | "
              f"{o['chat_name'][:35]} | {o['ship_to'][:35]} | {o['item_count']} items")

    results = []
    for i, order in enumerate(selected):
        sid = order["scenario_id"]
        ship_to = order["ship_to"][:45]
        diff = order["difficulty"]

        print(f"\n{'─' * 70}")
        print(f"  [{i+1}/{len(selected)}] S{sid:02d} ({diff}) | {order['chat_name']} | {ship_to}")
        print(f"{'─' * 70}")

        try:
            result = run_two_agent(order)
            if result is None:
                print(f"    SKIP: No testable items")
                continue
            results.append(result)

            status = "PASS" if result["complete_order"] else "FAIL"
            print(f"\n    ── RESULT: {status} ──")
            print(f"    Products: {result['matched']}/{result['target_count']} matched")
            print(f"    Qty correct: {result['qty_correct']}/{result['matched']}")
            print(f"    Extra: {result['extra']} | Missed: {result['missed']}")
            print(f"    Ship-to: {'OK' if result['ship_to_correct'] else 'WRONG'}")
            print(f"    Clarifications: {result['clarification_rounds']} | Corrections: {result['corrections_sent']}")
            print(f"    Bot cost: ${result['bot_cost']:.4f} | Customer (Opus) cost: ${result['customer_cost']:.4f}")

            if not result["complete_order"]:
                for d in result["details"]:
                    if d["status"] != "MATCH":
                        label = d.get("llm_item", d.get("sap_item", "?"))[:45]
                        print(f"      {d['status']}: {label}")

        except Exception as e:
            print(f"    ERROR: {str(e)[:100]}")
            import traceback
            traceback.print_exc()

    # Save results
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(SCRIPT_DIR, f"two_agent_results_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print(f"  RESULTS SUMMARY — Two-Agent Simulation")
    print(f"  Customer: {CUSTOMER_MODEL} | Bot: {BOT_MODEL}")
    print(f"{'=' * 70}")

    n = len(results)
    if n == 0:
        print("  No results to summarize")
        return

    passed = sum(1 for r in results if r["complete_order"])
    total_target = sum(r["target_count"] for r in results)
    total_matched = sum(r["matched"] for r in results)
    total_qty = sum(r["qty_correct"] for r in results)
    total_extra = sum(r["extra"] for r in results)
    total_missed = sum(r["missed"] for r in results)
    total_bot_cost = sum(r["bot_cost"] for r in results)
    total_cust_cost = sum(r["customer_cost"] for r in results)

    print(f"\n  Orders tested:      {n}")
    print(f"  Complete PASS:      {passed}/{n} ({passed*100//n if n else 0}%)")
    print(f"  Product recall:     {total_matched}/{total_target} ({total_matched*100//total_target if total_target else 0}%)")
    print(f"  Qty accuracy:       {total_qty}/{total_matched} ({total_qty*100//total_matched if total_matched else 0}%)")
    print(f"  Extra (halluc):     {total_extra}")
    print(f"  Missed:             {total_missed}")
    print(f"  Bot cost (Haiku):   ${total_bot_cost:.4f}")
    print(f"  Customer (Opus):    ${total_cust_cost:.4f}")
    print(f"  Total cost:         ${total_bot_cost + total_cust_cost:.4f}")

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

    print(f"\n  Results saved: {out_path}")

    # Print conversation excerpts for each test
    print(f"\n{'=' * 70}")
    print(f"  CONVERSATION EXCERPTS")
    print(f"{'=' * 70}")
    for r in results:
        print(f"\n  ── {r['scenario_id']} | {r['chat_name']} | {'PASS' if r['complete_order'] else 'FAIL'} ──")
        for turn in r["conversation"]:
            role = turn["role"].upper()
            text = turn["text"][:120]
            print(f"    [{turn['step']}] {role}: {text}")


if __name__ == "__main__":
    main()
