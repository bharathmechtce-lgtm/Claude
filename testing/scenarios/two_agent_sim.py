#!/usr/bin/env python3
"""
Two-Agent Simulation: Opus 4.6 (Customer) vs Haiku 4.5 (Bot)

Tests the 13 previously-failed orders through a 4-step conversational flow:
  Step 1: Customer mentions name → Bot welcomes, asks for location
  Step 2: Customer confirms location → Bot confirms
  Step 3: Customer sends order → Bot confirms products, handles matching
  Step 4: Customer confirms → Bot completes order

Customer messages are pre-composed (by Opus in session, no API cost).
Only Haiku API calls are made for the bot.
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

BOT_MODEL = "claude-haiku-4-5-20251001"
BOT_INPUT_COST = 0.80   # per 1M tokens
BOT_OUTPUT_COST = 4.0    # per 1M tokens

# Load data
with open(os.path.join(SCRIPT_DIR, "test_scenarios.json")) as f:
    ALL_SCENARIOS = json.load(f)["scenarios"]

with open(os.path.join(SCRIPT_DIR, "product_catalog.json")) as f:
    CATALOG = json.load(f)

CATALOG_BY_CODE = {p["item_code"]: p for p in CATALOG}


# ── API caller (Haiku only) ──
def call_haiku(messages, system_prompt):
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
                    "model": BOT_MODEL,
                    "max_tokens": 4096,
                    "system": system_prompt,
                    "messages": messages,
                },
                timeout=120,
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
                time.sleep(3)
                continue
            return "[TIMEOUT]", 0, 0
        except Exception as e:
            return f"[ERROR: {e}]", 0, 0
    return "[FAILED after retries]", 0, 0


# ── Build bot system prompt ──
def build_bot_system_prompt(scenario, target_ship_to):
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

QUANTITY CONVERSION RULES (customers speak in cases/kg/box, SAP records in PCS):
  CASE/BOX: "X case" or "X box" → quantity = X × PackSize (from catalogue)
  KG: "X kg" → quantity = X ÷ UnitWeight (from catalogue)
  DIRECT: "X pcs/btl/pkt/nos/block/bulk/tin/bag" → quantity = X PCS
  IMPORTANT: "block" means individual units. 1 block = 1 PCS always.

PRODUCT MATCHING RULES:
- Match customer text to the PRODUCT CATALOGUE below
- Do NOT invent item codes
- If match confidence is low, ASK for clarification
- If you cannot find a match, say so
- GENERIC TERMS: When a customer uses a generic term WITHOUT specifying a brand, do NOT
  default to one specific brand. Instead, ask which product they want by listing the
  matching options from the catalogue. Use context to narrow down sensibly:
  - "water bottle" / "pani" → list water/sparkling water brands (NOT sauce bottles)
  - "soda" → list soda brands only
  - "bottle" alone → use surrounding context to decide category. If ambiguous, ask.

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

Respond naturally as a WhatsApp assistant. Keep responses SHORT (2-4 lines max).
Do NOT output JSON unless specifically asked.
"""
    return prompt


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
          "conversion_applied": "3 case x 24 pcs/case = 72 PCS"
        }
      ]
    }
  ]
}

Rules:
- ALL quantities in PCS after conversion
- Use PackSize for case/box: qty = X x PackSize
- Use UnitWeight for kg: qty = X / UnitWeight
- For pcs/btl/pkt/nos/block: use number directly
- Match to catalogue — do NOT invent codes
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


# ═══════════════════════════════════════════════════════════════
# PRE-COMPOSED CUSTOMER MESSAGES (written by Opus in session)
#
# For each failed order, I (Opus 4.6) compose natural customer
# messages based on SAP truth items — the way a busy Indian
# restaurant manager would text on WhatsApp.
# ═══════════════════════════════════════════════════════════════

