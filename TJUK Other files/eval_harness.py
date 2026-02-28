#!/usr/bin/env python3
"""
WhatsApp Order Bot — Multi-Model Eval Harness
Runs 24 test scenarios across 5 models, scores against SAP ground truth.
Tracks token usage and cost per model.
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

# ============================================================
# API KEYS
# ============================================================
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "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")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "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")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "AIzaSyBQrlO-UCoWx7Aik-Re0LkI9FX6TMpkGPY")

# ============================================================
# MODEL CONFIGS (name, provider, model_id, input_cost_per_M, output_cost_per_M)
# ============================================================
MODELS = [
    {
        "name": "Claude Sonnet 4.5",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5-20250929",
        "input_cost": 3.0,    # $ per 1M input tokens
        "output_cost": 15.0,  # $ per 1M output tokens
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
        "name": "GPT-4o-mini",
        "provider": "openai",
        "model_id": "gpt-4o-mini",
        "input_cost": 0.15,
        "output_cost": 0.60,
    },
    {
        "name": "Gemini 2.0 Flash",
        "provider": "google",
        "model_id": "gemini-2.0-flash",
        "input_cost": 0.10,
        "output_cost": 0.40,
    },
]

# ============================================================
# API CALLERS
# ============================================================
def call_anthropic(model_id, system_prompt, user_prompt):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=120,
    )
    data = resp.json()
    if resp.status_code != 200:
        return None, 0, 0, f"Error {resp.status_code}: {data.get('error', {}).get('message', str(data))}"
    text = data["content"][0]["text"]
    input_tokens = data["usage"]["input_tokens"]
    output_tokens = data["usage"]["output_tokens"]
    return text, input_tokens, output_tokens, None


def call_openai(model_id, system_prompt, user_prompt):
    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": model_id,
            "max_tokens": 4096,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout=120,
    )
    data = resp.json()
    if resp.status_code != 200:
        return None, 0, 0, f"Error {resp.status_code}: {data.get('error', {}).get('message', str(data))}"
    text = data["choices"][0]["message"]["content"]
    input_tokens = data["usage"]["prompt_tokens"]
    output_tokens = data["usage"]["completion_tokens"]
    return text, input_tokens, output_tokens, None


def call_google(model_id, system_prompt, user_prompt):
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": 4096},
        },
        timeout=120,
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
# PROMPT BUILDER
# ============================================================
SYSTEM_PROMPT = """You are an AI order processing engine for TJUK, a food distribution company in Mumbai.
Your job: read WhatsApp messages and extract structured order data.

You will receive:
1. CUSTOMER CONTEXT — who this WhatsApp group belongs to (CardCode, CardName, ship-to addresses)
2. PRODUCT CATALOGUE — list of products this customer has ordered before (ItemCode | ItemName | UoM)
3. WHATSAPP MESSAGES — the raw messages to process

Your task:
- Identify which messages are actual orders (ignore acknowledgments, operational chatter, stock updates)
- For each order, extract: which ship-to address, what products, what quantities
- Match product names from messages to the closest ItemCode in the catalogue
- Handle additions (messages that say "add", "pls add") by merging into the parent order
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
          "quantity": 5,
          "uom": "PCS/KG/BOX/CASE/etc",
          "original_text": "what the customer actually wrote",
          "confidence": "HIGH/MEDIUM/LOW"
        }
      ]
    }
  ],
  "ignored_messages": ["list of message texts that were not orders"],
  "notes": "any ambiguities or issues found"
}

Rules:
- Match products using fuzzy matching — customers use abbreviations, typos, informal names
- "btl" = bottle, "pkt" = packet/pcs, "nos" = numbers/pcs, "pic" = pieces
- If quantity says "1 box" and the product is sold in PCS, output the box quantity as-is with uom=BOX
- If you cannot confidently match a product, still include it with confidence=LOW and your best guess
- Do NOT invent products. Only match to items in the provided catalogue.
- If the catalogue doesn't have a match, set item_code to "UNKNOWN" and keep the original text"""


