#!/usr/bin/env python3
"""
Build Conversation Benchmark for WhatsApp Order Bot Testing.

Produces an Excel workbook with 3 sheets:
  - Test Cases: One row per scenario/order — what I (the tester) know going in
  - Line Items: One row per product — prediction (AUTO/HIL/CHECK) with reasoning
  - Summary: Aggregate stats per scenario and overall

The benchmark is designed for conversational testing:
  - I already know what I want to order (the answer key from SAP)
  - I mimic the customer's style (from raw messages)
  - I push the bot until it resolves each item or genuinely can't
  - Then I compare bot output vs SAP truth vs my predictions
"""

import json
import re
from difflib import SequenceMatcher
from collections import defaultdict
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ── Load data ──────────────────────────────────────────────────────────────

with open("test_scenarios.json") as f:
    data = json.load(f)
scenarios = data["scenarios"]

with open("product_catalog.json") as f:
    catalog = json.load(f)

catalog_by_code = {p["item_code"]: p for p in catalog}
catalog_by_name = {}
for p in catalog:
    catalog_by_name[p["item_name"].upper().strip()] = p


# ── Helpers ────────────────────────────────────────────────────────────────

def fuzzy_score(a, b):
    """SequenceMatcher ratio between two strings."""
    return SequenceMatcher(None, a.upper(), b.upper()).ratio()


def word_overlap_score(customer_text, catalog_name):
    """Fraction of customer words found in catalog name."""
    # Only skip pure stop words and short unit abbreviations.
    # Do NOT skip product-descriptor words like block, cream, butter etc.
    skip = {"kg", "gm", "gms", "ml", "ltr", "pkt", "btl", "nos",
            "pcs", "plz", "pls", "send", "order", "add", "for", "the",
            "and", "of", "in", "to", "a", "ok", "please", "ka", "hai",
            "ye", "sab"}
    cust_words = set(re.findall(r'[a-zA-Z]{2,}', customer_text.upper())) - \
                 {w.upper() for w in skip}
    cat_words = set(re.findall(r'[a-zA-Z]{2,}', catalog_name.upper()))
    if not cust_words:
        return 0
    matched = cust_words & cat_words
    return len(matched) / len(cust_words) if cust_words else 0


def find_best_catalog_match(text, catalog_items):
    """Find best matching catalog product for customer text."""
    text_clean = text.strip()
    if not text_clean:
        return None, 0.0

    best_match = None
    best_score = 0.0

    for p in catalog_items:
        # Try fuzzy match
        fs = fuzzy_score(text_clean, p["item_name"])
        # Try word overlap
        ws = word_overlap_score(text_clean, p["item_name"])
        # Combined score: weighted average
        combined = max(fs, ws * 0.9)

        if combined > best_score:
            best_score = combined
            best_match = p

    return best_match, best_score


def extract_quantity_and_unit(text):
    """Extract quantity and unit from a product line."""
    text = text.strip()

    # Patterns: "10 kg", "10kg", "3 box", "3box", "2 btl", "50 pkt", "24 nos"
    # Also: "1 case", "3 cases", "1ltr", "12 pkt"
    patterns = [
        r'(\d+(?:\.\d+)?)\s*(kg|kgs|gm|gms|gram|grams|ml|ltr|litre|liter|litres|liters)',
        r'(\d+(?:\.\d+)?)\s*(box|boxes|case|cases|crate|crates)',
        r'(\d+(?:\.\d+)?)\s*(btl|btls|bottle|bottles)',
        r'(\d+(?:\.\d+)?)\s*(pkt|pkts|packet|packets|pcs|pieces|nos|pc)',
        r'(\d+(?:\.\d+)?)\s*(tin|tins|can|cans)',
        r'(\d+(?:\.\d+)?)\s*(block|blocks|bulk)',
        r'(\d+(?:\.\d+)?)\s*(bag|bags)',
    ]

    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            qty = float(m.group(1))
            unit = m.group(2).lower()
            return qty, normalize_unit(unit)

    # Try just a number at the start or end
    m = re.search(r'[-–]\s*(\d+(?:\.\d+)?)\s*$', text)
    if m:
        return float(m.group(1)), "PCS"

    m = re.search(r'^(\d+(?:\.\d+)?)\s+', text)
    if m:
        return float(m.group(1)), "PCS"

    return None, None


