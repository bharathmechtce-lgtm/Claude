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


def _fuzzy_word_in_text(word, text_words):
    """Check if word appears in text_words with fuzzy matching for common typos."""
    if word in text_words:
        return True
    # Common Indian English spelling variations
    for tw in text_words:
        if len(tw) >= 4 and len(word) >= 4:
            if SequenceMatcher(None, word, tw).ratio() > 0.75:
                return True
    return False


# ── Filter SAP items to only those mentioned in messages ──
def item_in_messages(item_desc, item_code, all_msg_text):
    """Check if a SAP item has textual basis in customer messages.

    Lenient: prefer false positives (keeping unreachable items) over
    false negatives (filtering out items the customer did order).
    """
    msg_lower = all_msg_text.lower()
    msg_words = set(re.findall(r'[a-zA-Z]{3,}', msg_lower))
    msg_lines = [line.strip().lower() for line in all_msg_text.split('\n')
                 if line.strip()]

    # Extract meaningful words from SAP item description
    desc_words = re.findall(r'[a-zA-Z]{3,}', item_desc.lower())
    desc_words = [w for w in desc_words if w not in SIZE_WORDS]

    if not desc_words:
        return False

    # Split into brand words and product words
    known_brands = {
        "amul", "pillsbury", "baskin", "davinci", "tulua", "sankalp", "gooddot",
        "kissan", "maggi", "nescafe", "hershey", "hersheys", "mccain", "mccains",
        "gowardhan", "dlecta", "delecta", "indibites", "manama", "perrier",
        "veeba", "signature", "sugam", "switz", "mother", "dairy",
        "tata", "knorr", "solas", "swiss", "sprite", "coke", "kinley",
        "real", "dabur", "epigamia", "fiamma", "nestle", "plant", "power",
        "schweppes", "golden", "crown",
    }

    brand_words = [w for w in desc_words if w in known_brands]
    product_words = [w for w in desc_words if w not in known_brands]

    # Totally generic words that match too broadly on their own
    very_generic = {"free", "low", "fat", "cut", "mix", "plus", "pure",
                    "bulk", "block", "hot", "red", "bag", "raw"}

    # Strategy 1: Brand match with lenient product check
    # If the customer mentions the brand, the item is likely relevant
    if brand_words:
        brand_in_msg = any(_fuzzy_word_in_text(bw, msg_words) for bw in brand_words)
        if brand_in_msg:
            # Brand-only items (Sprite, Coke, etc.) — brand match is enough
            if len(product_words) <= 2:
                return True
            # Brand + at least 1 non-generic product word matches
            if any(_fuzzy_word_in_text(pw, msg_words) for pw in product_words
                   if pw not in very_generic and len(pw) >= 4):
                return True

    # Strategy 2: Line-level match
    for line in msg_lines:
        line_words = set(re.findall(r'[a-zA-Z]{3,}', line))
        if not line_words:
            continue

        prod_matches = sum(1 for w in product_words
                           if w not in very_generic
                           and _fuzzy_word_in_text(w, line_words))
        brand_matches = sum(1 for w in brand_words
                            if _fuzzy_word_in_text(w, line_words))

        # Brand + any product word in same line
        if brand_matches >= 1 and prod_matches >= 1:
            return True

        # 2+ non-generic product words in same line (no brand needed)
        if prod_matches >= 2:
            return True

        # Single distinctive product word (>= 6 chars) in line
        for w in product_words:
            if len(w) >= 6 and w not in very_generic:
                if _fuzzy_word_in_text(w, line_words):
                    return True

    # Strategy 3: Cross-line brand + product match
    for bw in brand_words:
        if _fuzzy_word_in_text(bw, msg_words):
            for pw in product_words:
                if len(pw) >= 4 and pw not in very_generic:
                    if _fuzzy_word_in_text(pw, msg_words):
                        return True

    # Strategy 4: Product type match for common shorthand
    # Customers often say "vanilla 12 block" meaning "baskin vanilla"
    # Match if a distinctive product word appears and it's not too ambiguous
    product_type_words = {"vanilla", "chocolate", "strawberry", "mango",
                          "coffee", "butterscotch", "brownie", "waffle",
                          "tortilla", "paratha", "samosa", "idli", "vada",
                          "fries", "wedges", "syrup", "cream", "butter",
                          "cheese", "soda", "juice", "ketchup", "mustard",
                          "turmeric", "cumin", "coriander", "pepper"}
    matched_product_types = []
    for w in product_words:
        if w in product_type_words and _fuzzy_word_in_text(w, msg_words):
            matched_product_types.append(w)

    # If a brand is present in messages AND product type matches, it's a match
    if matched_product_types and brand_words:
        for bw in brand_words:
            if _fuzzy_word_in_text(bw, msg_words):
                return True

    # If 2+ product type words match across the message, likely a match
    if len(matched_product_types) >= 2:
        return True

    return False


def filter_testable_items(target_items, all_msg_text):
    """Filter SAP items to only those that appear in customer messages."""
    testable = []
    seen_codes = {}  # track item_code -> best match for dedup

    for item in target_items:
        desc = item.get("description", "")
        code = item.get("item_code", "")

        if item_in_messages(desc, code, all_msg_text):
            # Dedup: if same item_code already seen, keep only one
            if code in seen_codes:
                # Keep the one with qty closest to what might be in the message
                # (heuristic: keep the first occurrence)
                continue
            seen_codes[code] = len(testable)
            testable.append(item)

    return testable


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

QUANTITY CONVERSION RULES (customers speak in cases/kg/box, SAP records in PCS):

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
- Use PackSize from catalogue for case/box conversion: qty = X × PackSize
- Use UnitWeight from catalogue for kg conversion: qty = X ÷ UnitWeight
- For pcs/btl/pkt/nos/block: use the number directly as PCS
- Match products to the catalogue using item codes — do NOT invent codes
- Include conversion notes showing your math
- If you can't match a product, use item_code "UNKNOWN"
- NEVER include items the customer did not ask for
"""


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

    # Turn 2: Answer questions
    if "?" in bot_resp:
        send(f"Ship to: {ship_to}")

    # Turn 2.5: Summary + confirm
    send("That's it. Please show me the complete order summary for confirmation.")
    send("Confirmed. Looks good.")

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
