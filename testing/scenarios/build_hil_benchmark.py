#!/usr/bin/env python3
"""
HIL Benchmark Builder
=====================
For each of the 25 scenarios, analyzes every customer message line
against the product catalog and SAP ground truth to determine:
  - What CAN be matched by an LLM (automatable)
  - What CANNOT be matched and needs Human-in-Loop (HIL)
  - WHY it needs HIL (category + detailed reason)

HIL categories:
  CUSTOMER_ID  — Cannot determine which customer/CardCode
  SHIP_TO      — Cannot determine which delivery address
  PRODUCT      — Cannot match product to catalog
  QUANTITY     — Cannot determine correct quantity / conversion
  INSTRUCTION  — Ambiguous instruction (hold, cancel, modify)
  DATA_GAP     — SAP has items not mentioned in WhatsApp at all
  LANGUAGE     — Non-English text that may contain order info

Output: Excel workbook with:
  Sheet 1: HIL Summary per scenario
  Sheet 2: Detailed HIL items (every flag with reason)
  Sheet 3: Automatable items (what LLM should get right)
"""

import json
import os
import re
from difflib import SequenceMatcher
from datetime import datetime

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except ImportError:
    print("ERROR: pip install openpyxl")
    exit(1)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def fuzzy(s1, s2):
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def load_data():
    with open(os.path.join(SCRIPT_DIR, "test_scenarios.json")) as f:
        scenarios = json.load(f)["scenarios"]
    with open(os.path.join(SCRIPT_DIR, "product_catalog.json")) as f:
        catalog = json.load(f)
    catalog_by_code = {p["item_code"]: p for p in catalog}
    return scenarios, catalog, catalog_by_code


def extract_order_lines_from_message(text):
    """Split a multi-line customer message into individual order line candidates."""
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip lines that are clearly not orders
        lower = line.lower()
        if lower in ("ok", "yes", "no", "done", "sent", "👆🏻", "🤔"):
            continue
        if line.startswith("@"):
            # Could be a mention with an order instruction
            pass
        lines.append(line)
    return lines


def find_best_catalog_match(text, catalog, catalog_by_code, historical_patterns=None):
    """Try to match a customer text line to a product in the catalog.
    Returns (item_code, item_name, score, match_type) or None."""

    text_lower = text.lower()

    # Remove quantity patterns to get product name
    product_text = re.sub(
        r'\b\d+\s*(kg|pcs|btl|btls|box|case|cases|nos|pkt|pkts|tin|tins|pieces|crate|crates|bottles)\b',
        '', text_lower
    ).strip()
    product_text = re.sub(r'^\d+\s*', '', product_text).strip()
    product_text = re.sub(r'\s*-\s*\d+.*$', '', product_text).strip()
    product_text = re.sub(r'\s+\d+\s*$', '', product_text).strip()

    if not product_text or len(product_text) < 2:
        return None

    best_score = 0
    best_match = None

    # Check historical patterns first (these are what customer typically orders)
    if historical_patterns:
        for h in historical_patterns:
            h_name = h["item_name"].lower()
            score = fuzzy(product_text, h_name)
            # Also check key words
            words = [w for w in product_text.split() if len(w) > 2]
            word_hits = sum(1 for w in words if w in h_name)
            word_score = word_hits / max(len(words), 1)
            combined = max(score, word_score * 0.8)
            if combined > best_score:
                best_score = combined
                best_match = (h["item_code"], h["item_name"], combined, "historical")

    # Check full catalog
    for p in catalog:
        p_name = p["item_name"].lower()
        score = fuzzy(product_text, p_name)
        words = [w for w in product_text.split() if len(w) > 2]
        word_hits = sum(1 for w in words if w in p_name)
        word_score = word_hits / max(len(words), 1)
        combined = max(score, word_score * 0.8)
        if combined > best_score:
            best_score = combined
            best_match = (p["item_code"], p["item_name"], combined, "catalog")

    if best_match and best_score >= 0.35:
        return best_match
    return None