def normalize_unit(unit):
    """Normalize unit string."""
    unit = unit.lower().strip()
    if unit in ("kg", "kgs"):
        return "KG"
    if unit in ("gm", "gms", "gram", "grams"):
        return "GM"
    if unit in ("ml",):
        return "ML"
    if unit in ("ltr", "litre", "liter", "litres", "liters"):
        return "LTR"
    if unit in ("box", "boxes", "case", "cases", "crate", "crates"):
        return "CASE"
    if unit in ("btl", "btls", "bottle", "bottles"):
        return "BTL"
    if unit in ("pkt", "pkts", "packet", "packets"):
        return "PKT"
    if unit in ("pcs", "pieces", "nos", "pc"):
        return "PCS"
    if unit in ("tin", "tins", "can", "cans"):
        return "TIN"
    if unit in ("block", "blocks", "bulk"):
        return "BLOCK"
    if unit in ("bag", "bags"):
        return "BAG"
    return "PCS"


def convert_to_pcs(qty, unit, catalog_item):
    """
    Convert customer quantity to PCS based on catalog item properties.
    Returns (converted_qty, conversion_note).
    """
    if catalog_item is None:
        return None, "No catalog match"

    pack_size = catalog_item.get("pack_size", 1)
    unit_weight = catalog_item.get("unit_weight_kg", 0)

    if unit == "KG":
        if unit_weight and unit_weight > 0:
            converted = qty / unit_weight
            # Round to nearest integer
            converted = round(converted)
            return converted, f"{qty}kg ÷ {unit_weight}kg/pc = {converted} PCS"
        else:
            return None, f"Cannot convert {qty}kg — no unit weight in catalog"

    elif unit == "GM":
        if unit_weight and unit_weight > 0:
            qty_kg = qty / 1000.0
            converted = round(qty_kg / unit_weight)
            return converted, f"{qty}gm → {qty_kg}kg ÷ {unit_weight}kg/pc = {converted} PCS"
        else:
            return None, f"Cannot convert {qty}gm — no unit weight in catalog"

    elif unit == "CASE" or unit == "BOX":
        if pack_size and pack_size > 1:
            converted = round(qty * pack_size)
            return converted, f"{qty} case × {pack_size} pcs/case = {converted} PCS"
        else:
            # pack_size = 1 means each case IS 1 unit
            return round(qty), f"{qty} case × 1 = {round(qty)} PCS"

    elif unit == "BLOCK" or unit == "BULK":
        # Block/bulk usually = 1 PCS each (ice cream blocks etc.)
        return round(qty), f"{qty} block = {round(qty)} PCS"

    elif unit in ("BTL", "TIN", "PKT", "PCS", "BAG"):
        return round(qty), f"{qty} {unit.lower()} = {round(qty)} PCS"

    elif unit == "LTR":
        # Litre items — typically 1 btl = 1 PCS
        return round(qty), f"{qty} ltr = {round(qty)} PCS"

    elif unit == "ML":
        return round(qty), f"{qty} ml = {round(qty)} PCS"

    else:
        return round(qty), f"{qty} {unit} → {round(qty)} PCS (assumed direct)"


