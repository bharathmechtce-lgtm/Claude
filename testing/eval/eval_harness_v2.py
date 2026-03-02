#!/usr/bin/env python3
"""
WhatsApp Order Bot — Multi-Model Eval Harness v2
Now with enriched product catalogue (SalPackUn + BWeight1), conversion rules,
historical order patterns, 6 models, and improved scoring.
"""

import json
import time
import os
import re
import requests
import pandas as pd
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
import statistics

# ============================================================
# API KEYS
# ============================================================
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

# ============================================================
# MODEL CONFIGS
# ============================================================
MODELS = [
    {
        "name": "Claude Opus 4.6",
        "provider": "anthropic",
        "model_id": "claude-opus-4-6",
        "input_cost": 5.0,
        "output_cost": 25.0,
    },
    {
        "name": "Claude Sonnet 4.5",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5-20250929",
        "input_cost": 3.0,
        "output_cost": 15.0,
    },
    {
        "name": "Claude Haiku 4.5",
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5-20251001",
        "input_cost": 0.80,
        "output_cost": 4.0,
    },
    {
        "name": "GPT-4o",
        "provider": "openai",
        "model_id": "gpt-4o",
        "input_cost": 2.50,
        "output_cost": 10.0,
    },
    {
        "name": "GPT-5.2",
        "provider": "openai",
        "model_id": "gpt-5.2",
        "input_cost": 1.75,
        "output_cost": 14.0,
    },
    {
        "name": "Gemini 2.0 Flash",
        "provider": "google",
        "model_id": "gemini-2.0-flash",
        "input_cost": 0.10,
        "output_cost": 0.40,
    },
    {
        "name": "Gemini 2.5 Flash",
        "provider": "google",
        "model_id": "gemini-2.5-flash",
        "input_cost": 0.30,
        "output_cost": 2.50,
    },
]

# ============================================================
# API CALLERS
# ============================================================
def call_anthropic(model_id, system_prompt, user_prompt):
    payload = {
        "model": model_id,
        "max_tokens": 8192,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=payload,
        timeout=300,  # Opus can be slower
    )
    data = resp.json()
    if resp.status_code != 200:
        return None, 0, 0, f"Error {resp.status_code}: {data.get('error', {}).get('message', str(data))}"
    # Extract text from response (may have multiple content blocks with thinking models)
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]
    input_tokens = data["usage"]["input_tokens"]
    output_tokens = data["usage"]["output_tokens"]
    return text, input_tokens, output_tokens, None


def call_openai(model_id, system_prompt, user_prompt):
    # GPT-5.x are reasoning models — need max_completion_tokens + developer role
    is_reasoning = model_id.startswith("gpt-5")

    payload = {
        "model": model_id,
        "messages": [
            {"role": "developer" if is_reasoning else "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }

    if is_reasoning:
        payload["max_completion_tokens"] = 16384
        # Let GPT-5.2 use full reasoning — it needs to reason about fuzzy matching,
        # unit conversions, corrections, cancellations, multilingual chatter, etc.
    else:
        payload["max_tokens"] = 8192

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,  # reasoning models can be slower
    )
    data = resp.json()
    if resp.status_code != 200:
        return None, 0, 0, f"Error {resp.status_code}: {data.get('error', {}).get('message', str(data))}"
    text = data["choices"][0]["message"]["content"]
    input_tokens = data["usage"]["prompt_tokens"]
    output_tokens = data["usage"]["completion_tokens"]
    return text, input_tokens, output_tokens, None


def call_google(model_id, system_prompt, user_prompt):
    gen_config = {"maxOutputTokens": 8192}
    # Gemini 2.5 Flash has thinking enabled by default (budget=8192 tokens)
    # No need to set thinkingConfig — default is optimal

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": gen_config,
        },
        timeout=300,
    )
    data = resp.json()
    if resp.status_code != 200:
        err = data.get("error", {}).get("message", str(data))
        return None, 0, 0, f"Error {resp.status_code}: {err}"
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    usage = data.get("usageMetadata", {})
    input_tokens = usage.get("promptTokenCount", 0)
    output_tokens = usage.get("candidatesTokenCount", 0)
    return text, input_tokens, output_tokens, None


def call_model(model_config, system_prompt, user_prompt):
    provider = model_config["provider"]
    model_id = model_config["model_id"]
    if provider == "anthropic":
        return call_anthropic(model_id, system_prompt, user_prompt)
    elif provider == "openai":
        return call_openai(model_id, system_prompt, user_prompt)
    elif provider == "google":
        return call_google(model_id, system_prompt, user_prompt)