def analyze_scenario(scenario, catalog, catalog_by_code):
    """Analyze a single scenario for HIL requirements.
    Returns list of dicts, each representing an item assessment."""

    sid = scenario["scenario_id"]
    results = []
    hist_patterns = scenario.get("historical_patterns", [])

    # Collect all customer messages
    customer_messages = []
    for conv in scenario["conversations_1to1"]:
        for m in conv["messages"]:
            if m["role"] == "customer":
                customer_messages.append({
                    "sender": conv["sender"],
                    "time": m["time"],
                    "text": m["text"],
                    "type": m.get("type", ""),
                })

    # SAP truth items
    sap_truth = scenario["sap_truth"]
    ship_to_addresses = scenario["ship_to_addresses"]

    # --- 1. Check each customer message line for product matchability ---
    all_message_lines = []
    for msg in customer_messages:
        lines = extract_order_lines_from_message(msg["text"])
        for line in lines:
            all_message_lines.append({
                "sender": msg["sender"],
                "time": msg["time"],
                "text": line,
                "full_message": msg["text"],
                "msg_type": msg["type"],
            })

    # For each message line, try to match to catalog
    for ml in all_message_lines:
        text = ml["text"]
        text_lower = text.lower()

        # Skip non-order lines
        is_location_only = bool(re.match(
            r'^[A-Za-z\s]+(area|location|gymkhana|parel|bandra|worli|malad|jogeshwari|goregaon|dadar|kurla|vashi|powai|santacruz|nerul).*$',
            text_lower
        ))

        is_operational = text_lower in (
            "ok", "yes", "no", "done", "sent", "hold", "ho",
        ) or text_lower.startswith("@") and "add" not in text_lower

        is_marathi_hindi = bool(re.search(r'\b(aaj|udya|bhetel|materiel|kela|hota|pathvly|argent|bga|sutti|dya|nahi|baghitale|cancel\s*kar|bhejna|kal)\b', text_lower))

        has_quantity = bool(re.search(r'\d+\s*(kg|pcs|btl|box|case|nos|pkt|tin|pieces|crate|bottles?)', text_lower))
        has_number = bool(re.search(r'\d+', text))

        # Check for special instructions
        is_cancel = bool(re.search(r'\b(cancel|remove|delete|hatao)\b', text_lower))
        is_hold = text_lower.strip() in ("hold", "stop", "wait", "ruk")
        is_add = bool(re.search(r'\b(add|pls add|plz add|please add)\b', text_lower))
        is_billing_query = bool(re.search(r'\b(billing|bill|payment|invoice|reliance)\b', text_lower))

        # Try product match
        match = find_best_catalog_match(text, catalog, catalog_by_code, hist_patterns)

        # --- Determine HIL status ---
        if is_hold:
            results.append({
                "scenario_id": sid,
                "dimension": "INSTRUCTION",
                "status": "HIL",
                "customer_text": text,
                "time": ml["time"],
                "sender": ml["sender"],
                "matched_item_code": "",
                "matched_item_name": "",
                "match_score": 0,
                "sap_item_code": "",
                "sap_description": "",
                "sap_qty": "",
                "reason": "Ambiguous 'Hold' instruction — unclear if customer wants to cancel order, "
                          "pause shipment, or hold for more items. Must escalate to human.",
            })
        elif is_cancel:
            # Cancellation — need to figure out WHAT to cancel
            cancel_target = re.sub(r'\b(please|pls|plz|cancel|remove|delete|hatao|kar.*)\b', '', text_lower).strip()
            if cancel_target and len(cancel_target) > 2:
                match_cancel = find_best_catalog_match(cancel_target, catalog, catalog_by_code, hist_patterns)
                if match_cancel and match_cancel[2] >= 0.5:
                    results.append({
                        "scenario_id": sid,
                        "dimension": "INSTRUCTION",
                        "status": "AUTO",
                        "customer_text": text,
                        "time": ml["time"],
                        "sender": ml["sender"],
                        "matched_item_code": match_cancel[0],
                        "matched_item_name": match_cancel[1],
                        "match_score": round(match_cancel[2], 2),
                        "sap_item_code": "",
                        "sap_description": "",
                        "sap_qty": "",
                        "reason": f"Cancellation detected. Product '{cancel_target}' matched to catalog.",
                    })
                else:
                    results.append({
                        "scenario_id": sid,
                        "dimension": "INSTRUCTION",
                        "status": "HIL",
                        "customer_text": text,
                        "time": ml["time"],
                        "sender": ml["sender"],
                        "matched_item_code": "",
                        "matched_item_name": "",
                        "match_score": match_cancel[2] if match_cancel else 0,
                        "sap_item_code": "",
                        "sap_description": "",
                        "sap_qty": "",
                        "reason": f"Cancellation detected but product '{cancel_target}' could not be "
                                  f"confidently matched to catalog. Human needs to confirm which product.",
                    })
            else:
                results.append({
                    "scenario_id": sid,
                    "dimension": "INSTRUCTION",
                    "status": "HIL",
                    "customer_text": text,
                    "time": ml["time"],
                    "sender": ml["sender"],
                    "matched_item_code": "",
                    "matched_item_name": "",
                    "match_score": 0,
                    "sap_item_code": "",
                    "sap_description": "",
                    "sap_qty": "",
                    "reason": "Cancellation instruction but unclear what to cancel. "
                              "May be in Hindi/Marathi. Human review needed.",
                })
        elif is_billing_query:
            results.append({
                "scenario_id": sid,
                "dimension": "INSTRUCTION",
                "status": "HIL",
                "customer_text": text,
                "time": ml["time"],
                "sender": ml["sender"],
                "matched_item_code": "",
                "matched_item_name": "",
                "match_score": 0,
                "sap_item_code": "",
                "sap_description": "",
                "sap_qty": "",
                "reason": "Billing/payment query — not an order. Needs human to respond.",
            })
        elif is_location_only:
            # Location response — check if it matches a ship-to
            best_ship = 0
            best_ship_name = ""
            for addr in ship_to_addresses:
                score = fuzzy(text, addr)
                if score > best_ship:
                    best_ship = score
                    best_ship_name = addr
            if best_ship >= 0.3:
                results.append({
                    "scenario_id": sid,
                    "dimension": "SHIP_TO",
                    "status": "AUTO",
                    "customer_text": text,
                    "time": ml["time"],
                    "sender": ml["sender"],
                    "matched_item_code": "",
                    "matched_item_name": best_ship_name,
                    "match_score": round(best_ship, 2),
                    "sap_item_code": "",
                    "sap_description": "",
                    "sap_qty": "",
                    "reason": f"Location response matched to ship-to: {best_ship_name}",
                })
            else:
                results.append({
                    "scenario_id": sid,
                    "dimension": "SHIP_TO",
                    "status": "HIL",
                    "customer_text": text,
                    "time": ml["time"],
                    "sender": ml["sender"],
                    "matched_item_code": "",
                    "matched_item_name": "",
                    "match_score": round(best_ship, 2),
                    "sap_item_code": "",
                    "sap_description": "",
                    "sap_qty": "",
                    "reason": f"Location '{text}' does not match any known ship-to address. "
                              f"Best match: {best_ship_name} ({best_ship:.0%}). Human needs to confirm.",
                })
        elif is_marathi_hindi and not has_quantity:
            results.append({
                "scenario_id": sid,
                "dimension": "LANGUAGE",
                "status": "HIL",
                "customer_text": text,
                "time": ml["time"],
                "sender": ml["sender"],
                "matched_item_code": "",
                "matched_item_name": "",
                "match_score": 0,
                "sap_item_code": "",
                "sap_description": "",
                "sap_qty": "",
                "reason": "Non-English (Marathi/Hindi) text without clear quantity/product pattern. "
                          "May contain order info or operational chatter. Human must interpret.",
            })
        elif is_operational and not has_quantity:
            # Skip — not an order line
            continue
        elif match and match[2] >= 0.5:
            # Good match — automatable
            results.append({
                "scenario_id": sid,
                "dimension": "PRODUCT",
                "status": "AUTO",
                "customer_text": text,
                "time": ml["time"],
                "sender": ml["sender"],
                "matched_item_code": match[0],
                "matched_item_name": match[1],
                "match_score": round(match[2], 2),
                "sap_item_code": "",
                "sap_description": "",
                "sap_qty": "",
                "reason": f"Product matched via {match[3]} lookup (score: {match[2]:.0%})",
            })
        elif match and match[2] >= 0.35:
            # Weak match — needs HIL
            results.append({
                "scenario_id": sid,
                "dimension": "PRODUCT",
                "status": "HIL",
                "customer_text": text,
                "time": ml["time"],
                "sender": ml["sender"],
                "matched_item_code": match[0],
                "matched_item_name": match[1],
                "match_score": round(match[2], 2),
                "sap_item_code": "",
                "sap_description": "",
                "sap_qty": "",
                "reason": f"Weak product match (score: {match[2]:.0%}). Customer said '{text}', "
                          f"best catalog match is '{match[1]}'. Too uncertain — human should confirm.",
            })
        elif has_quantity or has_number:
            # Has quantity but no product match — HIL
            results.append({
                "scenario_id": sid,
                "dimension": "PRODUCT",
                "status": "HIL",
                "customer_text": text,
                "time": ml["time"],
                "sender": ml["sender"],
                "matched_item_code": "",
                "matched_item_name": "",
                "match_score": 0,
                "sap_item_code": "",
                "sap_description": "",
                "sap_qty": "",
                "reason": f"Order line detected (has quantity) but product '{text}' could not be "
                          f"matched to any item in the catalog. Human must identify the product.",
            })

    # --- 2. Check SAP truth items that have NO corresponding WhatsApp message ---
    # These are items in SAP that were ordered through other channels or batched
    matched_sap_indices = set()
    for r in results:
        if r["status"] == "AUTO" and r["matched_item_code"]:
            for i, st in enumerate(sap_truth):
                if st["item_code"] == r["matched_item_code"]:
                    matched_sap_indices.add(i)
                    r["sap_item_code"] = st["item_code"]
                    r["sap_description"] = st["description"]
                    r["sap_qty"] = st["quantity"]
                    break

    for i, st in enumerate(sap_truth):
        if i in matched_sap_indices:
            continue
        # Check if ANY message line mentions this product
        found_in_msg = False
        for ml in all_message_lines:
            score = fuzzy(ml["text"].lower(), st["description"].lower())
            # Also check item code keywords
            words = st["description"].lower().split()[:3]
            word_hits = sum(1 for w in words if len(w) > 3 and w in ml["text"].lower())
            if score >= 0.4 or word_hits >= 2:
                found_in_msg = True
                break

        if not found_in_msg:
            results.append({
                "scenario_id": sid,
                "dimension": "DATA_GAP",
                "status": "HIL",
                "customer_text": "(not mentioned in WhatsApp)",
                "time": "",
                "sender": "",
                "matched_item_code": "",
                "matched_item_name": "",
                "match_score": 0,
                "sap_item_code": st["item_code"],
                "sap_description": st["description"],
                "sap_qty": st["quantity"],
                "reason": f"SAP has order for '{st['description']}' (qty:{st['quantity']}) "
                          f"but NO corresponding WhatsApp message found. "
                          f"This order came through another channel (phone call, repeat order, etc.). "
                          f"LLM cannot extract what was never sent via WhatsApp.",
            })

    # --- 3. Ship-to analysis ---
    sap_ship_tos = set(st.get("ship_to_code", "") for st in sap_truth if st.get("ship_to_code"))
    if len(sap_ship_tos) > 1:
        # Multiple ship-to addresses — check if messages contain location info
        has_location_in_msgs = False
        for ml in all_message_lines:
            for addr in ship_to_addresses:
                if fuzzy(ml["text"], addr) >= 0.3:
                    has_location_in_msgs = True
                    break
            if has_location_in_msgs:
                break

        if not has_location_in_msgs:
            results.append({
                "scenario_id": sid,
                "dimension": "SHIP_TO",
                "status": "HIL",
                "customer_text": "(no location specified)",
                "time": "",
                "sender": "",
                "matched_item_code": "",
                "matched_item_name": "",
                "match_score": 0,
                "sap_item_code": "",
                "sap_description": "",
                "sap_qty": "",
                "reason": f"SAP has {len(sap_ship_tos)} different ship-to addresses but customer "
                          f"messages don't clearly specify which items go where. "
                          f"Human must determine delivery routing.",
            })

    return results