def extract_order_lines(message_text):
    """Split a message into individual order lines, filtering out metadata."""
    lines = message_text.strip().split('\n')
    order_lines = []

    skip_patterns = [
        r'^\d{10,}$',           # PO numbers
        r'^send\s+to',          # delivery instructions
        r'^send\s+tomorrow',
        r'^regards',
        r'^@',                  # @mentions
        r'^\*?address',
        r'^\*?contact',
        r'^\*?date\s*:',
        r'^ok$',
        r'^done$',
        r'^please\s+please\s+order\s*$',
        r'^\d+/\d+/\d+',       # dates
        r'^billing',
        r'^gate\s+no',
        r'^phoenix',
        r'^senapati',
        r'mumbai',
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # Skip metadata lines
        skip = False
        for pat in skip_patterns:
            if re.search(pat, line, re.IGNORECASE):
                skip = True
                break

        # Skip if line is just a location header (handled separately)
        if skip:
            continue

        # Skip lines that look like location headers with no product info
        # (e.g., "Jogeshwari", "Good food consept Goregaon")
        # But keep lines that have qty+product info even with location prefix
        has_qty = bool(re.search(r'\d+\s*(kg|gm|box|case|btl|pkt|nos|pcs|block|bulk|tin|bag|ltr|ml)', line, re.IGNORECASE))
        has_number = bool(re.search(r'\d+', line))

        if not has_number:
            # Could be a location line or operational text — skip as order line
            continue

        # If it has a quantity pattern or a number, it might be a product line
        if has_qty or has_number:
            # Clean leading numbering like "1. " or "1) "
            cleaned = re.sub(r'^\d+[\.\)]\s*', '', line)
            # Remove trailing location/address info after product
            order_lines.append(cleaned)

    return order_lines


def extract_location_from_messages(messages):
    """Extract location/ship-to from message sequence."""
    locations = []
    for m in messages:
        if m.get("location") and m["location"].strip():
            locations.append(m["location"].strip())
        # Also check for location in operational messages
        if m["type"] == "operational":
            text = m["text"].strip()
            # Check if it's a short location response (like "Dadar parsee gymkhana")
            if len(text.split()) <= 6 and not text.startswith("@"):
                # Could be a location
                locations.append(text)
    return locations


def detect_special_instructions(messages):
    """Detect cancel/hold/modify instructions."""
    instructions = []
    for m in messages:
        text = m["text"].lower()
        if "cancel" in text:
            instructions.append(("CANCEL", m["text"], m["sender"]))
        if "hold" in text and m["type"] == "operational":
            instructions.append(("HOLD", m["text"], m["sender"]))
        if "add" in text and m["type"] in ("order_addition", "operational"):
            if "add" in text.split()[:3]:  # "add" near start
                instructions.append(("ADD", m["text"], m["sender"]))
        if re.search(r'make it|change|instead|replace', text):
            instructions.append(("MODIFY", m["text"], m["sender"]))
    return instructions


def match_ship_to(location_text, ship_to_addresses):
    """Try to match a customer's location text to a ship_to address."""
    if not location_text:
        return None, 0

    best = None
    best_score = 0
    for addr in ship_to_addresses:
        score = fuzzy_score(location_text, addr)
        # Also try word overlap
        ws = word_overlap_score(location_text, addr)
        combined = max(score, ws)
        if combined > best_score:
            best_score = combined
            best = addr
    return best, best_score


def _build_style_notes(messages):
    """Analyze customer messaging style for tester reference."""
    notes = []
    order_msgs = [m for m in messages if m["type"] in ("order", "order_addition")]

    if len(order_msgs) == 1:
        lines = order_msgs[0]["text"].strip().split('\n')
        product_lines = [l for l in lines if re.search(r'\d', l)]
        if len(product_lines) > 1:
            notes.append("Single message, multi-line (bulk order)")
        else:
            notes.append("Single message, single item")
    elif len(order_msgs) > 1:
        notes.append(f"Multiple order messages ({len(order_msgs)})")

    additions = [m for m in messages if m["type"] == "order_addition"]
    if additions:
        notes.append(f"{len(additions)} addition(s) — 'add more' style")

    # Check for Hindi/Marathi
    for m in messages:
        if re.search(r'[ा-ी]|bhejna|karva|kardena|mein|hai|dena|bhai',
                     m["text"], re.IGNORECASE):
            notes.append("Contains Hindi/Marathi text")
            break

    # Check for @ mentions
    for m in messages:
        if '@' in m["text"]:
            notes.append("Contains @mentions")
            break

    # Check for PO numbers
    for m in messages:
        if re.search(r'\d{10,}', m["text"]):
            notes.append("Contains PO numbers")
            break

    senders = set(m["sender"] for m in order_msgs)
    if len(senders) > 1:
        notes.append(f"Multiple order senders ({len(senders)})")

    return "; ".join(notes) if notes else "Straightforward"


# ── Main analysis ──────────────────────────────────────────────────────────

test_cases = []
line_items = []

for sc in scenarios:
    sid = sc["scenario_id"]
    difficulty = sc["difficulty"]
    chat_name = sc["chat_name"]
    date = sc["date"]
    card_names = sc["card_names"]
    ship_to_addrs = sc["ship_to_addresses"]
    messages = sc["original_group_messages"]
    sap_truth = sc["sap_truth"]
    historical = sc.get("historical_patterns", [])

    # Build historical lookup for this scenario
    hist_by_code = {h["item_code"]: h for h in historical}

    # ── Collect all raw message text (for test case sheet) ──
    raw_messages_text = []
    for m in messages:
        prefix = f"[{m['type']}] {m['sender']}"
        raw_messages_text.append(f"{prefix}: {m['text']}")

    # ── Extract locations mentioned by customer ──
    customer_locations = extract_location_from_messages(messages)

    # ── Extract all order lines from order/order_addition messages ──
    all_order_lines = []  # (line_text, message_type, sender, time, full_msg)
    for m in messages:
        if m["type"] in ("order", "order_addition"):
            lines = extract_order_lines(m["text"])
            for line in lines:
                all_order_lines.append({
                    "line_text": line,
                    "msg_type": m["type"],
                    "sender": m["sender"],
                    "time": m["time"],
                    "full_msg": m["text"],
                    "location": m.get("location", ""),
                })

    # ── Detect special instructions ──
    instructions = detect_special_instructions(messages)

    # ── Build the SAP expected items list ──
    # Group SAP items by ship_to for clarity
    sap_by_ship = defaultdict(list)
    for item in sap_truth:
        sap_by_ship[item["ship_to_code"]].append(item)

    # ── Build expected items text for test case ──
    expected_items_lines = []
    for ship_to, items in sap_by_ship.items():
        expected_items_lines.append(f"Ship-to: {ship_to}")
        for it in items:
            expected_items_lines.append(
                f"  {it['description']} | Qty: {it['quantity']} PCS | "
                f"Code: {it['item_code']}")
    expected_items_text = "\n".join(expected_items_lines)

    # ── Test Case row ──
    test_cases.append({
        "scenario_id": f"S{sid:02d}",
        "difficulty": difficulty,
        "chat_name": chat_name,
        "date": date,
        "customer": ", ".join(card_names),
        "ship_to_options": "\n".join(ship_to_addrs),
        "raw_messages": "\n\n".join(raw_messages_text),
        "customer_style_notes": _build_style_notes(messages),
        "expected_items": expected_items_text,
        "special_instructions": "\n".join(
            [f"{t}: {txt}" for t, txt, s in instructions]) if instructions else "None",
        "order_line_count": len(all_order_lines),
        "sap_item_count": len(sap_truth),
    })

    # ── Match SAP items to customer order lines (bipartite best-match) ──
    # Score ALL (sap_item, customer_line) pairs, then match greedily
    # from highest score to lowest to avoid mismatches

    all_pairs = []
    for si, sap_item in enumerate(sap_truth):
        cat_item = catalog_by_code.get(sap_item["item_code"])
        for li, ol in enumerate(all_order_lines):
            if cat_item:
                score = max(
                    fuzzy_score(ol["line_text"], cat_item["item_name"]),
                    word_overlap_score(ol["line_text"], cat_item["item_name"]),
                    fuzzy_score(ol["line_text"], sap_item["description"]),
                    word_overlap_score(ol["line_text"], sap_item["description"]),
                )
            else:
                score = max(
                    fuzzy_score(ol["line_text"], sap_item["description"]),
                    word_overlap_score(ol["line_text"], sap_item["description"]),
                )
            all_pairs.append((score, si, li))

    # Sort by score descending — best matches first
    all_pairs.sort(key=lambda x: -x[0])

    # Greedily assign: each customer line can match at most one SAP item
    matched_sap = {}      # si -> (li, score)
    used_lines = set()    # line indices already consumed

    for score, si, li in all_pairs:
        if si in matched_sap:
            continue
        if li in used_lines:
            continue
        if score >= 0.40:
            # Validate: customer line must have meaningful product-keyword
            # overlap with the SAP description specifically.
            # We check SAP desc only (not catalog) to avoid matching
            # customer text to wrong SAP items through catalog intermediary
            sap_desc_val = sap_truth[si]["description"]
            line_text_val = all_order_lines[li]["line_text"]
            wo_sap = word_overlap_score(line_text_val, sap_desc_val)
            cat_item_v = catalog_by_code.get(sap_truth[si]["item_code"])
            # Also allow if catalog item name has strong overlap AND
            # catalog item IS the SAP item (same code)
            wo_cat = word_overlap_score(
                line_text_val, cat_item_v["item_name"]) if cat_item_v else 0
            # Accept if SAP desc has keyword overlap >= 0.30
            # OR catalog name has very strong overlap >= 0.50
            if wo_sap >= 0.30 or wo_cat >= 0.50:
                matched_sap[si] = (li, score)
                used_lines.add(li)

    # Now iterate SAP items and build predictions
    for si, sap_item in enumerate(sap_truth):
        sap_code = sap_item["item_code"]
        sap_desc = sap_item["description"]
        sap_qty = sap_item["quantity"]
        sap_ship = sap_item["ship_to_code"]
        sap_doc = sap_item["doc_num"]

        cat_item = catalog_by_code.get(sap_code)

        if si in matched_sap:
            li, match_s = matched_sap[si]
            best_line = all_order_lines[li]
            best_line_score = match_s
        else:
            best_line = None
            best_line_score = 0

        customer_said = best_line["line_text"] if best_line else ""
        source_msg = best_line["full_msg"] if best_line else ""
        msg_time = best_line["time"] if best_line else ""
        msg_sender = best_line["sender"] if best_line else ""

        if not customer_said:
            # SAP item has no corresponding WhatsApp message
            # But we're dropping DATA_GAP per the new approach
            # This item wasn't mentioned in WhatsApp at all
            # The tester won't try to order this — it's outside the benchmark scope
            # Skip it — don't add to line items
            continue

        # Product matching: can the bot find this product?
        catalog_match, match_score = find_best_catalog_match(customer_said, catalog)

        # Check if the catalog match is the RIGHT product
        correct_product = False
        if catalog_match and catalog_match["item_code"] == sap_code:
            correct_product = True
        elif catalog_match:
            # Check if close enough by name
            if fuzzy_score(catalog_match["item_name"], sap_desc) > 0.7:
                correct_product = True

        # Quantity analysis
        raw_qty, raw_unit = extract_quantity_and_unit(customer_said)

        # Use the correct catalog item (the SAP one) for conversion
        if cat_item:
            converted_qty, conversion_note = convert_to_pcs(
                raw_qty, raw_unit, cat_item) if raw_qty else (None, "No qty extracted")
        else:
            converted_qty, conversion_note = None, "No catalog item found"

        # ── Determine prediction ──
        prediction = "AUTO"
        reason_parts = []

        # Product assessment
        if match_score < 0.3:
            prediction = "HIL"
            reason_parts.append(
                f"PRODUCT: Low match score ({match_score:.0%}). "
                f"Customer said '{customer_said}' — hard to match to "
                f"'{sap_desc}'")
        elif not correct_product and match_score < 0.5:
            prediction = "HIL"
            reason_parts.append(
                f"PRODUCT: Best match is wrong product. "
                f"Matched '{catalog_match['item_name'] if catalog_match else 'None'}' "
                f"(score {match_score:.0%}) but SAP has '{sap_desc}'")
        elif not correct_product:
            # The bot might find a wrong product but it's close-ish
            # With historical patterns and context, the bot might figure it out
            if sap_code in hist_by_code:
                # Customer orders this frequently — bot should get it from history
                reason_parts.append(
                    f"Product match uncertain ({match_score:.0%}) but customer "
                    f"orders this item frequently "
                    f"({hist_by_code[sap_code]['order_count']} times)")
            else:
                prediction = "HIL"
                reason_parts.append(
                    f"PRODUCT: Fuzzy match points to wrong item. "
                    f"Best='{catalog_match['item_name'] if catalog_match else 'None'}' "
                    f"vs SAP='{sap_desc}'")

        # Ship-to assessment (only flag if multi-address scenario)
        if len(ship_to_addrs) > 1:
            # Check if customer mentioned a location in this order
            # Look at the message context for location info
            msg_has_location = False
            if best_line and best_line.get("location"):
                msg_has_location = True
            elif customer_locations:
                msg_has_location = True

            if not msg_has_location and len(set(
                    i["ship_to_code"] for i in sap_truth)) > 1:
                # Multiple ship-tos in SAP, no location in message
                if prediction != "HIL":
                    prediction = "HIL"
                reason_parts.append(
                    f"SHIP_TO: Multiple addresses available, "
                    f"no clear location in message. Need to determine: "
                    f"'{sap_ship}'")

        # Quantity assessment
        if prediction != "HIL":
            if raw_qty is None:
                # No quantity extracted — this could be "1 box" implied
                if sap_qty == 1:
                    reason_parts.append("Qty: implicit 1 unit — should resolve")
                else:
                    prediction = "CHECK"
                    reason_parts.append(
                        f"QTY: Cannot extract quantity from "
                        f"'{customer_said}'. SAP has {sap_qty}")
            elif converted_qty is not None:
                # Compare converted qty with SAP qty
                tolerance = 0.05  # 5% tolerance
                if abs(converted_qty - sap_qty) <= sap_qty * tolerance:
                    reason_parts.append(
                        f"Qty: {conversion_note} ✓ matches SAP ({sap_qty})")
                else:
                    prediction = "CHECK"
                    reason_parts.append(
                        f"QTY MISMATCH: {conversion_note} → {converted_qty} "
                        f"PCS but SAP has {sap_qty} PCS")
            else:
                prediction = "CHECK"
                reason_parts.append(
                    f"QTY: Conversion failed — {conversion_note}. "
                    f"SAP has {sap_qty}")

        if prediction == "AUTO" and not reason_parts:
            reason_parts.append(
                f"Product matched ({match_score:.0%}), "
                f"qty converts correctly, ship-to clear")

        line_items.append({
            "scenario_id": f"S{sid:02d}",
            "difficulty": difficulty,
            "chat_name": chat_name,
            "customer_said": customer_said,
            "source_message": source_msg[:200] if source_msg else "",
            "sender": msg_sender,
            "time": msg_time,
            "sap_item_code": sap_code,
            "sap_description": sap_desc,
            "sap_qty": sap_qty,
            "sap_ship_to": sap_ship,
            "catalog_match": catalog_match["item_name"] if catalog_match else "NOT FOUND",
            "match_score": f"{match_score:.0%}",
            "raw_qty": raw_qty if raw_qty else "",
            "raw_unit": raw_unit if raw_unit else "",
            "converted_qty": converted_qty if converted_qty else "",
            "conversion_note": conversion_note,
            "prediction": prediction,
            "reason": "; ".join(reason_parts),
        })


# ── Build Excel workbook ───────────────────────────────────────────────────

wb = Workbook()

# Styles
header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
header_fill = PatternFill(start_color="2F4F4F", end_color="2F4F4F",
                          fill_type="solid")
wrap_align = Alignment(wrap_text=True, vertical="top")
top_align = Alignment(vertical="top")
thin_border = Border(
    left=Side(style="thin"),
    right=Side(style="thin"),
    top=Side(style="thin"),
    bottom=Side(style="thin"),
)

auto_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE",
                        fill_type="solid")