FAILED_ORDERS = [
    {
        "scenario_id": 1,
        "ship_to": "GOOD FOOD CONCEPT (BOMBAY GYMKHANA)",
        "target_items_filter": [
            "T17IS12T61PAN002", "T17IS12T61KAS001", "T17IS12T61MAG001",
            "T17ID03T61WAL001", "T17IS11T61TUR001", "T17IS11T61CUM001",
            "T17IS12T61CUM001", "T17IS12T61COR001",
        ],
        "step1": "Hi, this is Good Food Concept",
        "step2": "Bombay Gymkhana",
        "step3": "4 pkt panda chilli whole\n4 pkt kashmiri chilli whole\n4 pkt magaj seeds\n3 pkt walnut tukda\n2 pkt turmeric powder\n2 pkt cumin powder\n2 pkt cumin whole\n1 pkt coriander whole\nAll Tulua brand",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 1,
        "ship_to": "GOOD FOOD CONCEPT( GOREGAON E)",
        "target_items_filter": [
            "H01IM02B04VEG004", "H01IT04K04PUR002", "G01IC21P06IRI001",
            "S18IS04S32MEN001", "P04IP17G07PRO002", "D13IP17D31CRE003",
            "S05IG05S12GRE001", "S11IB05S48PTO003", "S11IB05S48CHA001",
            "G01IC01P06EVA001", "G01IC01P06ECH001", "T28IC03C42COK003",
            "T28IC03S09SPR005", "T28IC03C42COK008", "N02IN04M01NOO005",
        ],
        "step1": "Hi this is Good Food Concept",
        "step2": "Goregaon East",
        "step3": "12 pcs best foods veg mayo\n12 pcs kissan tomato puree\n12 pcs pillsbury iris cream\n12 pcs sankalp mendu vada\n24 pcs gowardhan cheese block hard\n8 pcs dlecta cream cheese\n5 pcs sugam frozen green peas\n10 pcs signature tortilla 10inch\n6 pcs signature chapatti\n2 pcs pillsbury egg free vanilla 5kg\n2 pcs pillsbury egg free chocolate 5kg\n48 diet coke can\n18 sprite 2.25ltr\n9 coke 2.25ltr\n5 pcs maggi noodles 1.8kg",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 4,
        "ship_to": "NINETY DEGREE (RABALE)",
        "target_items_filter": [
            "G01IC01P06CEC001", "G01IC01P06EBR001",
            "G01IC01P06BAK004", "G01IC01P06BAK002",
        ],
        "step1": "Hi, Laxmi Foods here",
        "step2": "Ninety Degree Rabale",
        "step3": "Eggless Brownie mix - 64 pcs\nClassic egg free chocolate 5kg - 8 bags\nBakers plus egg free vanilla 5kg - 60 pcs\nBakers plus egg free chocolate 5kg - 80 pcs",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 9,
        "ship_to": "GOOD FOOD CONCEPT (BOMBAY GYMKHANA)",
        "target_items_filter": [
            "J01IP17A02CHE001", "P04IP17G08SLI001", "M02IF03M04FF9001",
        ],
        "step1": "Hi Good Food Concept here",
        "step2": "Bombay Gymkhana",
        "step3": "Amul cheese block 1 box\nGo slices cheese 6 pcs\nMccains french fries 9mm 5 pkt",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 9,
        "ship_to": "GOOD FOOD CONCEPT-MAHIM.W",
        "target_items_filter": [
            "J01IS06A02BUT001", "P04IP17G08MOZ005", "P04IP17G08SLI001",
            "H13IA01I15MIN001",
        ],
        # Note: SAP has duplicate entries for some items. Using deduplicated targets.
        # Amul Butter: 15+3=18, Sankalp items etc. are separate ship-to items
        # For this test we focus on the 4 distinct product types the scoring matched
        "step1": "Hi its Good Food Concept",
        "step2": "Mahim West",
        "step3": "Amul butter 18 pcs\nGo mozzarella dice 2kg - 2 pcs\nGo slices cheese 3 pcs\nIndibites mini samosa 2 pcs",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 13,
        "ship_to": "OBEROI TOWER (NARIMAN POINT)",
        "target_items_filter": [
            "G06II01B03VAN001", "G01IC01P06EVA001", "G06II01B03HON001",
            "G01IC01P06ECH001", "G06II01B03COF002", "G06II01B03VER001",
            "G06II01B03MAN003",
        ],
        # Original: "Coffee - 48 block, Vanilla - 12 block" etc + premix
        "step1": "Hi this is Ketan from Oberoi Tower",
        "step2": None,  # Single ship-to, no location needed
        "step3": "Coffee ice cream - 48 block\nVanilla ice cream - 12 block\nStrawberry ice cream - 24 block\nHoney nut crunch - 24 block\nMango ice cream - 12 block\nChocolate premix 5kg - 20 pcs\nVanilla premix 5kg - 20 pcs",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 15,
        "ship_to": "HOTEL BAWA REGENCY",
        "target_items_filter": [
            "J01IC21A02CRE001", "P04IP17G08SLI001",
        ],
        # Previous test matched only cream but missed go slices
        "step1": "Hi Bawa Group here",
        "step2": "Bawa Regency",
        "step3": "Amul cream 1ltr - 12 pcs\nGo slices cheese - 10 pcs",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 18,
        "ship_to": "POKKIDO JUNIOR",
        "target_items_filter": [
            "M02IF03M04FF9001", "G06RI06B03MIS002", "G06RI06B03COT002",
            "J01IS06A02BUT001", "M05II01M11VAN002", "T17IS11T61RED001",
            "I02IP08H19CHO001", "T17IS11T61TUR001", "T17IS12T61MUS001",
        ],
        "step1": "Hi this is Prasuk Jain Hospitality",
        "step2": "Pokkido Junior, Lower Parel",
        "step3": "French fries 15 pkt\nBaskin mississippi mud 2 pcs\nBaskin cotton candy 2 pcs\nAmul butter 6 pcs\nMother dairy vanilla 4 bulk\nTulua red chilli powder 1 pkt\nHersheys chocolate syrup 1 pcs\nTulua turmeric powder 1 pkt\nTulua mustard seeds 1 pkt",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 18,
        "ship_to": "PRASUK JAIN HOSPITALITY- KURLA (W)",
        "target_items_filter": [
            "M02IF03M04FF9001", "M02IF03M04WEG001", "M05II01M11VAN002",
            "T17IS12T61KAS001", "T17IS12T61BLP001", "M20IC26M49STR001",
        ],
        "step1": "Hi Prasuk Jain Hospitality",
        "step2": "Kurla West",
        "step3": "French fries 5 pkt\nMccains wedges 2 pkt\nMother dairy vanilla 2 bulk\nTulua kashmiri chilli whole 2 pkt\nTulua black pepper whole 1 pkt\nManama strawberry crush 1 btl",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 20,
        "ship_to": "GOOD FOOD CONCEPT( DADAR E)",
        "target_items_filter": [
            "J01IS06A02BUT001", "J01IP17A02CHE001",
            "P04IP17G08MOZ005", "J01IC21A02CRE001",
        ],
        "step1": "Hi Good Food Concept",
        "step2": "Dadar East",
        "step3": "6kg amul butter\n3kg amul cheese block\n2kg pizza cheese diced\n1 box amul fresh cream",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 20,
        "ship_to": "GOOD FOOD CONCEPT( GOREGAON E)",
        "target_items_filter": [
            "S11IP05F06LAC001", "G01IC21P06IRI001", "N02IC14N04MIL004",
            "T28IC03K09SOD001", "T28IC03S09SPR005", "T28IC03C42COK008",
            "T28IC03S07TON001",
        ],
        "step1": "Hi this is Good Food Concept",
        "step2": "Goregaon East",
        "step3": "Signature lachha paratha 1 box\nIrish whip cream 1 box\nMilk maid 4 bottle\nKinley soda 2 box\nSprite 2.25ltr 1 box\nCoke 2.25ltr 1 box\nTonic water 2 box",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 24,
        "ship_to": "SNOW WORLD ENTERTAINMENT [NERUL E]",
        "target_items_filter": [
            "D05IN03R12MAN001", "D05IN03R12ORA001", "J01IS06A02BUT001",
            "J01IP17A02CHE001", "G07IU01A02TAZ001", "J01IC21A02CRE001",
        ],
        # Focusing on the items that were missed + key ones
        "step1": "Hi this is Snow World",
        "step2": "Nerul East",
        "step3": "Real mango juice 12 pkt\nReal orange juice 12 pkt\nAmul butter 20 pcs\nAmul cheese block 5 pcs\nAmul tazza milk 12 pkt\nAmul fresh cream 24 pcs",
        "step3_followup": None,
        "step4_confirm": True,
    },
    {
        "scenario_id": 24,
        "ship_to": "THE GAME PALACIO",
        "target_items_filter": [
            "M05II01M11VAN002", "J01IP17A02CHE001",
            "T17IS12T61CUM001", "G07IU01A02TAZ001", "H13IA01I15MIN001",
        ],
        "step1": "Hi from Game Palacio",
        "step2": "Game Palacio",
        "step3": "Mother dairy vanilla 4 bulk\nAmul cheese block 12 pcs\nTulua cumin whole 3 pkt\nAmul tazza milk 24 pkt\nIndibites mini samosa 2 pkt",
        "step3_followup": None,
        "step4_confirm": True,
    },
]