# ============================================================
# SYSTEM PROMPT (v2 — with conversion rules)
# ============================================================
SYSTEM_PROMPT = """You are an AI order processing engine for TJUK, a food distribution company in Mumbai.
Your job: read WhatsApp messages and extract structured order data.

You will receive:
1. CUSTOMER CONTEXT — who this WhatsApp group belongs to (CardCode, CardName, ship-to addresses)
2. PRODUCT CATALOGUE — products with pack sizes and weights for unit conversion
3. HISTORICAL ORDER PATTERNS — what this customer typically orders and in what quantities
4. WHATSAPP MESSAGES — the raw messages to process

CRITICAL — QUANTITY CONVERSION RULES:
All SAP orders are recorded in PCS (pieces). Customers speak in cases, boxes, kg, etc. You MUST convert:

CASE/BOX CONVERSION: If customer says "X case/box/crate" of a product:
  → quantity = X × PackSize (units per case from catalogue)
  → Example: "3 case coke can 300ml" and PackSize=24 → quantity = 72 PCS

WEIGHT CONVERSION: If customer says "X kg" of a product:
  → quantity = X ÷ UnitWeight (weight per unit in KG from catalogue)
  → Example: "5kg amul butter 500gms" and UnitWeight=0.5 → quantity = 10 PCS

DIRECT PCS: If customer says "X pcs/pieces/nos/units" or just a number:
  → quantity = X PCS (no conversion needed)

BOTTLE/PACKET: "X btl/pkt" usually means X PCS directly.

SANITY CHECK: Compare your converted quantity against the Historical Order Patterns.
If your quantity is far outside the customer's typical range, flag it for review.

Your task:
- Identify which messages are actual orders (ignore acknowledgments, operational chatter, stock queries, delivery updates)
- For each order, extract: which ship-to address, what products, what quantities
- Match product names from messages to the closest ItemCode in the catalogue
- Handle additions ("add", "pls add") by merging into the parent order
- Handle corrections/cancellations appropriately
- If a message mentions a specific outlet/restaurant name, match it to the right ShipToCode

Output ONLY valid JSON in this exact format:
{
  "orders": [
    {
      "ship_to": "SHIP-TO ADDRESS NAME or 'DEFAULT' if unclear",
      "lines": [
        {
          "item_code": "MATCHED_ITEM_CODE",
          "item_name": "MATCHED_ITEM_NAME",
          "quantity": 72,
          "uom": "PCS",
          "original_text": "what the customer actually wrote",
          "conversion_applied": "3 case × 24 pcs/case = 72 PCS",
          "confidence": "HIGH/MEDIUM/LOW",
          "flag": "GREEN/YELLOW/RED"
        }
      ]
    }
  ],
  "ignored_messages": [
    {"text": "message text", "reason": "why ignored"}
  ],
  "notes": "any ambiguities or issues found"
}

Rules:
- Match products using fuzzy matching — customers use abbreviations, typos, informal names
- "btl" = bottle, "pkt" = packet/pcs, "nos" = numbers/pcs, "pic" = pieces
- ALL quantities in output must be in PCS after conversion
- If you cannot confidently match a product, still include it with confidence=LOW and your best guess
- Do NOT invent products. Only match to items in the provided catalogue.
- If the catalogue doesn't have a match, set item_code to "UNKNOWN" and keep the original text

FLAG RULES:
- GREEN: Exact item match + quantity in historical range + HIGH confidence
- YELLOW: Unit conversion applied, or quantity slightly outside typical range, or name-only fuzzy match
- RED: UNKNOWN item, LOW confidence, quantity far outside range, or first-time product for customer"""


# ============================================================
# PROMPT BUILDER (v2 — enriched)
# ============================================================
def build_user_prompt(scenario_data, hist_ranges):
    ctx = scenario_data["context"]
    products = scenario_data["products"]
    messages = scenario_data["messages"]
    card_codes = [c.strip() for c in ctx["card_codes"].split(",")]

    prompt = "=== CUSTOMER CONTEXT ===\n"
    prompt += f"WhatsApp Group: {ctx['chat']}\n"
    prompt += f"Customer: {ctx['card_codes']} — {ctx['card_names']}\n"
    prompt += f"Ship-to Addresses:\n"
    for addr in ctx["ship_to"]:
        prompt += f"  - {addr}\n"

    # Enriched product catalogue with pack size and weight
    prompt += "\n=== PRODUCT CATALOGUE ===\n"
    prompt += "ItemCode | ItemName | UoM | PackSize (pcs/case) | UnitWeight (kg)\n"
    prompt += "-" * 90 + "\n"
    for _, p in products.iterrows():
        pack = p.get("SalPackUn", 1)
        weight = p.get("BWeight1", "")
        pack_str = str(int(pack)) if pd.notna(pack) else "1"
        weight_str = str(weight) if pd.notna(weight) and weight != "" else ""
        prompt += f"{p['ItemCode']} | {p['ItemName']} | {p['SalUnitMsr']} | {pack_str} | {weight_str}\n"

    # Historical order patterns
    prompt += "\n=== HISTORICAL ORDER PATTERNS ===\n"
    prompt += "Items this customer typically orders (sorted by frequency):\n"
    prompt += "ItemCode | ItemName | OrderCount | TypicalQty (min-max, median)\n"
    customer_hist = []
    for cc in card_codes:
        for (card, item), stats in hist_ranges.items():
            if card == cc:
                item_name = ""
                match = products[products["ItemCode"] == item]
                if len(match) > 0:
                    item_name = match.iloc[0]["ItemName"]
                else:
                    # Try from full products df
                    item_name = item
                customer_hist.append((item, item_name, stats))
    # Sort by order count descending
    customer_hist.sort(key=lambda x: x[2]["count"], reverse=True)
    for item_code, item_name, stats in customer_hist[:50]:  # Top 50 items
        prompt += (f"  {item_code} | {item_name[:40]} | "
                   f"{stats['count']}x | {stats['min']:.0f}-{stats['max']:.0f} "
                   f"(med {stats['median']:.0f})\n")
    if not customer_hist:
        prompt += "  No historical orders found for this customer.\n"

    prompt += "\n=== WHATSAPP MESSAGES (process these) ===\n"
    for m in messages:
        prompt += f"[{m['time']}] {m['sender']}: {m['message']}\n"

    return prompt