hil_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE",
                       fill_type="solid")
check_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C",
                         fill_type="solid")

easy_fill = PatternFill(start_color="E2EFDA", end_color="E2EFDA",
                        fill_type="solid")
medium_fill = PatternFill(start_color="FFF2CC", end_color="FFF2CC",
                          fill_type="solid")
hard_fill = PatternFill(start_color="FCE4EC", end_color="FCE4EC",
                        fill_type="solid")

diff_fills = {"EASY": easy_fill, "MEDIUM": medium_fill, "HARD": hard_fill}
pred_fills = {"AUTO": auto_fill, "HIL": hil_fill, "CHECK": check_fill}


def style_header(ws, ncols):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(wrap_text=True, vertical="center")
        cell.border = thin_border


# ── Sheet 1: Test Cases ──

ws1 = wb.active
ws1.title = "Test Cases"

tc_headers = [
    "Scenario", "Difficulty", "Chat Name", "Date", "Customer",
    "Ship-to Options", "Raw Messages (Customer Sent)",
    "Customer Style Notes", "Expected Items (SAP Truth)",
    "Special Instructions", "Order Lines Extracted", "SAP Items Total"
]
ws1.append(tc_headers)
style_header(ws1, len(tc_headers))

for tc in test_cases:
    row = [
        tc["scenario_id"], tc["difficulty"], tc["chat_name"], tc["date"],
        tc["customer"], tc["ship_to_options"], tc["raw_messages"],
        tc["customer_style_notes"], tc["expected_items"],
        tc["special_instructions"], tc["order_line_count"],
        tc["sap_item_count"],
    ]
    ws1.append(row)