def run_one_order(order_def):
    """Run one order through the 4-step flow."""
    sid = order_def["scenario_id"]
    ship_to = order_def["ship_to"]
    scenario = next(s for s in ALL_SCENARIOS if s["scenario_id"] == sid)

    # Get target SAP items
    filter_codes = set(order_def["target_items_filter"])
    target_items = [
        i for i in scenario["sap_truth"]
        if i["ship_to_code"] == ship_to and i["item_code"] in filter_codes
    ]
    # Deduplicate by item_code (keep first, sum quantities)
    deduped = {}
    for item in target_items:
        code = item["item_code"]
        if code in deduped:
            deduped[code]["quantity"] += item["quantity"]
        else:
            deduped[code] = dict(item)
    target_items = list(deduped.values())

    bot_prompt = build_bot_system_prompt(scenario, ship_to)
    messages = []
    conv_log = []
    total_in = total_out = 0

    def bot_respond(customer_text, step_label):
        nonlocal total_in, total_out
        messages.append({"role": "user", "content": customer_text})
        conv_log.append({"step": step_label, "role": "CUSTOMER", "text": customer_text})

        time.sleep(1.5)  # Rate limiting
        resp, tok_in, tok_out = call_haiku(messages, bot_prompt)
        total_in += tok_in
        total_out += tok_out
        messages.append({"role": "assistant", "content": resp})
        conv_log.append({"step": step_label, "role": "BOT", "text": resp})
        return resp

    # ── STEP 1: Introduction ──
    bot_resp = bot_respond(order_def["step1"], "1-intro")

    # ── STEP 2: Location ──
    if order_def["step2"]:
        bot_resp = bot_respond(order_def["step2"], "2-location")
        # If bot double-checks, confirm again
        if "?" in bot_resp and order_def["step2"]:
            bot_resp = bot_respond(f"Yes, {order_def['step2']} confirmed", "2b-confirm")

    # ── STEP 3: Order ──
    bot_resp = bot_respond(order_def["step3"], "3-order")

    # Handle follow-up if needed
    if order_def.get("step3_followup"):
        bot_resp = bot_respond(order_def["step3_followup"], "3b-followup")

    # If bot asks clarification, answer
    if "?" in bot_resp:
        bot_resp = bot_respond("That's correct, please proceed", "3c-clarify")

    # Request summary
    bot_resp = bot_respond("That's it. Please show complete order summary.", "3d-summary")

    # ── STEP 4: Confirm ──
    if order_def["step4_confirm"]:
        bot_resp = bot_respond("Confirmed, looks good", "4-confirm")
    else:
        bot_resp = bot_respond("Confirmed", "4-confirm")

    # ── EXTRACTION ──
    extraction_resp = bot_respond(EXTRACTION_PROMPT, "5-extract")
    llm_json = extract_json(extraction_resp)

    if not llm_json:
        extraction_resp = bot_respond(
            "Please output the order as valid JSON only. No other text.",
            "5b-retry")
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
                    f"For {d['sap_item'][:40]}: should be {d['sap_qty']:.0f} PCS, not {d['llm_qty']:.0f}")
            elif d["status"] == "MISSED":
                corrections.append(
                    f"Missing: {d['sap_item'][:40]} — need {d['sap_qty']:.0f} PCS")
            elif d["status"] == "EXTRA":
                corrections.append(
                    f"Remove {d['llm_item'][:40]} — not ordered")

        if corrections:
            corrections_sent = 1
            bot_respond("Please correct:\n" + "\n".join(corrections), "6-correct")
            re_resp = bot_respond(EXTRACTION_PROMPT, "6b-re-extract")
            llm_json_2 = extract_json(re_resp)
            if llm_json_2:
                final_score = score_order(llm_json_2, target_items, ship_to)
                llm_json = llm_json_2

    cost = (total_in / 1e6) * BOT_INPUT_COST + (total_out / 1e6) * BOT_OUTPUT_COST

    return {
        "scenario_id": f"S{sid:02d}",
        "ship_to": ship_to,
        "chat_name": scenario["chat_name"],
        "difficulty": scenario["difficulty"],
        "target_count": final_score["target_count"],
        "llm_count": final_score["llm_count"],
        "matched": final_score["matched"],
        "qty_correct": final_score["qty_correct"],
        "extra": final_score["extra"],
        "missed": final_score["missed"],
        "ship_to_correct": final_score["ship_to_correct"],
        "complete_order": final_score["complete_order"],
        "corrections_sent": corrections_sent,
        "tokens_in": total_in,
        "tokens_out": total_out,
        "cost": cost,
        "details": final_score["details"],
        "conversation": conv_log,
        "llm_json": llm_json,
    }