def build_user_prompt(scenario_data):
    ctx = scenario_data["context"]
    products = scenario_data["products"]
    messages = scenario_data["messages"]

    prompt = "=== CUSTOMER CONTEXT ===\n"
    prompt += f"WhatsApp Group: {ctx['chat']}\n"
    prompt += f"Customer: {ctx['card_codes']} — {ctx['card_names']}\n"
    prompt += f"Ship-to Addresses:\n"
    for addr in ctx["ship_to"]:
        prompt += f"  - {addr}\n"

    prompt += "\n=== PRODUCT CATALOGUE ===\n"
    prompt += "ItemCode | ItemName | UoM\n"
    for _, p in products.iterrows():
        prompt += f"{p['ItemCode']} | {p['ItemName']} | {p['SalUnitMsr']}\n"

    prompt += "\n=== WHATSAPP MESSAGES (process these) ===\n"
    for m in messages:
        prompt += f"[{m['time']}] {m['sender']}: {m['message']}\n"

    return prompt


# ============================================================
# SCORING ENGINE
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
    # Try to find raw JSON
    match = re.search(r'(\{.*\})', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def score_result(model_output_json, sap_truth, products_df):
    """Score model output against SAP ground truth. Returns dict of metrics."""
    if not model_output_json or "orders" not in model_output_json:
        return {
            "valid_json": False,
            "products_correct": 0,
            "products_total": len(sap_truth),
            "products_accuracy": 0.0,
            "quantity_correct": 0,
            "quantity_accuracy": 0.0,
            "extra_items": 0,
            "missed_items": len(sap_truth),
            "ship_to_correct": 0,
            "ship_to_total": sap_truth["ShipToCode"].nunique() if "ShipToCode" in sap_truth.columns else 0,
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
                "ship_to": ship_to,
            })

    # SAP truth lines (aggregate by ItemCode per order)
    sap_lines = []
    for _, row in sap_truth.iterrows():
        sap_lines.append({
            "item_code": row["ItemCode"],
            "item_name": row["Dscription"],
            "quantity": row["Quantity"],
            "ship_to": str(row.get("ShipToCode", "")),
        })

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
            # Check quantity
            sap_qty = sap_lines[best_match]["quantity"]
            model_qty = ml.get("quantity", 0)
            try:
                model_qty = float(model_qty)
            except (ValueError, TypeError):
                model_qty = 0
            if abs(model_qty - sap_qty) < 0.01:
                quantity_correct += 1

    products_total = len(sap_lines)
    extra_items = max(0, len(model_lines) - products_correct)
    missed_items = products_total - products_correct

    return {
        "valid_json": True,
        "products_correct": products_correct,
        "products_total": products_total,
        "products_accuracy": products_correct / products_total if products_total > 0 else 0,
        "quantity_correct": quantity_correct,
        "quantity_accuracy": quantity_correct / products_total if products_total > 0 else 0,
        "extra_items": extra_items,
        "missed_items": missed_items,
        "model_line_count": len(model_lines),
        "ignored_count": len(model_output_json.get("ignored_messages", [])),
    }


# ============================================================
# MAIN EVAL
# ============================================================
def load_data():
    # Look for data files in current directory first, then original paths
    script_dir = os.path.dirname(os.path.abspath(__file__))

    sap_candidates = [
        os.path.join(script_dir, "whatsapp testing data.xlsx"),
        os.path.join(script_dir, "whatsapp testing data-1f573358.xlsx"),
        "/sessions/vibrant-beautiful-edison/mnt/uploads/whatsapp testing data-1f573358.xlsx",
    ]
    sap_path = None
    for p in sap_candidates:
        if os.path.exists(p):
            sap_path = p
            break
    if not sap_path:
        raise FileNotFoundError("Cannot find SAP data file. Place 'whatsapp testing data.xlsx' in same folder as this script.")

    wa_candidates = [
        os.path.join(script_dir, "whatsapp_master_dataset.xlsx"),
        "/sessions/vibrant-beautiful-edison/mnt/Whatsapp messages/whatsapp_master_dataset.xlsx",
    ]
    wa_path = None
    for p in wa_candidates:
        if os.path.exists(p):
            wa_path = p
            break
    if not wa_path:
        raise FileNotFoundError("Cannot find WhatsApp dataset. Place 'whatsapp_master_dataset.xlsx' in same folder as this script.")

    print(f"  SAP data: {sap_path}")
    print(f"  WA data:  {wa_path}")

    orders_df = pd.read_excel(sap_path, sheet_name="Orders")
    products_df = pd.read_excel(sap_path, sheet_name="Product master")
    ship_df = pd.read_excel(sap_path, sheet_name="Ship to")

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

    return orders_df, products_df, ship_df, wa_msgs