# ============================================================
# SCORING ENGINE (v2 — adjusted recall, improved matching)
# ============================================================
def fuzzy_match(s1, s2):
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def extract_json_from_response(text):
    """Extract JSON from model response, handling markdown code blocks."""
    if not text:
        return None
    # Try to find JSON in code blocks
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try to find raw JSON with balanced braces
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
                        return json.loads(text[start:i+1])
                    except json.JSONDecodeError:
                        pass
                    break
    return None


def check_chatter_for_missed_orders(ignored_messages, products_df):
    """Scan ignored messages for potential missed orders.
    If an ignored message mentions product-like terms with quantities, it might be a missed order."""
    import re as _re
    missed = 0
    product_names_lower = set()
    for _, p in products_df.iterrows():
        name = str(p["ItemName"]).lower()
        # Add key terms from product names (first 2 words)
        words = name.split()[:2]
        for w in words:
            if len(w) > 3:
                product_names_lower.add(w)

    qty_pattern = _re.compile(r'\b\d+\s*(pcs|case|box|kg|nos|btl|crate|pieces|packet|pkt)\b', _re.IGNORECASE)

    for msg in ignored_messages:
        text = ""
        if isinstance(msg, dict):
            text = msg.get("text", "")
        elif isinstance(msg, str):
            text = msg
        text_lower = text.lower()

        # Count product name hits
        product_hits = sum(1 for pn in product_names_lower if pn in text_lower)
        has_qty = bool(qty_pattern.search(text))

        if product_hits >= 2 and has_qty:
            missed += 1

    return missed