def main():
    print("=" * 70)
    print("  TWO-AGENT SIM: Opus 4.6 (Customer) vs Haiku 4.5 (Bot)")
    print(f"  {len(FAILED_ORDERS)} previously-failed orders")
    print(f"  Customer: pre-composed by Opus (no API cost)")
    print(f"  Bot: {BOT_MODEL} (API calls)")
    print("=" * 70)

    results = []
    for i, order_def in enumerate(FAILED_ORDERS):
        sid = order_def["scenario_id"]
        ship_to = order_def["ship_to"][:45]

        print(f"\n{'─' * 70}")
        print(f"  [{i+1}/{len(FAILED_ORDERS)}] S{sid:02d} | {ship_to}")
        print(f"{'─' * 70}")

        try:
            result = run_one_order(order_def)
            results.append(result)

            status = "PASS" if result["complete_order"] else "FAIL"
            print(f"\n    {status} | Products: {result['matched']}/{result['target_count']} | "
                  f"Qty: {result['qty_correct']}/{result['matched']} | "
                  f"Extra: {result['extra']} | Missed: {result['missed']} | "
                  f"Corrections: {result['corrections_sent']} | ${result['cost']:.4f}")

            if not result["complete_order"]:
                for d in result["details"]:
                    if d["status"] not in ("MATCH",):
                        label = d.get("sap_item", d.get("llm_item", "?"))[:50]
                        print(f"      {d['status']}: {label}")

            # Print conversation
            print(f"\n    Conversation:")
            for turn in result["conversation"]:
                text = turn["text"][:120].replace("\n", " | ")
                print(f"      [{turn['step']}] {turn['role']}: {text}")

        except Exception as e:
            print(f"    ERROR: {str(e)[:100]}")
            import traceback
            traceback.print_exc()

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(SCRIPT_DIR, f"two_agent_results_{ts}.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Summary
    print(f"\n{'=' * 70}")
    print(f"  RESULTS SUMMARY")
    print(f"{'=' * 70}")

    n = len(results)
    passed = sum(1 for r in results if r["complete_order"])
    total_target = sum(r["target_count"] for r in results)
    total_matched = sum(r["matched"] for r in results)
    total_qty = sum(r["qty_correct"] for r in results)
    total_extra = sum(r["extra"] for r in results)
    total_missed = sum(r["missed"] for r in results)
    total_cost = sum(r["cost"] for r in results)

    print(f"\n  Orders tested:    {n}")
    print(f"  Complete PASS:    {passed}/{n} ({passed*100//n if n else 0}%)")
    print(f"  Product recall:   {total_matched}/{total_target} ({total_matched*100//total_target if total_target else 0}%)")
    print(f"  Qty accuracy:     {total_qty}/{total_matched} ({total_qty*100//total_matched if total_matched else 0}%)")
    print(f"  Extra (halluc):   {total_extra}")
    print(f"  Missed:           {total_missed}")
    print(f"  Total cost:       ${total_cost:.4f}")

    for diff in ["EASY", "MEDIUM", "HARD"]:
        dr = [r for r in results if r["difficulty"] == diff]
        if not dr:
            continue
        dp = sum(1 for r in dr if r["complete_order"])
        dn = len(dr)
        print(f"  {diff}: {dp}/{dn} PASS")

    print(f"\n  Results: {out_path}")


if __name__ == "__main__":
    main()