def parse_wa_date(d):
    try:
        return datetime.strptime(d, "%d/%m/%y").strftime("%Y-%m-%d")
    except:
        return None


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


def prepare_scenarios(orders_df, products_df, ship_df, wa_msgs):
    scenarios = []
    for idx, (chat, date_str, difficulty) in enumerate(TEST_CASES, 1):
        card_codes = CHAT_TO_CARDS[chat]
        cust_orders = orders_df[orders_df["CardCode"].isin(card_codes)]
        cust_items = cust_orders["ItemCode"].unique()
        cust_products = products_df[products_df["ItemCode"].isin(cust_items)][
            ["ItemCode", "ItemName", "SalUnitMsr"]
        ]
        cust_ship = ship_df[ship_df["CardCode"].isin(card_codes)]
        ship_addresses = list(cust_ship["Address"].dropna().unique())
        card_names = ", ".join(
            cust_orders["CardName"].unique()[:3]
        )

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


def run_eval():
    print("=" * 60)
    print("WhatsApp Order Bot — Multi-Model Eval")
    print("=" * 60)

    print("\nLoading data...")
    orders_df, products_df, ship_df, wa_msgs = load_data()

    print("Preparing scenarios...")
    scenarios = prepare_scenarios(orders_df, products_df, ship_df, wa_msgs)
    print(f"  {len(scenarios)} scenarios ready")

    # Results storage
    all_results = []

    for model in MODELS:
        print(f"\n{'=' * 60}")
        print(f"MODEL: {model['name']} ({model['model_id']})")
        print(f"{'=' * 60}")

        total_input_tokens = 0
        total_output_tokens = 0
        total_cost = 0

        for scenario in scenarios:
            user_prompt = build_user_prompt(scenario)
            print(f"  Scenario {scenario['idx']:>2} [{scenario['difficulty']:<6}] {scenario['chat'][:30]:<30} ... ", end="", flush=True)

            start = time.time()
            try:
                text, in_tok, out_tok, error = call_model(model, SYSTEM_PROMPT, user_prompt)
            except Exception as e:
                text, in_tok, out_tok, error = None, 0, 0, f"Exception: {str(e)[:150]}"
            elapsed = time.time() - start

            if error:
                print(f"ERROR: {error[:60]}")
                all_results.append({
                    "model": model["name"],
                    "scenario": scenario["idx"],
                    "chat": scenario["chat"],
                    "date": scenario["date"],
                    "difficulty": scenario["difficulty"],
                    "valid_json": False,
                    "products_correct": 0,
                    "products_total": len(scenario["sap_truth"]),
                    "products_accuracy": 0,
                    "quantity_correct": 0,
                    "quantity_accuracy": 0,
                    "extra_items": 0,
                    "missed_items": len(scenario["sap_truth"]),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0,
                    "latency_s": elapsed,
                    "error": error[:200],
                    "raw_output": "",
                })
                continue

            cost = (in_tok / 1_000_000) * model["input_cost"] + (out_tok / 1_000_000) * model["output_cost"]
            total_input_tokens += in_tok
            total_output_tokens += out_tok
            total_cost += cost

            parsed = extract_json_from_response(text)
            scores = score_result(parsed, scenario["sap_truth"], products_df)

            print(f"Products: {scores['products_correct']}/{scores['products_total']} "
                  f"({scores['products_accuracy']:.0%}) | "
                  f"Qty: {scores['quantity_accuracy']:.0%} | "
                  f"Tokens: {in_tok}+{out_tok} | "
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
                "raw_output": text[:2000] if text else "",
            })

            time.sleep(0.5)  # Rate limit buffer

        print(f"\n  TOTAL: {total_input_tokens:,} input + {total_output_tokens:,} output tokens | ${total_cost:.4f}")

    return all_results, products_df