def score_result(model_output_json, sap_truth, products_df):
    """Score model output against SAP ground truth. Returns dict of metrics."""
    if not model_output_json or "orders" not in model_output_json:
        return {
            "valid_json": False,
            "recall_raw": 0, "recall_denominator": len(sap_truth),
            "adjusted_recall": 0.0,
            "products_correct": 0, "products_total": len(sap_truth),
            "products_accuracy": 0.0,
            "quantity_correct": 0, "quantity_accuracy": 0.0,
            "total_accuracy": 0.0,
            "extra_items": 0, "missed_items": len(sap_truth),
            "missed_in_chatter": 0,
            "model_line_count": 0, "ignored_count": 0,
        }

    # Flatten model output lines
    model_lines = []
    for order in model_output_json.get("orders", []):
        ship_to = order.get("ship_to", "DEFAULT")
        for line in order.get("lines", []):
            model_lines.append({
                "item_code": line.get("item_code", "UNKNOWN"),
                "item_name": line.get("item_name", ""),
                "quantity": line.get("quantity", 0),
                "uom": line.get("uom", ""),
                "confidence": line.get("confidence", "LOW"),
                "flag": line.get("flag", "RED"),
                "ship_to": ship_to,
            })

    # SAP truth lines
    sap_lines = []
    for _, row in sap_truth.iterrows():
        sap_lines.append({
            "item_code": row["ItemCode"],
            "item_name": row["Dscription"],
            "quantity": row["Quantity"],
            "ship_to": str(row.get("ShipToCode", "")),
        })

    # Check for missed orders in chatter
    ignored_messages = model_output_json.get("ignored_messages", [])
    missed_in_chatter = check_chatter_for_missed_orders(ignored_messages, products_df)

    # Adjusted recall
    # orders_found = number of model lines that matched something in SAP
    # We compute this after product matching below
    # For now, count SAP lines vs model lines matched

    # Match model lines to SAP lines
    matched_sap = set()
    products_correct = 0
    quantity_correct = 0

    for ml in model_lines:
        best_match = None
        best_score = 0

        for i, sl in enumerate(sap_lines):
            if i in matched_sap:
                continue
            # Exact item_code match
            if ml["item_code"] == sl["item_code"]:
                score = 1.0
            else:
                # Fuzzy match on item name
                score = fuzzy_match(ml.get("item_name", ""), sl["item_name"])
            if score > best_score:
                best_score = score
                best_match = i

        if best_match is not None and best_score >= 0.5:
            matched_sap.add(best_match)
            products_correct += 1
            # Check quantity (allow ±5% tolerance)
            sap_qty = sap_lines[best_match]["quantity"]
            model_qty = ml.get("quantity", 0)
            try:
                model_qty = float(model_qty)
            except (ValueError, TypeError):
                model_qty = 0
            if sap_qty > 0:
                diff_pct = abs(model_qty - sap_qty) / sap_qty
                if diff_pct <= 0.05:
                    quantity_correct += 1
            elif abs(model_qty - sap_qty) < 0.01:
                quantity_correct += 1

    products_total = len(sap_lines)
    extra_items = max(0, len(model_lines) - products_correct)
    missed_items = products_total - products_correct

    # Adjusted recall: of real orders present in input, how many did model find?
    # Denominator = items model found + items missed in chatter (not total SAP)
    recall_denominator = products_correct + missed_items
    if recall_denominator > 0:
        # Subtract items that had no WhatsApp data at all (we can't know this perfectly,
        # but we use: if model found + missed_in_chatter as the "real orders in input")
        adj_denominator = products_correct + missed_in_chatter
        adjusted_recall = products_correct / adj_denominator if adj_denominator > 0 else 1.0
    else:
        adjusted_recall = 0.0

    products_accuracy = products_correct / products_total if products_total > 0 else 0
    qty_accuracy = quantity_correct / products_correct if products_correct > 0 else 0
    total_accuracy = adjusted_recall * products_accuracy * qty_accuracy

    return {
        "valid_json": True,
        "recall_raw": products_correct,
        "recall_denominator": recall_denominator,
        "adjusted_recall": min(adjusted_recall, 1.0),
        "products_correct": products_correct,
        "products_total": products_total,
        "products_accuracy": products_accuracy,
        "quantity_correct": quantity_correct,
        "quantity_accuracy": qty_accuracy,
        "total_accuracy": total_accuracy,
        "extra_items": extra_items,
        "missed_items": missed_items,
        "missed_in_chatter": missed_in_chatter,
        "model_line_count": len(model_lines),
        "ignored_count": len(ignored_messages),
    }


# ============================================================
# DATA LOADING & TEST CASES
# ============================================================
CHAT_TO_CARDS = {
    "Bellona VS TJUK": ["CB00367"],
    "Snow World & TJUK ORDERING": ["CS01645", "CP00706", "CP00762"],
    "Sankalp Orders": ["CS01197", "CL00050"],
    "Monarch Liberty": ["CM01282", "CM01292"],
    "Good Food Concept": ["CG00313"],
    "Cremure Order Group": ["CC00815"],
    "Urban Gourmet UGIPL": ["CU00010", "CU00107", "CU00126"],
    "Jalapeno Food Ordering": ["CJ00241"],
    "Kulturd Kombucha": ["CK00598"],
    "BAWA GROUP ORDERING": ["CK00392", "CH00177"],
    "DU Hospitality X TJUK": ["CI00164"],
    "Ketan Oberoi Tower": ["CO00008"],
    "Laxmi Foods Pillsbury": ["CL00037"],
    "Pillsbury & Bake Wish": ["CB00642"],
}

TEST_CASES = [
    ("Good Food Concept", "2025-12-03", "EASY"),
    ("Cremure Order Group", "2025-10-30", "EASY"),
    ("Jalapeno Food Ordering", "2025-10-13", "EASY"),
    ("Ketan Oberoi Tower", "2025-08-13", "EASY"),
    ("Urban Gourmet UGIPL", "2025-09-01", "EASY"),
    ("Laxmi Foods Pillsbury", "2025-10-07", "EASY"),
    ("Cremure Order Group", "2026-01-26", "EASY"),
    ("DU Hospitality X TJUK", "2026-01-07", "EASY"),
    ("Good Food Concept", "2025-12-25", "MEDIUM"),
    ("Bellona VS TJUK", "2026-02-09", "MEDIUM"),
    ("Jalapeno Food Ordering", "2025-12-18", "MEDIUM"),
    ("Urban Gourmet UGIPL", "2025-09-26", "MEDIUM"),
    ("Ketan Oberoi Tower", "2026-01-20", "MEDIUM"),
    ("Cremure Order Group", "2026-01-16", "MEDIUM"),
    ("BAWA GROUP ORDERING", "2025-10-27", "MEDIUM"),
    ("DU Hospitality X TJUK", "2026-02-19", "MEDIUM"),
    ("Snow World & TJUK ORDERING", "2026-02-06", "HARD"),
    ("Monarch Liberty", "2026-01-02", "HARD"),
    ("Good Food Concept", "2025-12-15", "HARD"),
    ("DU Hospitality X TJUK", "2026-01-22", "HARD"),
    ("Cremure Order Group", "2025-12-08", "HARD"),
    ("Bellona VS TJUK", "2026-02-20", "HARD"),
    ("Snow World & TJUK ORDERING", "2026-02-17", "HARD"),
    ("Kulturd Kombucha", "2026-02-06", "HARD"),
]