# Format test cases
for row in ws1.iter_rows(min_row=2, max_row=ws1.max_row):
    diff = row[1].value
    if diff in diff_fills:
        for cell in row:
            cell.fill = diff_fills[diff]
    for cell in row:
        cell.alignment = wrap_align
        cell.border = thin_border

# Column widths
tc_widths = [8, 10, 25, 10, 30, 35, 80, 35, 80, 40, 12, 12]
for i, w in enumerate(tc_widths, 1):
    ws1.column_dimensions[get_column_letter(i)].width = w


# ── Sheet 2: Line Items ──

ws2 = wb.create_sheet("Line Items")

li_headers = [
    "Scenario", "Difficulty", "Chat Name",
    "Customer Said", "Source Message", "Sender", "Time",
    "SAP Item Code", "SAP Description", "SAP Qty (PCS)", "SAP Ship-to",
    "Catalog Match", "Match Score",
    "Raw Qty", "Raw Unit", "Converted Qty (PCS)", "Conversion Note",
    "Prediction", "Reason",
]
ws2.append(li_headers)
style_header(ws2, len(li_headers))

for li in line_items:
    row = [
        li["scenario_id"], li["difficulty"], li["chat_name"],
        li["customer_said"], li["source_message"], li["sender"], li["time"],
        li["sap_item_code"], li["sap_description"], li["sap_qty"],
        li["sap_ship_to"],
        li["catalog_match"], li["match_score"],
        li["raw_qty"], li["raw_unit"], li["converted_qty"],
        li["conversion_note"],
        li["prediction"], li["reason"],
    ]
    ws2.append(row)

