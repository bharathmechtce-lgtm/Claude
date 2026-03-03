"""
Shared scoring utilities for the WhatsApp Order Bot test harness.

Single source of truth for:
- Generic ground truth filtering (Fix E/2): score only items mentioned in messages
- Unified order scoring against SAP truth
- JSON extraction from LLM responses
- Fuzzy matching helpers

Used by: simulate_1to1.py, run_all_haiku.py, two_agent_sim.py
"""

import re
import json
from difflib import SequenceMatcher


# ═══════════════════════════════════════════════════════════════
# FUZZY MATCHING
# ═══════════════════════════════════════════════════════════════

def fuzzy_match(s1, s2):
    """Compute similarity ratio between two strings (case-insensitive)."""
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


# ═══════════════════════════════════════════════════════════════
# JSON EXTRACTION
# ═══════════════════════════════════════════════════════════════

def extract_json(text):
    """Extract JSON object from LLM response text.

    Handles:
    - JSON in markdown code blocks (```json ... ```)
    - Raw JSON in text
    - Balanced brace matching for nested objects
    """
    if not text:
        return None

    # Try markdown code block first
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try balanced brace extraction
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


# ═══════════════════════════════════════════════════════════════
# GENERIC GROUND TRUTH FILTERING
#
# Instead of manually removing scenarios like S01, we generically
# filter SAP items to only those the customer actually mentioned.
# If the customer said 3 things and SAP has 23, score against 3.
# ═══════════════════════════════════════════════════════════════

# Words to skip when matching item names to messages (units, sizes, packaging)
_SIZE_WORDS = {
    "1kg", "2kg", "3kg", "5kg", "10kg", "20kg", "500gms", "100gms", "140gms",
    "750gms", "650g", "400gms", "768gms", "875gms", "960gm", "800gm", "623g",
    "1ltr", "2ltr", "750ml", "330ml", "300ml", "500ml", "100ml", "4000ml",
    "pcs", "gms", "bag", "box", "nos", "pkt", "btl", "can", "jar",
    "pouch", "tin", "the", "and", "for", "with", "inch",
}

# Known brand names for brand-aware matching
_KNOWN_BRANDS = {
    "amul", "pillsbury", "baskin", "davinci", "tulua", "sankalp", "gooddot",
    "kissan", "maggi", "nescafe", "hershey", "hersheys", "mccain", "mccains",
    "gowardhan", "dlecta", "delecta", "indibites", "manama", "perrier",
    "veeba", "signature", "sugam", "switz", "mother", "dairy",
    "tata", "knorr", "solas", "swiss", "sprite", "coke", "kinley",
    "real", "dabur", "epigamia", "fiamma", "nestle", "plant", "power",
    "schweppes", "golden", "crown",
}

# Very generic words that match too broadly on their own
_VERY_GENERIC = {
    "free", "low", "fat", "cut", "mix", "plus", "pure",
    "bulk", "block", "hot", "red", "bag", "raw",
}

# Common product type words for shorthand matching
_PRODUCT_TYPE_WORDS = {
    "vanilla", "chocolate", "strawberry", "mango",
    "coffee", "butterscotch", "brownie", "waffle",
    "tortilla", "paratha", "samosa", "idli", "vada",
    "fries", "wedges", "syrup", "cream", "butter",
    "cheese", "soda", "juice", "ketchup", "mustard",
    "turmeric", "cumin", "coriander", "pepper",
}


def _fuzzy_word_in_text(word, text_words):
    """Check if word appears in text_words with fuzzy matching for typos."""
    if word in text_words:
        return True
    for tw in text_words:
        if len(tw) >= 4 and len(word) >= 4:
            if SequenceMatcher(None, word, tw).ratio() > 0.75:
                return True
    return False


