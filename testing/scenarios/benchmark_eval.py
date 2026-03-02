#!/usr/bin/env python3
"""
WhatsApp Order Bot — Benchmark Evaluation System
=================================================
Evaluates models across 25 scenarios measuring:
  (a) Customer identification
  (b) Ship-to address resolution
  (c) Product matching accuracy
  (d) Quantity conversion accuracy
  (e) Stage timing (per-stage and total latency)
  (f) LLM chattiness (number of response messages)
  (g) LLM verbosity (character count for token optimization)
  (h) Human-in-Loop detection (when to escalate vs guess wrong)
  + Token usage and cost per message

Models tested: Gemini 2.0 Flash, Claude Haiku 4.5, Claude Sonnet 4.5

Usage:
  # Load .env from project root
  pip install python-dotenv requests openpyxl
  python3 benchmark_eval.py
  python3 benchmark_eval.py --models haiku        # single model
  python3 benchmark_eval.py --scenarios 1,2,3      # specific scenarios
  python3 benchmark_eval.py --dry-run              # check setup only
"""

import json
import os
import re
import sys
import time
import argparse
import requests
from datetime import datetime
from difflib import SequenceMatcher
from collections import defaultdict

# ============================================================
# LOAD .env FROM PROJECT ROOT
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

def load_dotenv(path):
    """Minimal .env loader — no external dependency required."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

load_dotenv(ENV_PATH)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

# ============================================================
# MODEL CONFIGS — Only the 3 models we're evaluating
# ============================================================
MODELS = {
    "sonnet": {
        "name": "Claude Sonnet 4.5",
        "provider": "anthropic",
        "model_id": "claude-sonnet-4-5-20250929",
        "input_cost_per_M": 3.0,
        "output_cost_per_M": 15.0,
    },
    "haiku": {
        "name": "Claude Haiku 4.5",
        "provider": "anthropic",
        "model_id": "claude-haiku-4-5-20251001",
        "input_cost_per_M": 0.80,
        "output_cost_per_M": 4.0,
    },
    "gemini": {
        "name": "Gemini 2.0 Flash",
        "provider": "google",
        "model_id": "gemini-2.0-flash",
        "input_cost_per_M": 0.10,
        "output_cost_per_M": 0.40,
    },
}

# ============================================================
# BENCHMARK DEFINITIONS — Expected results for all 25 scenarios
# ============================================================
# Each benchmark defines what a CORRECT model should produce,
# AND what requires Human-in-Loop (HIL) because the data is
# genuinely unmatchable by any LLM or human without domain knowledge.
#
# Fields:
#   scenario_id: matches test_scenarios.json
#   expected_customer_id: the CardCode(s) model should identify
#   expected_ship_to_count: number of distinct ship-to addresses in SAP truth
#   expected_ship_to_codes: the actual ship-to codes from SAP
#   expected_product_count: number of SAP truth line items
#   hil_required: list of items/situations requiring Human-in-Loop
#   hil_reason: why HIL is needed
#   key_challenges: what makes this scenario hard
#   max_acceptable_bot_messages: ideal max bot responses (conversational mode)
#   expected_stages: which stages are tested [customer_id, ship_to, product, qty]

BENCHMARKS = [
    {
        "scenario_id": 1,
        "difficulty": "EASY",
        "chat_name": "Good Food Concept",
        "expected_customer_ids": ["CG00313"],
        "expected_ship_to_count": 3,
        "expected_ship_to_codes": [
            "GOOD FOOD CONCEPT( DADAR E)",
            "GOOD FOOD CONCEPT (BOMBAY GYMKHANA)",
            "GOOD FOOD CONCEPT( GOREGAON E)",
        ],
        "expected_product_count": 26,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "kg-to-PCS conversion (butter, cheese)",
            "btl = direct PCS",
            "Multiple ship-to addresses but customer says 'Dadar parsee gymkhana'",
        ],
        "max_acceptable_bot_messages": 3,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 2,
        "difficulty": "EASY",
        "chat_name": "Cremure Order Group",
        "expected_customer_ids": ["CC00815"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["CREMURE (JOGESHWARI)"],
        "expected_product_count": 1,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": ["Single product, kg conversion"],
        "max_acceptable_bot_messages": 2,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 3,
        "difficulty": "EASY",
        "chat_name": "Jalapeno Food Ordering",
        "expected_customer_ids": ["CJ00241"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["JALAPENO FOODS PVT LTD (PAREL EAST)"],
        "expected_product_count": 1,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": ["Single item order, straightforward"],
        "max_acceptable_bot_messages": 2,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 4,
        "difficulty": "EASY",
        "chat_name": "Laxmi Foods Pillsbury",
        "expected_customer_ids": ["CL00037"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["NINETY DEGREE (RABALE)"],
        "expected_product_count": 4,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": ["Multiple senders in group, need to isolate orders"],
        "max_acceptable_bot_messages": 3,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 5,
        "difficulty": "EASY",
        "chat_name": "Urban Gourmet UGIPL",
        "expected_customer_ids": ["CU00010", "CU00107", "CU00126"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["URBAN GOURMET INDIA PVT LTD (27 BAKE HOUSE)"],
        "expected_product_count": 5,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": ["Clean multi-item order, multiple CardCodes for same customer"],
        "max_acceptable_bot_messages": 2,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 6,
        "difficulty": "EASY",
        "chat_name": "Cremure Order Group",
        "expected_customer_ids": ["CC00815"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["CREMURE (JOGESHWARI)"],
        "expected_product_count": 11,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": ["Multiple items with kg conversion"],
        "max_acceptable_bot_messages": 2,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 7,
        "difficulty": "EASY",
        "chat_name": "DU Hospitality X TJUK",
        "expected_customer_ids": ["CI00164"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["INNERCIRCLE HOSPITALITY LLP"],
        "expected_product_count": 10,
        "hil_required": [
            {
                "item": "Tobasco-12btl",
                "sap_match": "I02IC05T05TRE001 — T.SAUCE RED PEPPER 60ML",
                "reason": "Customer says 'Tobasco' but catalog lists 'T.SAUCE RED PEPPER'. "
                          "No LLM or human can match this without prior domain knowledge. "
                          "This is a data entry naming convention issue.",
            },
            {
                "item": "Sirroco Jalepeno Slice- 1Tin",
                "sap_match": "No direct SAP match for 'Sirroco' brand jalapeno",
                "reason": "Brand name 'Sirroco' not in catalog. Multiple jalapeno products exist. "
                          "Needs human to confirm which specific product.",
            },
        ],
        "hil_reason": "Unmatchable product names due to data entry conventions",
        "key_challenges": [
            "Tobasco → T.SAUCE (genuinely unmatchable)",
            "Sirroco brand not in catalog",
            "case/box conversion for soda, juice",
        ],
        "max_acceptable_bot_messages": 3,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 8,
        "difficulty": "EASY",
        "chat_name": "Ketan Oberoi Tower",
        "expected_customer_ids": ["CO00008"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["OBEROI TOWER (NARIMAN POINT)"],
        "expected_product_count": 5,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "22 customer messages but only 5 SAP items — heavy chatter to filter",
            "Must identify actual orders vs operational messages",
        ],
        "max_acceptable_bot_messages": 5,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 9,
        "difficulty": "MEDIUM",
        "chat_name": "Good Food Concept",
        "expected_customer_ids": ["CG00313"],
        "expected_ship_to_count": 3,
        "expected_ship_to_codes": [
            "GOOD FOOD CONCEPT-MAHIM.W",
            "GOOD FOOD CONCEPT (BOMBAY GYMKHANA)",
            "GOOD FOOD CONCEPT( GOREGAON E)",
        ],
        "expected_product_count": 30,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "Holiday order — Christmas Day, high volume",
            "Addition messages ('add samosa patti 1 box')",
            "Multiple ship-to, need to route correctly",
            "30 line items — large order",
        ],
        "max_acceptable_bot_messages": 4,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 10,
        "difficulty": "MEDIUM",
        "chat_name": "Bellona VS TJUK",
        "expected_customer_ids": ["CB00367"],
        "expected_ship_to_count": 5,
        "expected_ship_to_codes": [
            "BELLONA HOSPITALITY SERVICES LTD-L.PAREL",
            "BELLONA HOSPITALITY SERVICES LIMITED - CRAFT(EIGHT",
            "BELLONA HOSPITALITY SERVICES LIMITED (CHA)",
            "BELLONA HOSPITALITY SERVICES LIMITED(LEGUME)",
            "BELLONA HOSPITALITY SERVICES LIMITED ALLORA CAFE",
        ],
        "expected_product_count": 26,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "5 distinct outlets/ship-to addresses",
            "Case conversions needed",
            "Must map outlet names from messages to SAP ship-to codes",
            "10 customer messages to process",
        ],
        "max_acceptable_bot_messages": 4,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 11,
        "difficulty": "MEDIUM",
        "chat_name": "Jalapeno Food Ordering",
        "expected_customer_ids": ["CJ00241"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["JALAPENO FOODS PVT LTD (PAREL EAST)"],
        "expected_product_count": 6,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "Addition message: 'Pls add order 9mm fres fries...'",
            "Mixed units: box, case",
            "Typos: 'fres fries' = french fries, 'sure scrip' = Sure Strip",
        ],
        "max_acceptable_bot_messages": 3,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 12,
        "difficulty": "MEDIUM",
        "chat_name": "Urban Gourmet UGIPL",
        "expected_customer_ids": ["CU00010", "CU00107", "CU00126"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["URBAN GOURMET INDIA PVT LTD (27 BAKE HOUSE)"],
        "expected_product_count": 6,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "'Please add more' — addition to existing order",
            "Case conversion for beverages (Thumsup, Coke, Ginger ale)",
            "Typo: 'Dite coke' = Diet Coke",
        ],
        "max_acceptable_bot_messages": 3,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 13,
        "difficulty": "MEDIUM",
        "chat_name": "Ketan Oberoi Tower",
        "expected_customer_ids": ["CO00008"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["OBEROI TOWER (NARIMAN POINT)"],
        "expected_product_count": 9,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "Operational chatter mixed with orders",
            "9 products to extract from 2 messages",
        ],
        "max_acceptable_bot_messages": 3,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 14,
        "difficulty": "MEDIUM",
        "chat_name": "Cremure Order Group",
        "expected_customer_ids": ["CC00815"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["CREMURE (JOGESHWARI)"],
        "expected_product_count": 7,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "Location mentions in messages",
            "kg conversions",
        ],
        "max_acceptable_bot_messages": 2,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 15,
        "difficulty": "MEDIUM",
        "chat_name": "BAWA GROUP ORDERING",
        "expected_customer_ids": ["CK00392", "CH00177"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["HOTEL BAWA REGENCY"],
        "expected_product_count": 5,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "4 senders in group — multi-sender",
            "Multi-outlet customer (Bawa Continental + others)",
            "kg conversions",
        ],
        "max_acceptable_bot_messages": 4,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 16,
        "difficulty": "MEDIUM",
        "chat_name": "DU Hospitality X TJUK",
        "expected_customer_ids": ["CI00164"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["INNERCIRCLE HOSPITALITY LLP"],
        "expected_product_count": 6,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "Addition message: 'Plz add fiamma Tomato peeled 2 box'",
            "Multiple senders contributing to one order",
            "box = case conversion needed",
        ],
        "max_acceptable_bot_messages": 3,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 17,
        "difficulty": "MEDIUM",
        "chat_name": "Monarch Liberty",
        "expected_customer_ids": ["CM01282", "CM01292"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["MONARCH LIBERTY WORLDWIDE HOSPITALITY LLP (Powai)"],
        "expected_product_count": 10,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "Multi-outlet customer (Powai, Santacruz)",
            "Fries dispatch reference (operational, not an order)",
            "10 products across 2 senders",
        ],
        "max_acceptable_bot_messages": 3,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 18,
        "difficulty": "HARD",
        "chat_name": "Snow World & TJUK ORDERING",
        "expected_customer_ids": ["CS01645", "CP00706", "CP00762"],
        "expected_ship_to_count": 3,
        "expected_ship_to_codes": [
            "POKKIDO JUNIOR",
            "PRASUK JAIN HOSPITALITY- KURLA (W)",
            "PJH PALLADIUM",
        ],
        "expected_product_count": 17,
        "hil_required": [
            {
                "item": "Davinci vanilla syrup",
                "sap_match": "K11IS01D28VAN001 — DAVINCI GOURMET VANILLA SYRUP 750ML",
                "reason": "Abbreviation and brand specifics — borderline matchable but low confidence",
            },
        ],
        "hil_reason": "Multi-customer group with 3 CardCodes, heavy chatter makes attribution hard",
        "key_challenges": [
            "3 different CardCodes in one WhatsApp group",
            "6 senders — hardest to attribute orders",
            "Heavy chatter mixed with orders",
            "Addition messages",
            "Need to split orders by correct CardCode",
        ],
        "max_acceptable_bot_messages": 6,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 19,
        "difficulty": "HARD",
        "chat_name": "Monarch Liberty",
        "expected_customer_ids": ["CM01282", "CM01292"],
        "expected_ship_to_count": 3,
        "expected_ship_to_codes": [
            "Monarch Liberty Worldwide Hsp LLP (EVE) -Santacruz",
            "MONARCH LIBERTY WORLDWIDE HOSPITALITY LLP (Powai)",
            "MONARCH LIBERTY WORLDWIDE HOSPITALITY LLP (WORLI)",
        ],
        "expected_product_count": 27,
        "hil_required": [
            {
                "item": "Tabasco (in S19 message)",
                "sap_match": "I02IC05T05TRE001 — T.SAUCE RED PEPPER 60ML",
                "reason": "Same Tobasco/T.SAUCE naming mismatch as S07",
            },
        ],
        "hil_reason": "Unmatchable product name + complex multi-outlet routing",
        "key_challenges": [
            "3 outlets (Powai, Santacruz, Worli) — must route correctly",
            "Corrections: '@Kumudini kindly add Demi glace powder 2 nos'",
            "Operational noise mixed with orders",
            "27 line items — high volume",
            "Tabasco → T.SAUCE mapping impossible without domain knowledge",
        ],
        "max_acceptable_bot_messages": 5,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 20,
        "difficulty": "HARD",
        "chat_name": "Good Food Concept",
        "expected_customer_ids": ["CG00313"],
        "expected_ship_to_count": 4,
        "expected_ship_to_codes": [
            "GOOD FOOD CONCEPT-MAHIM.W",
            "GOOD FOOD CONCEPT( DADAR E)",
            "GOOD FOOD CONCEPT (BOMBAY GYMKHANA)",
            "GOOD FOOD CONCEPT( GOREGAON E)",
        ],
        "expected_product_count": 25,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "4 ship-to addresses — complex routing",
            "Multiple orders from different locations",
            "2 senders, need to attribute correctly",
            "Location references embedded in messages",
        ],
        "max_acceptable_bot_messages": 5,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 21,
        "difficulty": "HARD",
        "chat_name": "DU Hospitality X TJUK",
        "expected_customer_ids": ["CI00164"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["INNERCIRCLE HOSPITALITY LLP"],
        "expected_product_count": 12,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "CANCELLATION: 'Please cancel perrier' — must remove from order",
            "Multiple additions over time",
            "4 senders in group",
            "Diet coke case conversion",
            "Schweppes abbreviation 'Sch' in truncated message",
        ],
        "max_acceptable_bot_messages": 5,
        "expected_stages": ["customer_id", "product", "qty"],
    },
    {
        "scenario_id": 22,
        "difficulty": "HARD",
        "chat_name": "Cremure Order Group",
        "expected_customer_ids": ["CC00815"],
        "expected_ship_to_count": 2,
        "expected_ship_to_codes": ["CREMURE (JOGESHWARI)", "CREMURE"],
        "expected_product_count": 8,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "CANCELLATION: 'Ye order ka bill cancel kardena' (Hindi: cancel this order's bill)",
            "Multiple locations: Malad, Jogeshwari",
            "kg conversions",
            "3 senders",
            "Hindi/Marathi mixed with English",
        ],
        "max_acceptable_bot_messages": 4,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 23,
        "difficulty": "HARD",
        "chat_name": "Bellona VS TJUK",
        "expected_customer_ids": ["CB00367"],
        "expected_ship_to_count": 3,
        "expected_ship_to_codes": [
            "BELLONA HOSPITALITY SERVICES LIMITED(LEGUME)",
            "BELLONA HOSPITALITY SERVICES LIMITED - CRAFT(EIGHT",
            "BELLONA HOSPITALITY SERVICES LTD-L.PAREL",
        ],
        "expected_product_count": 7,
        "hil_required": [
            {
                "item": "Perrier (in S23 Dobara order)",
                "sap_match": "Perrier exists in catalog but stock-out handling needed",
                "reason": "Stock availability question — model should flag, not assume",
            },
        ],
        "hil_reason": "Stock-out handling requires real-time inventory check",
        "key_challenges": [
            "3 outlets with different orders",
            "Case conversions",
            "Stock-out handling for Perrier",
            "2 senders attributing orders to outlets",
        ],
        "max_acceptable_bot_messages": 4,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 24,
        "difficulty": "HARD",
        "chat_name": "Snow World & TJUK ORDERING",
        "expected_customer_ids": ["CS01645", "CP00706", "CP00762"],
        "expected_ship_to_count": 3,
        "expected_ship_to_codes": [
            "THE GAME PALACIO",
            "KOA CAFÉ & BAR (VASHI)",
            "SNOW WORLD ENTERTAINMENT [NERUL E]",
        ],
        "expected_product_count": 36,
        "hil_required": [],
        "hil_reason": "",
        "key_challenges": [
            "CANCELLATION: 'PLZ CANCEL AMUL FRESH CREAM' + addition in same message",
            "3 CardCodes, 3 ship-to addresses",
            "4 senders",
            "36 line items — highest volume scenario",
            "Mixed products across outlets",
        ],
        "max_acceptable_bot_messages": 6,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
    {
        "scenario_id": 25,
        "difficulty": "HARD",
        "chat_name": "Kulturd Kombucha",
        "expected_customer_ids": ["CK00598"],
        "expected_ship_to_count": 1,
        "expected_ship_to_codes": ["KEPCHAKI MOMO-BANDRA.W"],
        "expected_product_count": 3,
        "hil_required": [
            {
                "item": "Hold message",
                "sap_match": "N/A",
                "reason": "'Hold' message at end — model should pause/escalate, not process. "
                          "Unclear if customer wants to cancel or just hold shipment.",
            },
            {
                "item": "Multiple outlet orders in messages for different businesses",
                "sap_match": "Only Kepchaki Momo order should be processed for this CardCode",
                "reason": "Messages mention Toast Pasta Bar (FOOD BY DEVIKA) and MASA BAKERY "
                          "(WHISK WITH US LLP) — these may be DIFFERENT CardCodes. "
                          "Model should not blindly process all.",
            },
        ],
        "hil_reason": "Ambiguous 'Hold' instruction + cross-business orders in one chat",
        "key_challenges": [
            "Niche products (Kombucha)",
            "'Hold' instruction — should trigger HIL, not assume",
            "Multiple businesses ordering in same chat",
            "Operational chatter about Reliance billing",
            "Only 3 SAP items but 7 messages — lots of noise",
        ],
        "max_acceptable_bot_messages": 4,
        "expected_stages": ["customer_id", "ship_to", "product", "qty"],
    },
]

# Build lookup
BENCHMARK_BY_ID = {b["scenario_id"]: b for b in BENCHMARKS}


# ============================================================
# API CALLERS
# ============================================================
def call_anthropic(model_id, system_prompt, user_prompt):
    """Call Claude API. Returns (text, input_tokens, output_tokens, error)."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "max_tokens": 8192,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=300,
    )
    data = resp.json()
    if resp.status_code != 200:
        return None, 0, 0, f"Error {resp.status_code}: {data.get('error', {}).get('message', str(data))}"
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]
    return text, data["usage"]["input_tokens"], data["usage"]["output_tokens"], None