# Format line items
for row in ws2.iter_rows(min_row=2, max_row=ws2.max_row):
    pred = row[17].value  # Prediction column (0-indexed: col 18)
    if pred in pred_fills:
        row[17].fill = pred_fills[pred]
    diff = row[1].value
    for cell in row:
        cell.alignment = wrap_align
        cell.border = thin_border

# Column widths
li_widths = [8, 10, 20, 40, 50, 20, 10, 20, 35, 10, 35, 35, 10,
             8, 8, 12, 35, 10, 60]
for i, w in enumerate(li_widths, 1):
    ws2.column_dimensions[get_column_letter(i)].width = w


# ── Sheet 3: Summary ──

ws3 = wb.create_sheet("Summary")

# Per-scenario summary
sum_headers = [
    "Scenario", "Difficulty", "Chat Name", "Customer", "Date",
    "Items in WhatsApp", "Items in SAP (total)",
    "AUTO", "HIL", "CHECK",
    "AUTO %", "HIL %", "CHECK %",
    "HIL Reasons",
]
ws3.append(sum_headers)
style_header(ws3, len(sum_headers))

# Calculate per-scenario stats
scenario_stats = defaultdict(lambda: {
    "auto": 0, "hil": 0, "check": 0, "total": 0, "hil_reasons": []
})