def load_data():
    script_dir = os.path.dirname(os.path.abspath(__file__))

    sap_candidates = [
        os.path.join(script_dir, "whatsapp testing data.xlsx"),
    ]
    sap_path = None
    for p in sap_candidates:
        if os.path.exists(p):
            sap_path = p
            break
    if not sap_path:
        raise FileNotFoundError("Cannot find 'whatsapp testing data.xlsx'. Place it in same folder as this script.")

    wa_candidates = [
        os.path.join(script_dir, "whatsapp_master_dataset.xlsx"),
    ]
    wa_path = None
    for p in wa_candidates:
        if os.path.exists(p):
            wa_path = p
            break
    if not wa_path:
        raise FileNotFoundError("Cannot find 'whatsapp_master_dataset.xlsx'. Place it in same folder as this script.")

    print(f"  SAP data: {sap_path}")
    print(f"  WA data:  {wa_path}")

    orders_df = pd.read_excel(sap_path, sheet_name="Orders")
    products_df = pd.read_excel(sap_path, sheet_name="Product master")
    ship_df = pd.read_excel(sap_path, sheet_name="Ship to")

    # Load weightpack data and merge into products
    try:
        wp_df = pd.read_excel(sap_path, sheet_name="weightpack")
        products_df = products_df.merge(
            wp_df[["ItemCode", "SalPackUn", "BWeight1"]],
            on="ItemCode", how="left"
        )
        print(f"  Weightpack merged: {products_df['SalPackUn'].notna().sum()}/{len(products_df)} products with pack size")
    except Exception as e:
        print(f"  WARNING: Could not load weightpack sheet: {e}")
        products_df["SalPackUn"] = 1
        products_df["BWeight1"] = None

    # Build historical order ranges
    hist_ranges = {}
    for (card, item), grp in orders_df.groupby(["CardCode", "ItemCode"]):
        qtys = grp["Quantity"].tolist()
        hist_ranges[(card, item)] = {
            "min": min(qtys),
            "max": max(qtys),
            "median": statistics.median(qtys),
            "mean": statistics.mean(qtys),
            "count": len(qtys),
        }
    print(f"  Historical ranges: {len(hist_ranges)} customer×item combos")

    # Load WhatsApp messages
    wb_wa = openpyxl.load_workbook(wa_path, read_only=True)
    ws_wa = wb_wa["All Messages"]
    wa_msgs = []
    for row in ws_wa.iter_rows(min_row=2, values_only=True):
        if not row[1]:
            continue
        wa_msgs.append({
            "chat": row[1], "date_str": str(row[2]), "time": str(row[3]),
            "sender": str(row[4]), "message": str(row[5]) if row[5] else "",
            "type": str(row[6]) if row[6] else "", "order_grp": str(row[7]) if row[7] else "",
        })

    return orders_df, products_df, ship_df, wa_msgs, hist_ranges


def parse_wa_date(d):
    try:
        return datetime.strptime(d, "%d/%m/%y").strftime("%Y-%m-%d")
    except:
        return None


def prepare_scenarios(orders_df, products_df, ship_df, wa_msgs, hist_ranges):
    scenarios = []
    for idx, (chat, date_str, difficulty) in enumerate(TEST_CASES, 1):
        card_codes = CHAT_TO_CARDS[chat]
        cust_orders = orders_df[orders_df["CardCode"].isin(card_codes)]
        cust_items = cust_orders["ItemCode"].unique()
        cust_products = products_df[products_df["ItemCode"].isin(cust_items)].copy()
        cust_ship = ship_df[ship_df["CardCode"].isin(card_codes)]
        ship_addresses = list(cust_ship["Address"].dropna().unique())
        card_names = ", ".join(cust_orders["CardName"].unique()[:3])

        wa_on_date = [
            m for m in wa_msgs
            if m["chat"] == chat and parse_wa_date(m["date_str"]) == date_str
        ]

        sap_truth = orders_df[
            (orders_df["CardCode"].isin(card_codes))
            & (orders_df["DocDate"].dt.strftime("%Y-%m-%d") == date_str)
        ]

        scenarios.append({
            "idx": idx,
            "chat": chat,
            "date": date_str,
            "difficulty": difficulty,
            "context": {
                "chat": chat,
                "card_codes": ", ".join(card_codes),
                "card_names": card_names,
                "ship_to": ship_addresses,
            },
            "products": cust_products,
            "messages": sorted(wa_on_date, key=lambda x: x["time"]),
            "sap_truth": sap_truth,
        })
    return scenarios