def call_google(model_id, system_prompt, user_prompt):
    """Call Gemini API. Returns (text, input_tokens, output_tokens, error)."""
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": 8192},
        },
        timeout=300,
    )
    data = resp.json()
    if resp.status_code != 200:
        return None, 0, 0, f"Error {resp.status_code}: {data.get('error', {}).get('message', str(data))}"
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    usage = data.get("usageMetadata", {})
    return text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), None


def call_model(model_config, system_prompt, user_prompt):
    """Dispatch to correct API caller."""
    if model_config["provider"] == "anthropic":
        return call_anthropic(model_config["model_id"], system_prompt, user_prompt)
    elif model_config["provider"] == "google":
        return call_google(model_config["model_id"], system_prompt, user_prompt)
    return None, 0, 0, f"Unknown provider: {model_config['provider']}"


# ============================================================
# SYSTEM PROMPT
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

HUMAN-IN-LOOP (HIL) RULES:
- If you CANNOT confidently match a product to the catalogue (confidence < 60%), flag it for human review
- If a customer says "hold", "stop", or gives ambiguous instructions, flag for HIL — do NOT guess
- If quantity is far outside historical range (>3x or <0.2x), flag for HIL
- NEVER hallucinate a product match. It's better to flag for human review than guess wrong.

Output ONLY valid JSON in this exact format:
{
  "customer_identified": {
    "card_codes": ["CG00313"],
    "card_names": ["GOOD FOOD CONCEPT"],
    "confidence": "HIGH"
  },
  "orders": [
    {
      "ship_to": "SHIP-TO ADDRESS NAME or 'DEFAULT' if unclear",
      "ship_to_confidence": "HIGH/MEDIUM/LOW",
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
  "human_review_needed": [
    {
      "item": "description of what needs review",
      "reason": "why human review is needed",
      "original_text": "customer message text"
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
- If you cannot confidently match a product, add it to human_review_needed AND include it in orders with confidence=LOW
- Do NOT invent products. Only match to items in the provided catalogue.
- If the catalogue doesn't have a match, set item_code to "UNKNOWN" and keep the original text

FLAG RULES:
- GREEN: Exact item match + quantity in historical range + HIGH confidence
- YELLOW: Unit conversion applied, or quantity slightly outside typical range, or name-only fuzzy match
- RED: UNKNOWN item, LOW confidence, quantity far outside range, or first-time product for customer"""


# ============================================================
# PROMPT BUILDER
# ============================================================
def build_user_prompt(scenario):
    """Build the user prompt from a scenario in test_scenarios.json."""
    card_codes = ", ".join(scenario["card_codes"])
    card_names = ", ".join(scenario["card_names"])
    ship_addresses = scenario["ship_to_addresses"]

    prompt = "=== CUSTOMER CONTEXT ===\n"
    prompt += f"WhatsApp Group: {scenario['chat_name']}\n"
    prompt += f"Customer: {card_codes} — {card_names}\n"
    prompt += "Ship-to Addresses:\n"
    for addr in ship_addresses:
        prompt += f"  - {addr}\n"

    # Product catalogue from scenario
    prompt += "\n=== PRODUCT CATALOGUE ===\n"
    prompt += "ItemCode | ItemName | UoM | PackSize (pcs/case) | UnitWeight (kg)\n"
    prompt += "-" * 90 + "\n"

    # Load full product catalog for this scenario
    catalog_path = os.path.join(SCRIPT_DIR, "product_catalog.json")
    with open(catalog_path) as f:
        full_catalog = json.load(f)

    # Use historical patterns to identify relevant products
    hist_items = {h["item_code"] for h in scenario.get("historical_patterns", [])}
    relevant_products = [p for p in full_catalog if p["item_code"] in hist_items]

    # If too few, just use top historical items
    for p in relevant_products[:200]:
        pack = p.get("pack_size", 1) or 1
        weight = p.get("unit_weight_kg", "")
        weight_str = f"{weight}" if weight else ""
        prompt += f"{p['item_code']} | {p['item_name']} | {p.get('uom', 'PCS')} | {pack} | {weight_str}\n"

    # Historical order patterns
    prompt += "\n=== HISTORICAL ORDER PATTERNS ===\n"
    prompt += "Items this customer typically orders (sorted by frequency):\n"
    prompt += "ItemCode | ItemName | OrderCount | TypicalQty (min-max, median)\n"
    for h in scenario.get("historical_patterns", [])[:50]:
        prompt += (f"  {h['item_code']} | {h['item_name'][:45]} | "
                   f"{h['order_count']}x | {h['min_qty']:.0f}-{h['max_qty']:.0f} "
                   f"(med {h['median_qty']:.0f})\n")

    # WhatsApp messages
    prompt += "\n=== WHATSAPP MESSAGES (process these) ===\n"
    for conv in scenario["conversations_1to1"]:
        for m in conv["messages"]:
            role_tag = "CUSTOMER" if m["role"] == "customer" else "STAFF"
            sender = conv["sender"] if m["role"] == "customer" else m.get("sender", "BOT")
            prompt += f"[{m['time']}] [{role_tag}] {sender}: {m['text']}\n"

    return prompt


# ============================================================
# JSON EXTRACTION
# ============================================================
def extract_json_from_text(text):
    """Extract JSON from model response, handling code blocks and raw JSON."""
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


# ============================================================
# SCORING ENGINE
# ============================================================
def fuzzy_match(s1, s2):
    """Fuzzy string similarity ratio."""
    return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()


def score_scenario(model_output_json, sap_truth, benchmark):
    """
    Score model output against SAP ground truth + benchmark expectations.
    Returns comprehensive metrics dict.
    """
    result = {
        "valid_json": model_output_json is not None,
        # Customer ID
        "customer_id_correct": False,
        "customer_id_confidence": "NONE",
        # Ship-to
        "ship_to_count_expected": benchmark["expected_ship_to_count"],
        "ship_to_count_found": 0,
        "ship_to_accuracy": 0.0,
        # Product matching
        "products_expected": benchmark["expected_product_count"],
        "products_matched": 0,
        "products_accuracy": 0.0,
        # Quantity
        "qty_correct": 0,
        "qty_accuracy": 0.0,
        # HIL detection
        "hil_expected_count": len(benchmark["hil_required"]),
        "hil_detected_count": 0,
        "hil_detection_rate": 0.0,
        "hil_false_positives": 0,
        # Chattiness
        "response_char_count": 0,
        "response_line_count": 0,
        # Extra / missed
        "extra_items": 0,
        "missed_items": 0,
    }

    if not model_output_json:
        result["missed_items"] = len(sap_truth)
        return result

    raw_text = json.dumps(model_output_json)
    result["response_char_count"] = len(raw_text)
    result["response_line_count"] = raw_text.count("\n") + 1

    # --- Customer ID scoring ---
    cust_info = model_output_json.get("customer_identified", {})
    model_card_codes = cust_info.get("card_codes", [])
    if isinstance(model_card_codes, str):
        model_card_codes = [model_card_codes]
    expected_cards = set(benchmark["expected_customer_ids"])
    found_cards = set(model_card_codes)
    result["customer_id_correct"] = bool(expected_cards & found_cards)
    result["customer_id_confidence"] = cust_info.get("confidence", "NONE")

    # --- Ship-to scoring ---
    orders = model_output_json.get("orders", [])
    model_ship_tos = set()
    for order in orders:
        ship = order.get("ship_to", "").strip()
        if ship and ship.upper() != "DEFAULT":
            model_ship_tos.add(ship)

    expected_ships = set(benchmark["expected_ship_to_codes"])
    # Fuzzy match ship-to addresses
    matched_ships = 0
    for expected in expected_ships:
        for found in model_ship_tos:
            if fuzzy_match(expected, found) >= 0.5:
                matched_ships += 1
                break

    result["ship_to_count_found"] = len(model_ship_tos)
    if len(expected_ships) > 0:
        result["ship_to_accuracy"] = matched_ships / len(expected_ships)

    # --- Product & Quantity scoring ---
    model_lines = []
    for order in orders:
        for line in order.get("lines", []):
            model_lines.append({
                "item_code": line.get("item_code", "UNKNOWN"),
                "item_name": line.get("item_name", ""),
                "quantity": line.get("quantity", 0),
                "confidence": line.get("confidence", "LOW"),
                "flag": line.get("flag", "RED"),
            })

    matched_sap = set()
    qty_correct = 0

    for ml in model_lines:
        best_idx = None
        best_score = 0
        for i, st in enumerate(sap_truth):
            if i in matched_sap:
                continue
            if ml["item_code"] == st["item_code"]:
                score = 1.0
            else:
                score = fuzzy_match(ml.get("item_name", ""), st["description"])
            if score > best_score:
                best_score = score
                best_idx = i

        if best_idx is not None and best_score >= 0.4:
            matched_sap.add(best_idx)
            sap_qty = float(sap_truth[best_idx]["quantity"])
            try:
                model_qty = float(ml["quantity"])
            except (ValueError, TypeError):
                model_qty = 0

            if sap_qty > 0:
                diff_pct = abs(model_qty - sap_qty) / sap_qty
                if diff_pct <= 0.05:
                    qty_correct += 1
            elif abs(model_qty - sap_qty) < 0.01:
                qty_correct += 1

    products_matched = len(matched_sap)
    result["products_matched"] = products_matched
    result["products_accuracy"] = products_matched / len(sap_truth) if sap_truth else 0
    result["qty_correct"] = qty_correct
    result["qty_accuracy"] = qty_correct / products_matched if products_matched > 0 else 0
    result["extra_items"] = max(0, len(model_lines) - products_matched)
    result["missed_items"] = len(sap_truth) - products_matched

    # --- HIL detection scoring ---
    hil_items = model_output_json.get("human_review_needed", [])
    if not isinstance(hil_items, list):
        hil_items = []

    # Check if expected HIL items were flagged
    hil_detected = 0
    for expected_hil in benchmark["hil_required"]:
        expected_text = expected_hil["item"].lower()
        for flagged in hil_items:
            flagged_text = (flagged.get("item", "") + " " + flagged.get("reason", "")).lower()
            if fuzzy_match(expected_text, flagged_text) >= 0.3 or expected_text in flagged_text:
                hil_detected += 1
                break
        # Also check if it was flagged as LOW confidence in order lines
        for ml in model_lines:
            if ml["confidence"] == "LOW" and ml["flag"] == "RED":
                item_text = ml.get("item_name", "").lower()
                if fuzzy_match(expected_text, item_text) >= 0.3:
                    hil_detected += 1
                    break

    result["hil_detected_count"] = min(hil_detected, len(benchmark["hil_required"]))
    result["hil_detection_rate"] = (
        result["hil_detected_count"] / len(benchmark["hil_required"])
        if benchmark["hil_required"] else 1.0  # No HIL expected = perfect score
    )

    # HIL false positives: items flagged for review that aren't in expected HIL
    result["hil_false_positives"] = max(0, len(hil_items) - result["hil_detected_count"])

    return result


# ============================================================
# MAIN EVALUATION
# ============================================================
def run_benchmark(model_keys=None, scenario_ids=None, dry_run=False):
    """Run the full benchmark evaluation."""
    # Load scenarios
    scenarios_path = os.path.join(SCRIPT_DIR, "test_scenarios.json")
    with open(scenarios_path) as f:
        scenarios_data = json.load(f)
    all_scenarios = scenarios_data["scenarios"]

    # Filter scenarios if requested
    if scenario_ids:
        all_scenarios = [s for s in all_scenarios if s["scenario_id"] in scenario_ids]

    # Filter models
    if model_keys:
        models_to_run = {k: MODELS[k] for k in model_keys if k in MODELS}
    else:
        models_to_run = MODELS

    print("=" * 70)
    print("  WhatsApp Order Bot — Benchmark Evaluation System")
    print(f"  Models: {', '.join(m['name'] for m in models_to_run.values())}")
    print(f"  Scenarios: {len(all_scenarios)}")
    print(f"  Metrics: Customer ID, Ship-to, Product, Qty, Timing, Chattiness, HIL, Tokens, Cost")
    print("=" * 70)

    # Validate API keys
    for key, model in models_to_run.items():
        if model["provider"] == "anthropic" and not ANTHROPIC_KEY:
            print(f"  WARNING: No ANTHROPIC_API_KEY set. {model['name']} will fail.")
        if model["provider"] == "google" and not GEMINI_KEY:
            print(f"  WARNING: No GEMINI_API_KEY set. {model['name']} will fail.")

    if dry_run:
        print("\n  DRY RUN — checking setup only, no API calls.")
        print(f"\n  Scenarios loaded: {len(all_scenarios)}")
        for s in all_scenarios:
            b = BENCHMARK_BY_ID.get(s["scenario_id"], {})
            hil_count = len(b.get("hil_required", []))
            print(f"    S{s['scenario_id']:02d} [{s['difficulty']:<6}] {s['chat_name']:<30} "
                  f"SAP:{len(s['sap_truth']):>2} | HIL:{hil_count} | "
                  f"Challenges: {', '.join(b.get('key_challenges', [])[:2])}")
        return [], {}

    all_results = []
    model_summaries = {}

    for model_key, model_config in models_to_run.items():
        print(f"\n{'=' * 70}")
        print(f"  MODEL: {model_config['name']} ({model_config['model_id']})")
        print(f"{'=' * 70}")

        model_results = []
        total_input_tokens = 0
        total_output_tokens = 0
        total_cost = 0.0
        total_latency = 0.0

        for scenario in all_scenarios:
            sid = scenario["scenario_id"]
            benchmark = BENCHMARK_BY_ID.get(sid, {})
            sap_truth = scenario["sap_truth"]

            # Build prompt
            user_prompt = build_user_prompt(scenario)

            print(f"  S{sid:02d} [{scenario['difficulty']:<6}] "
                  f"{scenario['chat_name'][:28]:<28} "
                  f"(SAP:{len(sap_truth):>2}, HIL:{len(benchmark.get('hil_required', []))}) ... ",
                  end="", flush=True)

            # Call model
            start_time = time.time()
            try:
                text, in_tok, out_tok, error = call_model(model_config, SYSTEM_PROMPT, user_prompt)
            except Exception as e:
                text, in_tok, out_tok, error = None, 0, 0, f"Exception: {str(e)[:200]}"
            latency = time.time() - start_time

            if error:
                print(f"ERROR: {error[:80]}")
                result = {
                    "model": model_config["name"],
                    "model_key": model_key,
                    "scenario_id": sid,
                    "chat_name": scenario["chat_name"],
                    "difficulty": scenario["difficulty"],
                    "latency_s": latency,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0,
                    "cost_per_message": 0.0,
                    "response_char_count": 0,
                    "error": error[:200],
                    "valid_json": False,
                    "customer_id_correct": False,
                    "ship_to_accuracy": 0.0,
                    "products_accuracy": 0.0,
                    "qty_accuracy": 0.0,
                    "hil_detection_rate": 0.0,
                    "products_matched": 0,
                    "products_expected": len(sap_truth),
                    "qty_correct": 0,
                    "extra_items": 0,
                    "missed_items": len(sap_truth),
                    "hil_expected_count": len(benchmark.get("hil_required", [])),
                    "hil_detected_count": 0,
                    "hil_false_positives": 0,
                    "raw_output": "",
                }
                model_results.append(result)
                all_results.append(result)
                continue

            # Calculate cost
            cost = (in_tok / 1_000_000) * model_config["input_cost_per_M"] + \
                   (out_tok / 1_000_000) * model_config["output_cost_per_M"]
            total_input_tokens += in_tok
            total_output_tokens += out_tok
            total_cost += cost
            total_latency += latency

            # Parse and score
            parsed = extract_json_from_text(text)
            scores = score_scenario(parsed, sap_truth, benchmark)

            # Cost per message (customer messages in this scenario)
            cust_msg_count = sum(
                sum(1 for m in c["messages"] if m["role"] == "customer")
                for c in scenario["conversations_1to1"]
            )
            cost_per_msg = cost / max(cust_msg_count, 1)

            result = {
                "model": model_config["name"],
                "model_key": model_key,
                "scenario_id": sid,
                "chat_name": scenario["chat_name"],
                "difficulty": scenario["difficulty"],
                "latency_s": round(latency, 2),
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cost": round(cost, 6),
                "cost_per_message": round(cost_per_msg, 6),
                "response_char_count": scores["response_char_count"],
                "error": "",
                "raw_output": text or "",
                **{k: v for k, v in scores.items() if k != "response_char_count"},
            }
            model_results.append(result)
            all_results.append(result)

            # Print summary line
            cid = "Y" if scores["customer_id_correct"] else "N"
            print(f"CustID:{cid} | Ship:{scores['ship_to_accuracy']:.0%} | "
                  f"Prod:{scores['products_matched']}/{len(sap_truth)} ({scores['products_accuracy']:.0%}) | "
                  f"Qty:{scores['qty_accuracy']:.0%} | "
                  f"HIL:{scores['hil_detected_count']}/{scores['hil_expected_count']} | "
                  f"Chars:{scores['response_char_count']:,} | "
                  f"${cost:.4f} | {latency:.1f}s")

            time.sleep(0.3)  # Rate limiting

        # Model summary
        if model_results:
            valid = [r for r in model_results if r["valid_json"]]
            summary = {
                "model": model_config["name"],
                "scenarios_run": len(model_results),
                "valid_json_pct": len(valid) / len(model_results) if model_results else 0,
                "avg_customer_id_accuracy": sum(1 for r in valid if r["customer_id_correct"]) / max(len(valid), 1),
                "avg_ship_to_accuracy": sum(r["ship_to_accuracy"] for r in valid) / max(len(valid), 1),
                "avg_product_accuracy": sum(r["products_accuracy"] for r in valid) / max(len(valid), 1),
                "avg_qty_accuracy": sum(r["qty_accuracy"] for r in valid) / max(len(valid), 1),
                "avg_hil_detection": sum(r["hil_detection_rate"] for r in valid) / max(len(valid), 1),
                "total_input_tokens": total_input_tokens,
                "total_output_tokens": total_output_tokens,
                "total_cost": round(total_cost, 4),
                "avg_cost_per_scenario": round(total_cost / max(len(model_results), 1), 4),
                "avg_cost_per_message": round(
                    sum(r["cost_per_message"] for r in valid) / max(len(valid), 1), 6
                ),
                "avg_latency_s": round(total_latency / max(len(model_results), 1), 2),
                "avg_response_chars": round(
                    sum(r["response_char_count"] for r in valid) / max(len(valid), 1)
                ),
                "total_products_matched": sum(r["products_matched"] for r in valid),
                "total_products_expected": sum(r["products_expected"] for r in valid),
                "total_qty_correct": sum(r["qty_correct"] for r in valid),
                "total_extra_items": sum(r["extra_items"] for r in valid),
                "total_missed_items": sum(r["missed_items"] for r in valid),
            }
            model_summaries[model_key] = summary

            print(f"\n  --- {model_config['name']} Summary ---")
            print(f"  Customer ID:   {summary['avg_customer_id_accuracy']:.0%}")
            print(f"  Ship-to:       {summary['avg_ship_to_accuracy']:.0%}")
            print(f"  Product Match: {summary['avg_product_accuracy']:.0%}")
            print(f"  Qty Accuracy:  {summary['avg_qty_accuracy']:.0%}")
            print(f"  HIL Detection: {summary['avg_hil_detection']:.0%}")
            print(f"  Avg Chars:     {summary['avg_response_chars']:,}")
            print(f"  Avg Latency:   {summary['avg_latency_s']}s")
            print(f"  Total Cost:    ${summary['total_cost']:.4f}")
            print(f"  Avg Cost/Msg:  ${summary['avg_cost_per_message']:.6f}")
            print(f"  Tokens:        {total_input_tokens:,} in / {total_output_tokens:,} out")

    return all_results, model_summaries


# ============================================================
# OUTPUT — JSON RESULTS
# ============================================================
def save_results(all_results, model_summaries):
    """Save results as JSON (no openpyxl dependency required)."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Detailed results (without raw_output for size)
    detailed = []
    for r in all_results:
        row = {k: v for k, v in r.items() if k != "raw_output"}
        detailed.append(row)

    output = {
        "metadata": {
            "timestamp": timestamp,
            "models": list(model_summaries.keys()),
            "scenarios_count": len(set(r["scenario_id"] for r in all_results)),
            "benchmarks_version": "1.0",
        },
        "model_summaries": model_summaries,
        "detailed_results": detailed,
        "benchmark_definitions": BENCHMARKS,
    }

    outpath = os.path.join(SCRIPT_DIR, f"benchmark_results_{timestamp}.json")
    with open(outpath, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to: {outpath}")

    # Also save raw outputs separately
    raw_path = os.path.join(SCRIPT_DIR, f"benchmark_raw_outputs_{timestamp}.json")
    raw = {}
    for r in all_results:
        key = f"{r['model_key']}__S{r['scenario_id']:02d}"
        raw[key] = {
            "model": r["model"],
            "scenario_id": r["scenario_id"],
            "raw_output": r.get("raw_output", ""),
        }
    with open(raw_path, "w") as f:
        json.dump(raw, f, indent=2)
    print(f"  Raw outputs saved to: {raw_path}")

    return outpath


# ============================================================
# COMPARISON TABLE (terminal output)
# ============================================================
def print_comparison_table(model_summaries):
    """Print a formatted comparison table to terminal."""
    if not model_summaries:
        return

    print(f"\n{'=' * 90}")
    print("  FINAL MODEL COMPARISON")
    print(f"{'=' * 90}")

    headers = [
        "Metric", *[s["model"][:20] for s in model_summaries.values()]
    ]
    rows = [
        ("Customer ID Acc", *[f"{s['avg_customer_id_accuracy']:.0%}" for s in model_summaries.values()]),
        ("Ship-to Acc", *[f"{s['avg_ship_to_accuracy']:.0%}" for s in model_summaries.values()]),
        ("Product Match", *[f"{s['avg_product_accuracy']:.0%}" for s in model_summaries.values()]),
        ("Qty Accuracy", *[f"{s['avg_qty_accuracy']:.0%}" for s in model_summaries.values()]),
        ("HIL Detection", *[f"{s['avg_hil_detection']:.0%}" for s in model_summaries.values()]),
        ("Avg Latency", *[f"{s['avg_latency_s']}s" for s in model_summaries.values()]),
        ("Avg Chars/Response", *[f"{s['avg_response_chars']:,}" for s in model_summaries.values()]),
        ("Total Cost", *[f"${s['total_cost']:.4f}" for s in model_summaries.values()]),
        ("Avg Cost/Message", *[f"${s['avg_cost_per_message']:.6f}" for s in model_summaries.values()]),
        ("Total Tokens (in)", *[f"{s['total_input_tokens']:,}" for s in model_summaries.values()]),
        ("Total Tokens (out)", *[f"{s['total_output_tokens']:,}" for s in model_summaries.values()]),
        ("Valid JSON %", *[f"{s['valid_json_pct']:.0%}" for s in model_summaries.values()]),
        ("Products Matched", *[f"{s['total_products_matched']}/{s['total_products_expected']}" for s in model_summaries.values()]),
        ("Items Missed", *[f"{s['total_missed_items']}" for s in model_summaries.values()]),
        ("Items Extra", *[f"{s['total_extra_items']}" for s in model_summaries.values()]),
    ]

    # Calculate column widths
    col_widths = [max(len(str(row[i])) for row in [headers] + list(rows)) + 2
                  for i in range(len(headers))]

    # Print header
    header_line = "  " + "".join(h.ljust(w) for h, w in zip(headers, col_widths))
    print(header_line)
    print("  " + "-" * sum(col_widths))

    # Print rows
    for row in rows:
        line = "  " + "".join(str(v).ljust(w) for v, w in zip(row, col_widths))
        print(line)

    # Difficulty breakdown
    print(f"\n  --- By Difficulty ---")
    # We'd need to recalculate from detailed results, but summary is enough for now


# ============================================================
# ENTRY POINT
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="WhatsApp Order Bot Benchmark Eval")
    parser.add_argument("--models", type=str, default=None,
                        help="Comma-separated model keys: sonnet,haiku,gemini")
    parser.add_argument("--scenarios", type=str, default=None,
                        help="Comma-separated scenario IDs: 1,2,3")
    parser.add_argument("--dry-run", action="store_true",
                        help="Check setup only, no API calls")
    args = parser.parse_args()

    model_keys = None
    if args.models:
        model_keys = [k.strip() for k in args.models.split(",")]

    scenario_ids = None
    if args.scenarios:
        scenario_ids = [int(s.strip()) for s in args.scenarios.split(",")]

    all_results, model_summaries = run_benchmark(
        model_keys=model_keys,
        scenario_ids=scenario_ids,
        dry_run=args.dry_run,
    )

    if all_results:
        print_comparison_table(model_summaries)
        outpath = save_results(all_results, model_summaries)
        print(f"\n  Done! Results at: {outpath}")


if __name__ == "__main__":
    main()