def item_mentioned_in_messages(item_desc, item_code, all_msg_text):
    """Check if a SAP item has textual basis in customer messages.

    Lenient: prefer false positives (keeping unreachable items) over
    false negatives (filtering out items the customer did order).

    Args:
        item_desc: SAP item description (e.g. "AMUL BUTTER 500GMS")
        item_code: SAP item code
        all_msg_text: concatenated customer message text

    Returns:
        True if the item appears to be mentioned in the messages
    """
    msg_lower = all_msg_text.lower()
    msg_words = set(re.findall(r'[a-zA-Z]{3,}', msg_lower))
    msg_lines = [line.strip().lower() for line in all_msg_text.split('\n')
                 if line.strip()]

    # Extract meaningful words from SAP item description
    desc_words = re.findall(r'[a-zA-Z]{3,}', item_desc.lower())
    desc_words = [w for w in desc_words if w not in _SIZE_WORDS]

    if not desc_words:
        return False

    brand_words = [w for w in desc_words if w in _KNOWN_BRANDS]
    product_words = [w for w in desc_words if w not in _KNOWN_BRANDS]

    # Strategy 1: Brand match with lenient product check
    if brand_words:
        brand_in_msg = any(_fuzzy_word_in_text(bw, msg_words) for bw in brand_words)
        if brand_in_msg:
            if len(product_words) <= 2:
                return True
            if any(_fuzzy_word_in_text(pw, msg_words) for pw in product_words
                   if pw not in _VERY_GENERIC and len(pw) >= 4):
                return True

    # Strategy 2: Line-level match
    for line in msg_lines:
        line_words = set(re.findall(r'[a-zA-Z]{3,}', line))
        if not line_words:
            continue

        prod_matches = sum(1 for w in product_words
                           if w not in _VERY_GENERIC
                           and _fuzzy_word_in_text(w, line_words))
        brand_matches = sum(1 for w in brand_words
                            if _fuzzy_word_in_text(w, line_words))

        if brand_matches >= 1 and prod_matches >= 1:
            return True
        if prod_matches >= 2:
            return True
        for w in product_words:
            if len(w) >= 6 and w not in _VERY_GENERIC:
                if _fuzzy_word_in_text(w, line_words):
                    return True

    # Strategy 3: Cross-line brand + product match
    for bw in brand_words:
        if _fuzzy_word_in_text(bw, msg_words):
            for pw in product_words:
                if len(pw) >= 4 and pw not in _VERY_GENERIC:
                    if _fuzzy_word_in_text(pw, msg_words):
                        return True

    # Strategy 4: Product type shorthand match
    matched_product_types = []
    for w in product_words:
        if w in _PRODUCT_TYPE_WORDS and _fuzzy_word_in_text(w, msg_words):
            matched_product_types.append(w)

    if matched_product_types and brand_words:
        for bw in brand_words:
            if _fuzzy_word_in_text(bw, msg_words):
                return True

    if len(matched_product_types) >= 2:
        return True

    return False


def filter_testable_items(target_items, all_msg_text):
    """Filter SAP ground truth items to only those mentioned in customer messages.

    This is the generic alternative to manually removing scenarios.
    If customer said 3 things and SAP has 23, score against 3.

    Args:
        target_items: list of dicts (must have 'description' and 'item_code' keys)
        all_msg_text: concatenated customer message text

    Returns:
        Filtered list of testable items (deduped by item_code)
    """
    testable = []
    seen_codes = {}

    for item in target_items:
        desc = item.get("description", "")
        code = item.get("item_code", "")

        if item_mentioned_in_messages(desc, code, all_msg_text):
            if code in seen_codes:
                continue
            seen_codes[code] = len(testable)
            testable.append(item)

    return testable


# ═══════════════════════════════════════════════════════════════
# UNIFIED ORDER SCORING
# ═══════════════════════════════════════════════════════════════

def score_order(llm_json, target_items, target_ship_to=None,
                match_threshold=0.4, qty_tolerance=0.10):
    """Score an LLM-extracted order against ground truth.

    Args:
        llm_json: parsed JSON from LLM (must have "orders" key)
        target_items: list of SAP truth dicts (item_code, description, quantity)
        target_ship_to: expected ship-to address string (None to skip check)
        match_threshold: minimum fuzzy match score (default 0.4)
        qty_tolerance: allowed quantity deviation as fraction (default 0.10 = 10%)

    Returns:
        Dict with scoring metrics: matched, qty_correct, extra, missed, details, etc.
    """
    result = {
        "target_count": len(target_items),
        "llm_count": 0,
        "matched": 0,
        "qty_correct": 0,
        "extra": 0,
        "missed": 0,
        "ship_to_correct": target_ship_to is None,  # True if not checking
        "complete_order": False,
        "details": [],
    }

    if not llm_json or "orders" not in llm_json:
        result["missed"] = len(target_items)
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
                "original_text": line.get("original_text", ""),
                "conversion": line.get("conversion_applied", ""),
            })

    result["llm_count"] = len(llm_lines)

    # Check ship-to match (if target provided)
    if target_ship_to is not None:
        for st in llm_ship_tos:
            if fuzzy_match(st, target_ship_to) >= 0.5:
                result["ship_to_correct"] = True
                break

    # Build all match pairs and sort by score (greedy best-first)
    all_pairs = []
    for li, ll in enumerate(llm_lines):
        for ti, tgt in enumerate(target_items):
            if (ll["item_code"] and ll["item_code"] != "UNKNOWN"
                    and ll["item_code"] == tgt["item_code"]):
                score = 1.0
            else:
                score = max(
                    fuzzy_match(ll.get("item_name", ""), tgt["description"]),
                    fuzzy_match(ll.get("item_code", ""), tgt["item_code"]),
                )
            all_pairs.append((score, li, ti))

    all_pairs.sort(key=lambda x: -x[0])
    matched_target = set()
    matched_llm = set()

    for score, li, ti in all_pairs:
        if li in matched_llm or ti in matched_target:
            continue
        if score < match_threshold:
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
        if sap_qty > 0:
            qty_ok = abs(llm_qty - sap_qty) / sap_qty <= qty_tolerance
        else:
            qty_ok = abs(llm_qty - sap_qty) < 0.01

        if qty_ok:
            result["qty_correct"] += 1

        result["details"].append({
            "status": "MATCH" if qty_ok else "QTY_MISMATCH",
            "llm_item": ll["item_name"][:50],
            "llm_code": ll["item_code"],
            "llm_qty": llm_qty,
            "sap_item": tgt["description"][:50],
            "sap_code": tgt["item_code"],
            "sap_qty": sap_qty,
            "match_score": score,
            "conversion": ll.get("conversion", ""),
        })

    result["matched"] = len(matched_target)

    # Extra items (LLM output not matched to any SAP item)
    for li, ll in enumerate(llm_lines):
        if li not in matched_llm:
            result["extra"] += 1
            result["details"].append({
                "status": "EXTRA",
                "llm_item": ll["item_name"][:50],
                "llm_code": ll["item_code"],
                "llm_qty": ll.get("quantity", 0),
                "sap_item": "-",
                "sap_code": "-",
                "sap_qty": 0,
                "match_score": 0,
            })

    # Missed items (SAP items not matched by LLM)
    for ti, tgt in enumerate(target_items):
        if ti not in matched_target:
            result["missed"] += 1
            result["details"].append({
                "status": "MISSED",
                "llm_item": "-",
                "llm_code": "-",
                "llm_qty": 0,
                "sap_item": tgt["description"][:50],
                "sap_code": tgt["item_code"],
                "sap_qty": tgt["quantity"],
                "match_score": 0,
            })

    result["complete_order"] = (
        result["matched"] == result["target_count"]
        and result["qty_correct"] == result["matched"]
        and result["extra"] == 0
        and result["ship_to_correct"]
    )

    return result