def build_excel(all_results, scenarios):
    """Build Excel output with HIL benchmark."""
    wb = openpyxl.Workbook()

    # Styles
    hdr_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    body_font = Font(name="Arial", size=10)
    bold_font = Font(name="Arial", size=10, bold=True)
    title_font = Font(name="Arial", bold=True, size=14, color="1F4E79")
    thin = Border(left=Side("thin"), right=Side("thin"), top=Side("thin"), bottom=Side("thin"))

    hil_fill = PatternFill("solid", fgColor="FFC7CE")  # Red
    auto_fill = PatternFill("solid", fgColor="C6EFCE")  # Green
    easy_fill = PatternFill("solid", fgColor="C6EFCE")
    med_fill = PatternFill("solid", fgColor="FFEB9C")
    hard_fill = PatternFill("solid", fgColor="FFC7CE")
    diff_fills = {"EASY": easy_fill, "MEDIUM": med_fill, "HARD": hard_fill}

    dim_fills = {
        "PRODUCT": PatternFill("solid", fgColor="B4C6E7"),
        "SHIP_TO": PatternFill("solid", fgColor="FFD966"),
        "INSTRUCTION": PatternFill("solid", fgColor="F4B084"),
        "QUANTITY": PatternFill("solid", fgColor="D9E2F3"),
        "CUSTOMER_ID": PatternFill("solid", fgColor="E2EFDA"),
        "LANGUAGE": PatternFill("solid", fgColor="D6DCE4"),
        "DATA_GAP": PatternFill("solid", fgColor="FCE4D6"),
    }

    # ---- SHEET 1: Summary per scenario ----
    ws1 = wb.active
    ws1.title = "HIL Summary"
    ws1.merge_cells("A1:L1")
    ws1.cell(row=1, column=1, value="HIL Benchmark — Summary per Scenario").font = title_font
    ws1.cell(row=2, column=1, value=f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}").font = Font(name="Arial", size=9, italic=True, color="666666")

    headers1 = [
        "Scenario", "Difficulty", "Customer", "Date",
        "Total Items\nAnalyzed", "Automatable", "HIL\nRequired",
        "HIL %", "HIL Product", "HIL Ship-to", "HIL Instruction",
        "HIL Data Gap", "HIL Language",
    ]
    for c, h in enumerate(headers1, 1):
        cell = ws1.cell(row=4, column=c, value=h)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    row_num = 5
    for s in scenarios:
        sid = s["scenario_id"]
        items = [r for r in all_results if r["scenario_id"] == sid]
        auto = [r for r in items if r["status"] == "AUTO"]
        hil = [r for r in items if r["status"] == "HIL"]

        hil_product = sum(1 for r in hil if r["dimension"] == "PRODUCT")
        hil_ship = sum(1 for r in hil if r["dimension"] == "SHIP_TO")
        hil_instr = sum(1 for r in hil if r["dimension"] == "INSTRUCTION")
        hil_gap = sum(1 for r in hil if r["dimension"] == "DATA_GAP")
        hil_lang = sum(1 for r in hil if r["dimension"] == "LANGUAGE")
        hil_pct = len(hil) / max(len(items), 1)

        vals = [
            f"S{sid:02d}", s["difficulty"], s["chat_name"], s["date"],
            len(items), len(auto), len(hil),
            hil_pct, hil_product, hil_ship, hil_instr, hil_gap, hil_lang,
        ]
        for c, v in enumerate(vals, 1):
            cell = ws1.cell(row=row_num, column=c, value=v)
            cell.font = body_font
            cell.border = thin
        ws1.cell(row=row_num, column=2).fill = diff_fills.get(s["difficulty"], PatternFill())
        ws1.cell(row=row_num, column=8).number_format = "0.0%"

        # Color HIL % cell
        if hil_pct >= 0.5:
            ws1.cell(row=row_num, column=8).fill = hil_fill
        elif hil_pct <= 0.2:
            ws1.cell(row=row_num, column=8).fill = auto_fill

        row_num += 1

    # Totals row
    total_items = len(all_results)
    total_auto = sum(1 for r in all_results if r["status"] == "AUTO")
    total_hil = sum(1 for r in all_results if r["status"] == "HIL")
    ws1.cell(row=row_num, column=1, value="TOTAL").font = bold_font
    ws1.cell(row=row_num, column=5, value=total_items).font = bold_font
    ws1.cell(row=row_num, column=6, value=total_auto).font = bold_font
    ws1.cell(row=row_num, column=7, value=total_hil).font = bold_font
    c8 = ws1.cell(row=row_num, column=8, value=total_hil / max(total_items, 1))
    c8.font = bold_font
    c8.number_format = "0.0%"
    for c in range(1, 14):
        ws1.cell(row=row_num, column=c).border = thin

    for col, w in zip("ABCDEFGHIJKLM", [8, 10, 30, 10, 10, 10, 8, 8, 10, 10, 12, 10, 10]):
        ws1.column_dimensions[col].width = w

    # ---- SHEET 2: Detailed HIL items ----
    ws2 = wb.create_sheet("HIL Details")
    ws2.merge_cells("A1:J1")
    ws2.cell(row=1, column=1, value="HIL Items — Detailed (every item requiring Human-in-Loop)").font = title_font

    headers2 = [
        "Scenario", "Difficulty", "Dimension", "Customer Said",
        "Time", "Best Catalog Match", "Match Score",
        "SAP Item", "SAP Qty", "Reason (Why HIL)",
    ]
    for c, h in enumerate(headers2, 1):
        cell = ws2.cell(row=3, column=c, value=h)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    hil_items = [r for r in all_results if r["status"] == "HIL"]
    for r_idx, r in enumerate(hil_items, 4):
        s = next((sc for sc in scenarios if sc["scenario_id"] == r["scenario_id"]), {})
        vals = [
            f"S{r['scenario_id']:02d}",
            s.get("difficulty", ""),
            r["dimension"],
            r["customer_text"][:80],
            r["time"],
            r["matched_item_name"][:50] if r["matched_item_name"] else "",
            r["match_score"],
            (r["sap_description"][:50] if r["sap_description"] else ""),
            r["sap_qty"],
            r["reason"],
        ]
        for c, v in enumerate(vals, 1):
            cell = ws2.cell(row=r_idx, column=c, value=v)
            cell.font = body_font
            cell.border = thin
        ws2.cell(row=r_idx, column=2).fill = diff_fills.get(s.get("difficulty", ""), PatternFill())
        ws2.cell(row=r_idx, column=3).fill = dim_fills.get(r["dimension"], PatternFill())
        ws2.cell(row=r_idx, column=7).number_format = "0%"
        ws2.cell(row=r_idx, column=10).alignment = Alignment(wrap_text=True)

    for col, w in zip("ABCDEFGHIJ", [8, 10, 14, 50, 10, 40, 8, 40, 8, 70]):
        ws2.column_dimensions[col].width = w

    # ---- SHEET 3: Automatable items ----
    ws3 = wb.create_sheet("Automatable Items")
    ws3.merge_cells("A1:I1")
    ws3.cell(row=1, column=1, value="Automatable Items — What LLM should get right").font = title_font

    headers3 = [
        "Scenario", "Difficulty", "Dimension", "Customer Said",
        "Time", "Matched Item", "Match Score",
        "SAP Item", "Reason",
    ]
    for c, h in enumerate(headers3, 1):
        cell = ws3.cell(row=3, column=c, value=h)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    auto_items = [r for r in all_results if r["status"] == "AUTO"]
    for r_idx, r in enumerate(auto_items, 4):
        s = next((sc for sc in scenarios if sc["scenario_id"] == r["scenario_id"]), {})
        vals = [
            f"S{r['scenario_id']:02d}",
            s.get("difficulty", ""),
            r["dimension"],
            r["customer_text"][:80],
            r["time"],
            r["matched_item_name"][:50] if r["matched_item_name"] else "",
            r["match_score"],
            r["sap_description"][:50] if r["sap_description"] else "",
            r["reason"][:80],
        ]
        for c, v in enumerate(vals, 1):
            cell = ws3.cell(row=r_idx, column=c, value=v)
            cell.font = body_font
            cell.border = thin
        ws3.cell(row=r_idx, column=2).fill = diff_fills.get(s.get("difficulty", ""), PatternFill())
        ws3.cell(row=r_idx, column=7).number_format = "0%"

    for col, w in zip("ABCDEFGHI", [8, 10, 14, 50, 10, 40, 8, 40, 50]):
        ws3.column_dimensions[col].width = w

    # Save
    outpath = os.path.join(SCRIPT_DIR, "HIL_Benchmark.xlsx")
    wb.save(outpath)
    print(f"\nSaved: {outpath}")
    return outpath