def build_results_excel(all_results):
    """Build comprehensive results spreadsheet."""
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
    ws1.merge_cells("A1:H1")
    ws1.cell(row=1, column=1, value="Model Comparison Summary").font = title_font

    headers1 = ["Model", "Avg Product\nAccuracy", "Avg Quantity\nAccuracy",
                 "Total Input\nTokens", "Total Output\nTokens", "Total Cost",
                 "Avg Latency (s)", "Valid JSON %"]
    for c, h in enumerate(headers1, 1):
        cell = ws1.cell(row=3, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    for r, model_name in enumerate(df["model"].unique(), 4):
        mdf = df[df["model"] == model_name]
        ws1.cell(row=r, column=1, value=model_name).font = bold
        c2 = ws1.cell(row=r, column=2, value=mdf["products_accuracy"].mean())
        c2.font = bold; c2.number_format = pct
        c3 = ws1.cell(row=r, column=3, value=mdf["quantity_accuracy"].mean())
        c3.font = body; c3.number_format = pct
        ws1.cell(row=r, column=4, value=int(mdf["input_tokens"].sum())).font = body
        ws1.cell(row=r, column=5, value=int(mdf["output_tokens"].sum())).font = body
        c6 = ws1.cell(row=r, column=6, value=mdf["cost"].sum())
        c6.font = bold; c6.number_format = cost_fmt
        ws1.cell(row=r, column=7, value=round(mdf["latency_s"].mean(), 1)).font = body
        c8 = ws1.cell(row=r, column=8, value=mdf["valid_json"].mean())
        c8.font = body; c8.number_format = pct
        for c in range(1, 9): ws1.cell(row=r, column=c).border = thin

    for col, w in zip("ABCDEFGH", [20, 14, 14, 14, 14, 12, 14, 12]):
        ws1.column_dimensions[col].width = w

    # ---- SHEET 2: Accuracy by Difficulty ----
    ws2 = out.create_sheet("By Difficulty")
    ws2.merge_cells("A1:F1")
    ws2.cell(row=1, column=1, value="Accuracy Breakdown by Difficulty Level").font = title_font

    headers2 = ["Model", "EASY\nProduct Acc", "MEDIUM\nProduct Acc", "HARD\nProduct Acc",
                 "EASY\nQty Acc", "MEDIUM\nQty Acc", "HARD\nQty Acc"]
    for c, h in enumerate(headers2, 1):
        cell = ws2.cell(row=3, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    for r, model_name in enumerate(df["model"].unique(), 4):
        mdf = df[df["model"] == model_name]
        ws2.cell(row=r, column=1, value=model_name).font = bold
        for ci, diff in enumerate(["EASY", "MEDIUM", "HARD"], 2):
            ddf = mdf[mdf["difficulty"] == diff]
            c = ws2.cell(row=r, column=ci, value=ddf["products_accuracy"].mean() if len(ddf) > 0 else 0)
            c.font = body; c.number_format = pct; c.fill = diff_fills[diff]
        for ci, diff in enumerate(["EASY", "MEDIUM", "HARD"], 5):
            ddf = mdf[mdf["difficulty"] == diff]
            c = ws2.cell(row=r, column=ci, value=ddf["quantity_accuracy"].mean() if len(ddf) > 0 else 0)
            c.font = body; c.number_format = pct
        for c in range(1, 8): ws2.cell(row=r, column=c).border = thin

    for col, w in zip("ABCDEFG", [20, 14, 14, 14, 14, 14, 14]):
        ws2.column_dimensions[col].width = w

    # ---- SHEET 3: Detailed Results ----
    ws3 = out.create_sheet("Detailed Results")
    headers3 = ["Scenario", "Difficulty", "Customer", "Date", "Model",
                 "Products\nCorrect", "Products\nTotal", "Product\nAccuracy",
                 "Qty\nCorrect", "Qty\nAccuracy", "Extra\nItems", "Missed\nItems",
                 "Input\nTokens", "Output\nTokens", "Cost", "Latency", "Error"]
    for c, h in enumerate(headers3, 1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    for r, (_, row) in enumerate(df.iterrows(), 2):
        ws3.cell(row=r, column=1, value=row["scenario"]).font = body
        ws3.cell(row=r, column=2, value=row["difficulty"]).font = body
        ws3.cell(row=r, column=2).fill = diff_fills.get(row["difficulty"], PatternFill())
        ws3.cell(row=r, column=3, value=row["chat"][:30]).font = body
        ws3.cell(row=r, column=4, value=row["date"]).font = body
        ws3.cell(row=r, column=5, value=row["model"]).font = body
        ws3.cell(row=r, column=6, value=row["products_correct"]).font = body
        ws3.cell(row=r, column=7, value=row["products_total"]).font = body
        c8 = ws3.cell(row=r, column=8, value=row["products_accuracy"])
        c8.font = body; c8.number_format = pct
        ws3.cell(row=r, column=9, value=row["quantity_correct"]).font = body
        c10 = ws3.cell(row=r, column=10, value=row["quantity_accuracy"])
        c10.font = body; c10.number_format = pct
        ws3.cell(row=r, column=11, value=row.get("extra_items", 0)).font = body
        ws3.cell(row=r, column=12, value=row.get("missed_items", 0)).font = body
        ws3.cell(row=r, column=13, value=row["input_tokens"]).font = body
        ws3.cell(row=r, column=14, value=row["output_tokens"]).font = body
        c15 = ws3.cell(row=r, column=15, value=row["cost"])
        c15.font = body; c15.number_format = cost_fmt
        ws3.cell(row=r, column=16, value=round(row["latency_s"], 1)).font = body
        ws3.cell(row=r, column=17, value=row.get("error", "")[:50]).font = body
        for c in range(1, 18): ws3.cell(row=r, column=c).border = thin

    for col, w in zip("ABCDEFGHIJKLMNOPQ", [8,10,30,12,18,10,10,10,10,10,8,8,10,10,10,8,30]):
        ws3.column_dimensions[col].width = w

    # ---- SHEET 4: Cost Analysis ----
    ws4 = out.create_sheet("Cost Analysis")
    ws4.merge_cells("A1:F1")
    ws4.cell(row=1, column=1, value="Cost & Token Analysis").font = title_font

    headers4 = ["Model", "Input Tokens", "Output Tokens", "Total Tokens",
                 "Total Cost", "Cost per\nScenario", "Accuracy per $"]
    for c, h in enumerate(headers4, 1):
        cell = ws4.cell(row=3, column=c, value=h)
        cell.font = hdr_font; cell.fill = hdr_fill; cell.border = thin
        cell.alignment = Alignment(wrap_text=True, horizontal="center")

    for r, model_name in enumerate(df["model"].unique(), 4):
        mdf = df[df["model"] == model_name]
        in_t = int(mdf["input_tokens"].sum())
        out_t = int(mdf["output_tokens"].sum())
        total_cost = mdf["cost"].sum()
        avg_acc = mdf["products_accuracy"].mean()

        ws4.cell(row=r, column=1, value=model_name).font = bold
        ws4.cell(row=r, column=2, value=in_t).font = body
        ws4.cell(row=r, column=3, value=out_t).font = body
        ws4.cell(row=r, column=4, value=in_t + out_t).font = body
        c5 = ws4.cell(row=r, column=5, value=total_cost)
        c5.font = bold; c5.number_format = cost_fmt
        c6 = ws4.cell(row=r, column=6, value=total_cost / 24 if total_cost > 0 else 0)
        c6.font = body; c6.number_format = cost_fmt
        c7 = ws4.cell(row=r, column=7, value=avg_acc / total_cost if total_cost > 0 else 0)
        c7.font = bold; c7.number_format = "0.0"
        for c in range(1, 8): ws4.cell(row=r, column=c).border = thin

    for col, w in zip("ABCDEFG", [20, 14, 14, 14, 12, 12, 14]):
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
        ws5.cell(row=r, column=3, value=row.get("raw_output", "")[:500]).font = body
        ws5.cell(row=r, column=3).alignment = Alignment(wrap_text=True)

    ws5.column_dimensions["A"].width = 10
    ws5.column_dimensions["B"].width = 20
    ws5.column_dimensions["C"].width = 100

    script_dir = os.path.dirname(os.path.abspath(__file__))
    outpath = os.path.join(script_dir, "WhatsApp_Eval_Results.xlsx")
    out.save(outpath)
    print(f"\nResults saved to {outpath}")
    return outpath


if __name__ == "__main__":
    results, products_df = run_eval()
    outpath = build_results_excel(results)
    print(f"\nDone! Results at: {outpath}")