# ============================================================
# MAIN EVAL
# ============================================================
def run_eval():
    print("=" * 60)
    print("WhatsApp Order Bot — Multi-Model Eval v2")
    print("  6 models | enriched catalogue | conversion rules")
    print("=" * 60)

    print("\nLoading data...")
    orders_df, products_df, ship_df, wa_msgs, hist_ranges = load_data()

    print("Preparing scenarios...")
    scenarios = prepare_scenarios(orders_df, products_df, ship_df, wa_msgs, hist_ranges)
    print(f"  {len(scenarios)} scenarios ready")

    all_results = []

    for model in MODELS:
        print(f"\n{'=' * 60}")
        print(f"MODEL: {model['name']} ({model['model_id']})")
        print(f"{'=' * 60}")

        total_input_tokens = 0
        total_output_tokens = 0
        total_cost = 0

        for scenario in scenarios:
            user_prompt = build_user_prompt(scenario, hist_ranges)
            print(f"  Scenario {scenario['idx']:>2} [{scenario['difficulty']:<6}] {scenario['chat'][:30]:<30} ... ", end="", flush=True)

            start = time.time()
            try:
                text, in_tok, out_tok, error = call_model(model, SYSTEM_PROMPT, user_prompt)
            except Exception as e:
                text, in_tok, out_tok, error = None, 0, 0, f"Exception: {str(e)[:150]}"
            elapsed = time.time() - start

            if error:
                print(f"ERROR: {error[:80]}")
                all_results.append({
                    "model": model["name"],
                    "scenario": scenario["idx"],
                    "chat": scenario["chat"],
                    "date": scenario["date"],
                    "difficulty": scenario["difficulty"],
                    "valid_json": False,
                    "adjusted_recall": 0, "products_correct": 0,
                    "products_total": len(scenario["sap_truth"]),
                    "products_accuracy": 0, "quantity_correct": 0,
                    "quantity_accuracy": 0, "total_accuracy": 0,
                    "extra_items": 0, "missed_items": len(scenario["sap_truth"]),
                    "missed_in_chatter": 0,
                    "input_tokens": 0, "output_tokens": 0,
                    "cost": 0, "latency_s": elapsed,
                    "error": error[:200], "raw_output": "",
                })
                continue

            cost = (in_tok / 1_000_000) * model["input_cost"] + (out_tok / 1_000_000) * model["output_cost"]
            total_input_tokens += in_tok
            total_output_tokens += out_tok
            total_cost += cost

            parsed = extract_json_from_response(text)
            scores = score_result(parsed, scenario["sap_truth"], products_df)

            print(f"Recall: {scores['adjusted_recall']:.0%} | "
                  f"Product: {scores['products_correct']}/{scores['products_total']} "
                  f"({scores['products_accuracy']:.0%}) | "
                  f"Qty: {scores['quantity_accuracy']:.0%} | "
                  f"Total: {scores['total_accuracy']:.0%} | "
                  f"${cost:.4f} | {elapsed:.1f}s")

            all_results.append({
                "model": model["name"],
                "scenario": scenario["idx"],
                "chat": scenario["chat"],
                "date": scenario["date"],
                "difficulty": scenario["difficulty"],
                **scores,
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cost": cost,
                "latency_s": elapsed,
                "error": "",
                "raw_output": text if text else "",
            })

            time.sleep(0.5)

        print(f"\n  TOTAL: {total_input_tokens:,} in + {total_output_tokens:,} out tokens | ${total_cost:.4f}")

    return all_results, products_df