for li in line_items:
    sid = li["scenario_id"]
    pred = li["prediction"]
    scenario_stats[sid]["total"] += 1
    scenario_stats[sid][pred.lower()] += 1
    if pred == "HIL":
        scenario_stats[sid]["hil_reasons"].append(li["reason"][:50])

for tc in test_cases:
    sid = tc["scenario_id"]
    stats = scenario_stats[sid]
    total = stats["total"] if stats["total"] > 0 else 1
    hil_reasons = "; ".join(set(stats["hil_reasons"])) if stats["hil_reasons"] else ""

    row = [
        sid, tc["difficulty"], tc["chat_name"], tc["customer"], tc["date"],
        stats["total"], tc["sap_item_count"],
        stats["auto"], stats["hil"], stats["check"],
        f"{stats['auto']/total:.0%}",
        f"{stats['hil']/total:.0%}",
        f"{stats['check']/total:.0%}",
        hil_reasons,
    ]
    ws3.append(row)

# Overall totals
total_auto = sum(s["auto"] for s in scenario_stats.values())
total_hil = sum(s["hil"] for s in scenario_stats.values())
total_check = sum(s["check"] for s in scenario_stats.values())
grand_total = total_auto + total_hil + total_check

ws3.append([])
ws3.append([
    "TOTAL", "", "", "", "",
    grand_total, sum(tc["sap_item_count"] for tc in test_cases),
    total_auto, total_hil, total_check,
    f"{total_auto/grand_total:.0%}" if grand_total else "0%",
    f"{total_hil/grand_total:.0%}" if grand_total else "0%",
    f"{total_check/grand_total:.0%}" if grand_total else "0%",
    "",
])