def main():
    print("=" * 60)
    print("  HIL Benchmark Builder")
    print("  Analyzing 25 scenarios for Human-in-Loop requirements")
    print("=" * 60)

    scenarios, catalog, catalog_by_code = load_data()

    all_results = []
    for s in scenarios:
        print(f"  S{s['scenario_id']:02d} [{s['difficulty']:<6}] {s['chat_name']:<30} ... ", end="", flush=True)
        results = analyze_scenario(s, catalog, catalog_by_code)
        auto_count = sum(1 for r in results if r["status"] == "AUTO")
        hil_count = sum(1 for r in results if r["status"] == "HIL")
        print(f"Items:{len(results):>3} | Auto:{auto_count:>3} | HIL:{hil_count:>3}")
        all_results.extend(results)

    total_auto = sum(1 for r in all_results if r["status"] == "AUTO")
    total_hil = sum(1 for r in all_results if r["status"] == "HIL")
    print(f"\n  TOTAL: {len(all_results)} items | {total_auto} automatable | {total_hil} HIL")
    print(f"  HIL Rate: {total_hil / max(len(all_results), 1):.1%}")

    # Breakdown by dimension
    from collections import Counter
    hil_by_dim = Counter(r["dimension"] for r in all_results if r["status"] == "HIL")
    print(f"\n  HIL Breakdown:")
    for dim, count in hil_by_dim.most_common():
        print(f"    {dim:<15} {count:>3}")

    outpath = build_excel(all_results, scenarios)
    print(f"\n  Done! Open: {outpath}")


if __name__ == "__main__":
    main()