# ============================================================
# EXCEL OUTPUT (v2 — with recall, total accuracy)
# ============================================================
def build_results_excel(all_results):
    df = pd.DataFrame(all_results)

    out = openpyxl.Workbook()
    hdr_font = Font(name="Arial", bold=True, size=11, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    body = Font(name="Arial", size=10)
    bold = Font(name="Arial", size=10, bold=True)
    title_font = Font(name="Arial", bold=True, size=14, color="1F4E79")
    thin = Border(left=Side("thin"), right=Side("thin"), top=Side("thin"), bottom=Side("thin"))
    easy_fill = PatternFill("solid", fgColor="C6EFCE")
    med_fill = PatternFill("solid", fgColor="FFEB9C")
    hard_fill = PatternFill("solid", fgColor="FFC7CE")
    diff_fills = {"EASY": easy_fill, "MEDIUM": med_fill, "HARD": hard_fill}
    pct = "0.0%"
    cost_fmt = "$#,##0.0000"

    # ---- SHEET 1: Model Comparison Summary ----
    ws1 = out.active
    ws1.title = "Model Comparison"
    ws1.merge_cells("A1:I1")
    ws1.cell(row=1, column=1, value="Model Comparison Summary — v2 (with enriched catalogue + conversion rules)").font = title_font

    headers1 = ["Model", "Avg\nRecall", "Avg Product\nAccuracy", "Avg Quantity\nAccuracy",
                 "Avg Total\nAccuracy", "Total Input\nTokens", "Total Output\nTokens",
                 "Total Cost", "Valid JSON %"]
    for c, h in enumerate(headers1, 1):
        cell = ws1.cell(row=3, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    for r, model_name in enumerate(df["model"].unique(), 4):
        mdf = df[df["model"] == model_name]
        ws1.cell(row=r, column=1, value=model_name).font = bold
        for ci, col in enumerate(["adjusted_recall", "products_accuracy", "quantity_accuracy", "total_accuracy"], 2):
            c = ws1.cell(row=r, column=ci, value=mdf[col].mean())
            c.font = bold if col == "total_accuracy" else body; c.number_format = pct
        ws1.cell(row=r, column=6, value=int(mdf["input_tokens"].sum())).font = body
        ws1.cell(row=r, column=7, value=int(mdf["output_tokens"].sum())).font = body
        c8 = ws1.cell(row=r, column=8, value=mdf["cost"].sum())
        c8.font = bold; c8.number_format = cost_fmt
        c9 = ws1.cell(row=r, column=9, value=mdf["valid_json"].mean())
        c9.font = body; c9.number_format = pct
        for c in range(1, 10): ws1.cell(row=r, column=c).border = thin

    for col, w in zip("ABCDEFGHI", [22, 12, 14, 14, 14, 14, 14, 12, 12]):
        ws1.column_dimensions[col].width = w

    # ---- SHEET 2: By Difficulty ----
    ws2 = out.create_sheet("By Difficulty")
    ws2.merge_cells("A1:H1")
    ws2.cell(row=1, column=1, value="Accuracy Breakdown by Difficulty Level").font = title_font

    headers2 = ["Model", "Difficulty", "Recall", "Product Acc", "Qty Acc", "Total Acc", "Scenarios", "Avg Cost"]
    for c, h in enumerate(headers2, 1):
        cell = ws2.cell(row=3, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    row_num = 4
    for model_name in df["model"].unique():
        for diff in ["EASY", "MEDIUM", "HARD"]:
            ddf = df[(df["model"] == model_name) & (df["difficulty"] == diff)]
            if len(ddf) == 0:
                continue
            ws2.cell(row=row_num, column=1, value=model_name).font = bold
            c2 = ws2.cell(row=row_num, column=2, value=diff)
            c2.font = body; c2.fill = diff_fills.get(diff, PatternFill())
            for ci, col in enumerate(["adjusted_recall", "products_accuracy", "quantity_accuracy", "total_accuracy"], 3):
                c = ws2.cell(row=row_num, column=ci, value=ddf[col].mean())
                c.font = body; c.number_format = pct
            ws2.cell(row=row_num, column=7, value=len(ddf)).font = body
            c8 = ws2.cell(row=row_num, column=8, value=ddf["cost"].mean())
            c8.font = body; c8.number_format = cost_fmt
            for c in range(1, 9): ws2.cell(row=row_num, column=c).border = thin
            row_num += 1

    for col, w in zip("ABCDEFGH", [22, 10, 10, 12, 10, 10, 10, 10]):
        ws2.column_dimensions[col].width = w

    # ---- SHEET 3: Detailed Results ----
    ws3 = out.create_sheet("Detailed Results")
    headers3 = ["Scenario", "Difficulty", "Customer", "Date", "Model",
                 "Recall", "Products\nCorrect", "Products\nTotal", "Product\nAccuracy",
                 "Qty\nCorrect", "Qty\nAccuracy", "Total\nAccuracy",
                 "Extra\nItems", "Missed\nItems", "Missed in\nChatter",
                 "Input\nTokens", "Output\nTokens", "Cost", "Latency", "Error"]
    for c, h in enumerate(headers3, 1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    for r, (_, row) in enumerate(df.iterrows(), 2):
        vals = [
            row["scenario"], row["difficulty"], row["chat"][:30], row["date"], row["model"],
            row["adjusted_recall"], row["products_correct"], row["products_total"],
            row["products_accuracy"], row["quantity_correct"], row["quantity_accuracy"],
            row["total_accuracy"], row.get("extra_items", 0), row.get("missed_items", 0),
            row.get("missed_in_chatter", 0),
            row["input_tokens"], row["output_tokens"], row["cost"],
            round(row["latency_s"], 1), row.get("error", "")[:50],
        ]
        for c, v in enumerate(vals, 1):
            cell = ws3.cell(row=r, column=c, value=v)
            cell.font = body; cell.border = thin
        ws3.cell(row=r, column=2).fill = diff_fills.get(row["difficulty"], PatternFill())
        for pct_col in [6, 9, 11, 12]:
            ws3.cell(row=r, column=pct_col).number_format = pct
        ws3.cell(row=r, column=18).number_format = cost_fmt

    for col, w in zip("ABCDEFGHIJKLMNOPQRST",
                       [8,10,30,12,22,10,10,10,10,10,10,10,8,8,10,10,10,10,8,30]):
        ws3.column_dimensions[col].width = w

    # ---- SHEET 4: Cost Analysis ----
    ws4 = out.create_sheet("Cost Analysis")
    ws4.merge_cells("A1:G1")
    ws4.cell(row=1, column=1, value="Cost & Token Analysis").font = title_font

    headers4 = ["Model", "Input Tokens", "Output Tokens", "Total Tokens",
                 "Total Cost", "Cost per\nScenario", "Projected\n60 orders/day\n(monthly)"]
    for c, h in enumerate(headers4, 1):
        cell = ws4.cell(row=3, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    for r, model_name in enumerate(df["model"].unique(), 4):
        mdf = df[df["model"] == model_name]
        in_t = int(mdf["input_tokens"].sum())
        out_t = int(mdf["output_tokens"].sum())
        total_cost = mdf["cost"].sum()
        cost_per = total_cost / 24 if total_cost > 0 else 0
        monthly = cost_per * 60 * 30  # 60 orders/day × 30 days

        ws4.cell(row=r, column=1, value=model_name).font = bold
        ws4.cell(row=r, column=2, value=in_t).font = body
        ws4.cell(row=r, column=3, value=out_t).font = body
        ws4.cell(row=r, column=4, value=in_t + out_t).font = body
        c5 = ws4.cell(row=r, column=5, value=total_cost)
        c5.font = bold; c5.number_format = cost_fmt
        c6 = ws4.cell(row=r, column=6, value=cost_per)
        c6.font = body; c6.number_format = cost_fmt
        c7 = ws4.cell(row=r, column=7, value=monthly)
        c7.font = bold; c7.number_format = "$#,##0.00"
        for c in range(1, 8): ws4.cell(row=r, column=c).border = thin

    for col, w in zip("ABCDEFG", [22, 14, 14, 14, 12, 12, 16]):
        ws4.column_dimensions[col].width = w

    # ---- SHEET 5: Raw Outputs ----
    ws5 = out.create_sheet("Raw Outputs")
    headers5 = ["Scenario", "Model", "Raw Output"]
    for c, h in enumerate(headers5, 1):
        cell = ws5.cell(row=1, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin

    for r, (_, row) in enumerate(df.iterrows(), 2):
        ws5.cell(row=r, column=1, value=row["scenario"]).font = body
        ws5.cell(row=r, column=2, value=row["model"]).font = body
        # Excel cells max ~32K chars; truncate for display but save full to JSON
        ws5.cell(row=r, column=3, value=row.get("raw_output", "")[:32000]).font = body
        ws5.cell(row=r, column=3).alignment = Alignment(wrap_text=True)

    ws5.column_dimensions["A"].width = 10
    ws5.column_dimensions["B"].width = 22
    ws5.column_dimensions["C"].width = 100

    script_dir = os.path.dirname(os.path.abspath(__file__))
    outpath = os.path.join(script_dir, "WhatsApp_Eval_Results_v2.xlsx")
    out.save(outpath)
    print(f"\nResults saved to {outpath}")
    return outpath


def save_raw_outputs_json(all_results):
    """Save full raw outputs as JSON for ground truth scoring."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    raw_out = {}
    for r in all_results:
        key = f"{r['model']}__S{r['scenario']}"
        raw_out[key] = {
            "model": r["model"],
            "scenario": r["scenario"],
            "chat": r["chat"],
            "date": r["date"],
            "difficulty": r["difficulty"],
            "raw_output": r.get("raw_output", ""),
            "error": r.get("error", ""),
            "input_tokens": r.get("input_tokens", 0),
            "output_tokens": r.get("output_tokens", 0),
            "cost": r.get("cost", 0),
        }
    outpath = os.path.join(script_dir, "raw_model_outputs.json")
    with open(outpath, "w") as f:
        json.dump(raw_out, f, indent=2)
    print(f"Raw outputs saved to {outpath}")
    return outpath


if __name__ == "__main__":
    results, products_df = run_eval()
    outpath = build_results_excel(results)
    json_path = save_raw_outputs_json(results)
    print(f"\nDone! Results at: {outpath}")
    print(f"Raw outputs at: {json_path}")