# Format summary
for row in ws3.iter_rows(min_row=2, max_row=ws3.max_row):
    diff = row[1].value
    if diff in diff_fills:
        for cell in row:
            cell.fill = diff_fills[diff]
    for cell in row:
        cell.alignment = wrap_align
        cell.border = thin_border

sum_widths = [8, 10, 25, 30, 10, 12, 12, 8, 8, 8, 8, 8, 8, 60]
for i, w in enumerate(sum_widths, 1):
    ws3.column_dimensions[get_column_letter(i)].width = w

# Bold the total row
total_row_num = ws3.max_row
for cell in ws3[total_row_num]:
    cell.font = Font(bold=True)

# ── Save ──
output_path = "Conversation_Benchmark.xlsx"
wb.save(output_path)

print(f"\nBenchmark saved to: {output_path}")
print(f"\nOverall stats:")
print(f"  Scenarios: {len(test_cases)}")
print(f"  Line items analyzed: {grand_total}")
print(f"  AUTO: {total_auto} ({total_auto/grand_total:.0%})")
print(f"  HIL:  {total_hil} ({total_hil/grand_total:.0%})")
print(f"  CHECK: {total_check} ({total_check/grand_total:.0%})")
print(f"\nPer-scenario breakdown:")
for tc in test_cases:
    sid = tc["scenario_id"]
    s = scenario_stats[sid]
    t = s["total"] if s["total"] > 0 else 1
    print(f"  {sid} ({tc['difficulty']:6s}) {tc['chat_name']:30s} "
          f"| Items: {s['total']:2d} "
          f"| AUTO: {s['auto']:2d} ({s['auto']/t:.0%}) "
          f"| HIL: {s['hil']:2d} ({s['hil']/t:.0%}) "
          f"| CHECK: {s['check']:2d} ({s['check']/t:.0%})")