# ═══════════════════════════════════════════════════════════════
# NATURAL CONVERSATION HELPERS
#
# For test harnesses: respond to the bot's questions naturally
# instead of hardcoded followups. The bot should drive the
# conversation — the test harness just answers what's asked.
# ═══════════════════════════════════════════════════════════════

def classify_bot_response(bot_resp):
    """Classify what the bot is doing so the test harness can respond naturally.

    Returns one of:
        "asking_location"   — bot asks about delivery location/address
        "asking_product"    — bot asks for product clarification / brand choice
        "asking_conversion" — bot shows conversion math and asks to confirm
        "showing_summary"   — bot presents an order summary for confirmation
        "acknowledging"     — bot acknowledged the order (no question asked)
        "other"             — anything else
    """
    lower = bot_resp.lower()
    has_question = "?" in bot_resp

    # Showing a summary / asking for confirmation
    summary_signals = [
        "please confirm", "confirm this order", "order summary",
        "here's your order", "here is your order", "complete order",
        "order list", "order recap", "any changes",
    ]
    if any(s in lower for s in summary_signals):
        return "showing_summary"

    # Asking about location / delivery address
    location_signals = [
        "location", "address", "deliver", "ship to", "which outlet",
        "which branch", "which store", "where should",
    ]
    if has_question and any(s in lower for s in location_signals):
        return "asking_location"

    # Asking about product clarification
    product_signals = [
        "did you mean", "which one", "which product", "which brand",
        "clarify", "multiple options", "do you want",
    ]
    if has_question and any(s in lower for s in product_signals):
        return "asking_product"

    # Showing conversion math
    conversion_signals = ["pcs", "pieces", "× ", "x ", "÷ ", "/ ", "= "]
    confirm_signals = ["correct", "right", "confirm"]
    if (has_question
            and any(s in lower for s in conversion_signals)
            and any(s in lower for s in confirm_signals)):
        return "asking_conversion"

    # Just acknowledged
    ack_signals = ["got it", "noted", "received", "taken", "understood"]
    if any(s in lower for s in ack_signals) and not has_question:
        return "acknowledging"

    return "other"


def auto_respond(bot_resp, ship_to, turn_number, max_turns=6):
    """Generate a natural customer response based on what the bot said.

    Args:
        bot_resp: the bot's latest message
        ship_to: the target delivery location
        turn_number: current turn in the conversation (0-indexed)
        max_turns: maximum conversation turns before forcing extraction

    Returns:
        (response_text, should_extract) — text to send, and whether to extract next
    """
    intent = classify_bot_response(bot_resp)

    if intent == "asking_location":
        return f"Deliver to: {ship_to}", False

    if intent == "asking_product":
        return "Yes, go with the closest match from your catalogue", False

    if intent == "asking_conversion":
        return "Yes, that's correct", False

    if intent == "showing_summary":
        return "Confirmed, looks good", True

    # If the bot just acknowledged or said something else,
    # and we've had enough turns, ask for the summary
    if turn_number >= max_turns - 1:
        return "That's all. Please show the complete order for confirmation.", False

    if intent == "acknowledging":
        return "That's all for today. Please confirm the complete order.", False

    # Default: prompt for summary
    return "Please show me the complete order summary for confirmation.", False
