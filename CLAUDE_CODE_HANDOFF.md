# WhatsApp Order Bot — Full Context Handoff for Claude Code

## 1. PROJECT OVERVIEW

**Company:** TJUK — a food distribution company based in Mumbai, India.

**Problem:** TJUK receives orders via WhatsApp group chats. Currently, staff manually read these messages and enter orders into SAP Business One (SAP B1). This is slow, error-prone, and doesn't scale.

**Solution:** An AI-powered bot that reads WhatsApp messages, identifies orders (ignoring chatter), matches products to the SAP catalogue, converts quantities to SAP's unit (PCS), and outputs structured JSON ready for SAP import.

**Stack:** SAP Business One (MS SQL Server backend), WhatsApp Business API (future), Python for eval/scoring.

---

## 2. THE CORE CHALLENGE — QUANTITY CONVERSION

SAP records everything in **PCS (pieces)**. Customers order in **cases, boxes, kg, btl, pkt**, etc. The bot MUST convert:

### Conversion Rules

| Customer Says | Conversion | Formula | Example |
|---|---|---|---|
| "X case/box/crate" | Case → PCS | qty = X × SalPackUn | "3 case coke 300ml" → PackSize=24 → 72 PCS |
| "X kg" | Weight → PCS | qty = X ÷ BWeight1 | "5kg amul butter 500gms" → BWeight=0.5 → 10 PCS |
| "X pcs/nos/units/btl/pkt" | Direct | qty = X | "2 btls sweet chilli sauce" → 2 PCS |
| Just a number | Direct | qty = X | "amul butter 10" → 10 PCS |

### Key SAP Fields for Conversion

- **SalPackUn** (Sales Packing Unit): Number of pieces per case/box. Found in OITM table. Example: SalPackUn=24 means 24 units per case.
- **BWeight1** (Weight per unit in KG): Used for kg→PCS conversion. Found in OITM table. Example: BWeight1=0.5 means each unit weighs 0.5kg.
- Both fields are in the `weightpack` sheet of the SAP data export.

### Edge Cases Discovered

- "box" sometimes means individual unit (not case) — e.g., "6 box Baskin Robbins" = 6 PCS (each box is one tub)
- Some products have BWeight1=0 or NULL — need fallback logic
- Customers use Marathi, Hindi, abbreviations, typos (e.g., "Cahshew" for Cashew, "pic" for pieces)
- "Jogeshwari" in a message is a delivery location, not a product
- Orders span multiple messages: "add X to yesterday's order" must be handled

---

## 3. DATA FILES

All files are in the same directory.

### whatsapp testing data.xlsx (SAP Export)

| Sheet | Rows | Key Columns |
|---|---|---|
| Customer master | 7,036 | CardCode, CardName, Phone1, Phone2, Cellular, E_Mail, GroupCode, SlpCode |
| Product master | 2,138 | ItemCode, ItemName, FrgnName, ItmsGrpCod, ItmsGrpNam, SalUnitMsr, NumInSale, InvntryUom |
| Orders | 11,070 | DocEntry, DocNum, DocDate, DocDueDate, CardCode, CardName, ShipToCode, Address2, ItemCode, Dscription, Quantity, Price, LineTotal, U_WhatsApp |
| Ship to | 82 | CardCode, CardName, Address, Street, City, State, ZipCode, GSTRegnNo |
| weightpack | 2,140 | ItemCode, ItemName, SalPackUn, BWeight1, SWeight1, SalUnitMsr, InvntryUom |

### whatsapp_master_dataset.xlsx (WhatsApp Messages)

| Sheet | Rows | Key Columns |
|---|---|---|
| All Messages | 1,930 | #, Chat Source, Date, Time, Sender, Message Text, Type, Order Group |
| Orders Grouped | 696 | Order Group, Chat Source, Date, Time, Sender, Location/Outlet, Full Order Text, Msgs |
| Summary | 34 | Stats |

- Date format in WhatsApp data: `DD/MM/YY`
- 17 unique WhatsApp group chats
- Messages include orders, chatter, delivery queries, acknowledgments, Marathi/Hindi text

---

## 4. CUSTOMER → WHATSAPP GROUP MAPPING

Each WhatsApp group corresponds to one or more SAP CardCodes:

```python
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
```

Multi-CardCode groups mean the customer has multiple entities (e.g., Snow World has 3 different sub-businesses ordering through one WhatsApp group).

---

## 5. TEST SCENARIOS (24 Total)

These are the 24 scenarios used for benchmarking, organized by difficulty:

```python
TEST_CASES = [
    # EASY (8) — simple orders, few messages, clear intent
    ("Good Food Concept", "2025-12-03", "EASY"),
    ("Cremure Order Group", "2025-10-30", "EASY"),
    ("Jalapeno Food Ordering", "2025-10-13", "EASY"),
    ("Ketan Oberoi Tower", "2025-08-13", "EASY"),
    ("Urban Gourmet UGIPL", "2025-09-01", "EASY"),
    ("Laxmi Foods Pillsbury", "2025-10-07", "EASY"),
    ("Cremure Order Group", "2026-01-26", "EASY"),
    ("DU Hospitality X TJUK", "2026-01-07", "EASY"),
    # MEDIUM (8) — multiple orders, corrections, multi-product
    ("Good Food Concept", "2025-12-25", "MEDIUM"),
    ("Bellona VS TJUK", "2026-02-09", "MEDIUM"),
    ("Jalapeno Food Ordering", "2025-12-18", "MEDIUM"),
    ("Urban Gourmet UGIPL", "2025-09-26", "MEDIUM"),
    ("Ketan Oberoi Tower", "2026-01-20", "MEDIUM"),
    ("Cremure Order Group", "2026-01-16", "MEDIUM"),
    ("BAWA GROUP ORDERING", "2025-10-27", "MEDIUM"),
    ("DU Hospitality X TJUK", "2026-02-19", "MEDIUM"),
    # HARD (8) — multi-customer chats, cancellations, multilingual, lots of chatter
    ("Snow World & TJUK ORDERING", "2026-02-06", "HARD"),
    ("Monarch Liberty", "2026-01-02", "HARD"),
    ("Good Food Concept", "2025-12-15", "HARD"),
    ("DU Hospitality X TJUK", "2026-01-22", "HARD"),
    ("Cremure Order Group", "2025-12-08", "HARD"),
    ("Bellona VS TJUK", "2026-02-20", "HARD"),
    ("Snow World & TJUK ORDERING", "2026-02-17", "HARD"),
    ("Kulturd Kombucha", "2026-02-06", "HARD"),
]
```

---

## 6. GROUND TRUTH BENCHMARK

Manually verified against actual SAP order data. This is the answer key.

### Benchmark Numbers

| Metric | Count | Description |
|---|---|---|
| Order Messages | 69 | WhatsApp messages that are actual orders (not chatter) |
| Product Lines | 100 | Total product lines across all 69 order messages |
| Verifiable Lines | 97 | Lines that could be matched to SAP orders |
| Verified PCS | 96 | Lines where PCS quantity matches SAP exactly |

### Per-Scenario Breakdown

| S# | Difficulty | Customer | Date | Recall | Products | Qty |
|---|---|---|---|---|---|---|
| 1 | EASY | Good Food Concept | 2025-12-03 | 1 | 3 | 3 |
| 2 | EASY | Cremure Order Group | 2025-10-30 | 1 | 1 | 1 |
| 3 | EASY | Jalapeno Food Ordering | 2025-10-13 | 1 | 1 | 1 |
| 4 | EASY | Ketan Oberoi Tower | 2025-08-13 | 1 | 0* | 0* |
| 5 | EASY | Urban Gourmet UGIPL | 2025-09-01 | 1 | 5 | 5 |
| 6 | EASY | Laxmi Foods Pillsbury | 2025-10-07 | 2 | 2 | 2 |
| 7 | EASY | Cremure Order Group | 2026-01-26 | 1 | 3 | 2** |
| 8 | EASY | DU Hospitality X TJUK | 2026-01-07 | 2 | 3 | 3 |
| 9 | MEDIUM | Good Food Concept | 2025-12-25 | 2 | 3 | 3 |
| 10 | MEDIUM | Bellona VS TJUK | 2026-02-09 | 6 | 4 | 4 |
| 11 | MEDIUM | Jalapeno Food Ordering | 2025-12-18 | 2 | 6 | 6 |
| 12 | MEDIUM | Urban Gourmet UGIPL | 2025-09-26 | 2 | 6 | 6 |
| 13 | MEDIUM | Ketan Oberoi Tower | 2026-01-20 | 2 | 7 | 7 |
| 14 | MEDIUM | Cremure Order Group | 2026-01-16 | 2 | 4 | 4 |
| 15 | MEDIUM | BAWA GROUP ORDERING | 2025-10-27 | 3 | 1 | 1 |
| 16 | MEDIUM | DU Hospitality X TJUK | 2026-02-19 | 1 | 2 | 2 |
| 17 | HARD | Snow World & TJUK ORDERING | 2026-02-06 | 10 | 9 | 9 |
| 18 | HARD | Monarch Liberty | 2026-01-02 | 4 | 8 | 8 |
| 19 | HARD | Good Food Concept | 2025-12-15 | 4 | 8 | 8 |
| 20 | HARD | DU Hospitality X TJUK | 2026-01-22 | 5 | 7 | 7 |
| 21 | HARD | Cremure Order Group | 2025-12-08 | 5 | 3 | 3 |
| 22 | HARD | Bellona VS TJUK | 2026-02-20 | 4 | 6 | 6 |
| 23 | HARD | Snow World & TJUK ORDERING | 2026-02-17 | 4 | 2 | 2 |
| 24 | HARD | Kulturd Kombucha | 2026-02-06 | 3 | 3 | 3 |
| | | **TOTALS** | | **69** | **97** | **96** |

*S4: Marathi chatter scenario — order references products not in SAP catalogue for that customer
**S7: 1 line has ambiguous unit that doesn't match SAP exactly

### Ground Truth Data Structure

The full ground truth is in `ground_truth_builder.py`. Each scenario contains:

```python
GROUND_TRUTH[1] = {
    "difficulty": "EASY",
    "customer": "Good Food Concept",
    "date": "2025-12-03",
    "messages": [
        {"sender": "+91 98206 81818", "time": "4:44 pm",
         "text": "5kg amul butter 3kg amul cheese block 2 btls sweet chilli sauce",
         "is_order": True},
        {"sender": "Phone With Kumu", "time": "4:52 pm",
         "text": "area?", "is_order": False},
        ...
    ],
    "expected_order_lines": [
        {"wa_text": "5kg amul butter", "sap_itemcode": "J01IS06A02BUT001",
         "sap_desc": "AMUL-BUTTER IP 500GMS", "expected_pcs": 10,
         "conversion": "weight", "wa_qty": 5, "wa_unit": "kg",
         "pack_size": 40, "bweight": 0.5,
         "calc": "5 / 0.5 = 10 PCS"},
        ...
    ],
    "sap_total_lines": 26,
    "wa_matched_lines": 3,
    "note": "SAP has 26 lines across 3 orders but only 3 items mentioned in WA."
}
```

---

## 7. SYSTEM PROMPT (Current Best — v2)

This is the prompt sent to every model. It's the core of the bot:

```
You are an AI order processing engine for TJUK, a food distribution company in Mumbai.
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
- RED: UNKNOWN item, LOW confidence, quantity far outside range, or first-time product for customer
```

---

## 8. USER PROMPT STRUCTURE

Each API call sends a user prompt built from SAP data + WhatsApp messages:

```
=== CUSTOMER CONTEXT ===
WhatsApp Group: {chat_name}
Customer: {card_codes} — {card_names}
Ship-to Addresses:
  - {address1}
  - {address2}

=== PRODUCT CATALOGUE ===
ItemCode | ItemName | UoM | PackSize (pcs/case) | UnitWeight (kg)
---------------------------------------------------------------------------
J01IS06A02BUT001 | AMUL-BUTTER IP 500GMS | PCS | 40 | 0.5
...

=== HISTORICAL ORDER PATTERNS ===
Items this customer typically orders (sorted by frequency):
ItemCode | ItemName | OrderCount | TypicalQty (min-max, median)
  J01IS06A02BUT001 | AMUL-BUTTER IP 500GMS | 15x | 5-20 (med 10)
  ...

=== WHATSAPP MESSAGES (process these) ===
[4:44 pm] +91 98206 81818: 5kg amul butter 3kg amul cheese block 2 btls sweet chilli sauce
[4:52 pm] Phone With Kumu: area?
[4:57 pm] +91 98206 81818: Dadar parsee gymkhana
```

---

## 9. EVAL RESULTS — FINAL SCORECARD

### Model Comparison (Scored Against Verified Ground Truth)

| Model | Recall | Product Match | Qty Match | Tokens (24) | Cost (24) |
|---|---|---|---|---|---|
| **Claude Sonnet 4.5** | **100%** (69/69) | **100%** (97/97) | **90.6%** (87/96) | 155,337 | $0.89 |
| Claude Haiku 4.5 | 100% (69/69) | 100% (97/97) | 79.2% (76/96) | 159,153 | $0.25 |
| GPT-5.2 | 100% (69/69) | 97.9% (95/97) | 89.4% (84/94) | 131,270 | $0.57 |
| GPT-4o | 98.6% (68/69) | 96.9% (94/97) | 84.9% (79/93) | 126,414 | $0.49 |
| Gemini 2.0 Flash | 100% (66/66*) | 100% (96/96*) | 78.9% (75/95) | 147,458 | $0.02 |
| Claude Opus 4.6 | 85.5% (59/69) | 90.7% (88/97) | 92.0% (80/87) | 166,328 | $1.75 |
| Gemini 2.5 Flash | 59.4% (41/69) | 68.0% (66/97) | 96.9% (63/65) | 143,094 | $0.09 |

*Gemini 2.0 Flash: 1 scenario errored (S15), so denominator is 66/96 not 69/97

### Scoring Definitions

- **Recall**: Of the 69 real order messages in WhatsApp, how many did the model identify as orders?
- **Product Match**: Of the 97 product lines in ground truth, how many did the model match to the correct SAP ItemCode?
- **Qty Match**: Of the matched products, how many had the correct PCS quantity (within ±5% tolerance)?

### Key Findings

1. **Sonnet 4.5 is the clear winner**: Perfect recall, perfect product matching, best qty accuracy
2. **Quantity conversion is the hardest part**: Even Sonnet only gets 91% — the case/kg→PCS conversion challenge
3. **Opus 4.6 and Gemini 2.5 Flash had reliability issues**: Dropped entire scenarios (likely output parsing failures or timeouts)
4. **Haiku is a budget option**: Same recall/product as Sonnet but 12% worse on qty, at 1/4 the cost
5. **The 9% qty gap in Sonnet is addressable**: Better prompt engineering, more conversion examples, edge case handling

### Token Usage Per Model (Full 24 Scenarios)

| Model | Input Tokens | Output Tokens | Total Cost |
|---|---|---|---|
| Claude Opus 4.6 | 120,229 | 46,099 | $1.7536 |
| Claude Sonnet 4.5 | 120,205 | 35,132 | $0.8876 |
| Claude Haiku 4.5 | 120,205 | 38,948 | $0.2520 |
| GPT-4o | 103,260 | 23,154 | $0.4897 |
| GPT-5.2 | 103,236 | 28,034 | $0.5731 |
| Gemini 2.0 Flash | 115,727 | 31,731 | $0.0243 |
| Gemini 2.5 Flash | 120,440 | 22,654 | $0.0928 |

### Model Pricing (per 1M tokens)

| Model | Input $/M | Output $/M | Model ID |
|---|---|---|---|
| Claude Opus 4.6 | $5.00 | $25.00 | claude-opus-4-6 |
| Claude Sonnet 4.5 | $3.00 | $15.00 | claude-sonnet-4-5-20250929 |
| Claude Haiku 4.5 | $0.80 | $4.00 | claude-haiku-4-5-20251001 |
| GPT-4o | $2.50 | $10.00 | gpt-4o |
| GPT-5.2 | $1.75 | $14.00 | gpt-5.2 |
| Gemini 2.0 Flash | $0.10 | $0.40 | gemini-2.0-flash |
| Gemini 2.5 Flash | $0.30 | $2.50 | gemini-2.5-flash |

---

## 10. PRODUCTION COST ESTIMATE

From actual SAP data (last 3 months):
- **~283 orders/day** on working days
- **Estimated ~60% via WhatsApp = ~170 orders/day**
- **Sonnet cost per order: ~$0.037** (avg ~5,000 input + ~1,460 output tokens)

| Metric | Value |
|---|---|
| Daily cost | ~$6.29 |
| Monthly cost | ~$189 |
| Yearly cost | ~$2,295 |

---

## 11. CODE FILES

### eval_harness_v2.py (~997 lines)
The main evaluation script. Runs all 7 models across 24 scenarios.

**What it does:**
1. Loads SAP data (orders, products, ship-to, weightpack) and WhatsApp messages
2. Builds enriched prompts per scenario (customer context + product catalogue with SalPackUn/BWeight1 + historical order patterns + WhatsApp messages)
3. Calls each model's API (Anthropic, OpenAI, Google)
4. Parses JSON from model response
5. Scores against raw SAP (not ground truth — that's step 2)
6. Outputs `WhatsApp_Eval_Results_v2.xlsx` + `raw_model_outputs.json`

**Key API details:**
- Anthropic: max_tokens=8192, timeout=300s, handles multi-block content (thinking models)
- OpenAI GPT-4o: max_tokens=8192, system role, timeout=180s
- OpenAI GPT-5.2: max_completion_tokens=16384, developer role (reasoning model), NO reasoning_effort throttle
- Gemini: maxOutputTokens=8192, timeout=300s, thinking enabled by default for 2.5 Flash

### ground_truth_builder.py (~1,490 lines)
Contains the verified ground truth for all 24 scenarios. Each scenario has tagged messages (order vs chatter) and expected order lines with SAP ItemCodes, PCS quantities, and conversion calculations. Generates `ground_truth_benchmark.xlsx`.

### score_against_ground_truth.py (~527 lines)
Reads `raw_model_outputs.json`, scores each model's output against the ground truth benchmark. Outputs `ground_truth_scores.xlsx` with the final scorecard (Model, Recall, Product %, Qty %, Tokens, Cost).

### verify_ground_truth.py (~133 lines)
Cross-checks every ground truth line against actual SAP order data. Confirms: Product 97/97 = 100%, Qty 96/97 = 99%.

---

## 12. API KEYS

```
ANTHROPIC_API_KEY=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
OPENAI_API_KEY=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
GEMINI_API_KEY=AIzaSyBQrlO-UCoWx7Aik-Re0LkI9FX6TMpkGPY
```

---

## 13. API CALLER PATTERNS

### Anthropic (Claude)
```python
payload = {
    "model": model_id,  # e.g., "claude-sonnet-4-5-20250929"
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
    json=payload, timeout=300,
)
# Response may have multiple content blocks (thinking models)
text = ""
for block in data["content"]:
    if block["type"] == "text":
        text += block["text"]
input_tokens = data["usage"]["input_tokens"]
output_tokens = data["usage"]["output_tokens"]
```

### OpenAI (GPT-4o / GPT-5.2)
```python
is_reasoning = model_id.startswith("gpt-5")
payload = {
    "model": model_id,
    "messages": [
        {"role": "developer" if is_reasoning else "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ],
}
if is_reasoning:
    payload["max_completion_tokens"] = 16384  # NOT max_tokens for reasoning models
else:
    payload["max_tokens"] = 8192
# DO NOT set reasoning_effort — full reasoning is needed for this task
resp = requests.post(
    "https://api.openai.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
    json=payload, timeout=180,
)
text = data["choices"][0]["message"]["content"]
input_tokens = data["usage"]["prompt_tokens"]
output_tokens = data["usage"]["completion_tokens"]
```

### Google (Gemini)
```python
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
text = data["candidates"][0]["content"]["parts"][0]["text"]
input_tokens = data["usageMetadata"]["promptTokenCount"]
output_tokens = data["usageMetadata"]["candidatesTokenCount"]
```

---

## 14. SCORING LOGIC

### How Scoring Works (score_against_ground_truth.py)

For each scenario, for each model:

1. **Parse JSON** from model's raw output (handles markdown code blocks, finds balanced braces)
2. **Recall**: Count how many of the ground truth's order messages the model found (by matching expected product lines)
3. **Product Match**: For each ground truth line, find the best matching model output line by ItemCode (exact match priority) or fuzzy name match (threshold ≥ 0.5)
4. **Qty Match**: For matched products, check if model's PCS quantity is within ±5% of ground truth's expected PCS

### Key Implementation Detail
The scorer matches ground truth lines to model output lines greedily (best match first, no double-counting). A model that outputs extra lines (false positives) isn't penalized on recall/product/qty — only on the "Extra Items" metric.

---

## 15. KNOWN ISSUES & NEXT STEPS

### Why Qty is Only 91% (Even for Sonnet)

Common failure modes in the 9% qty miss:
- Ambiguous "box" vs "case" interpretation (S3: "6box" = 6 PCS or 6 cases?)
- Products where SalPackUn or BWeight1 is missing/zero in SAP
- Multi-line messages where qty gets attributed to wrong product
- "Add X more" messages where the addition logic fails

### Recommended Improvements

1. **Prompt engineering**: Add more conversion examples, especially edge cases
2. **Few-shot examples**: Include 2-3 worked examples in the system prompt showing exact conversions
3. **Structured conversion table**: Instead of prose rules, give a lookup table of common products with exact conversion factors
4. **Post-processing validation**: After model returns JSON, programmatically verify conversions against SalPackUn/BWeight1
5. **Confidence-based routing**: HIGH confidence → auto-process, MEDIUM → human review, LOW → reject

### Production Architecture (Future)

1. WhatsApp Business API → receives messages
2. Group messages by chat + time window (e.g., 30-min batches)
3. Call Sonnet 4.5 API with enriched prompt
4. Parse JSON response
5. Validate conversions programmatically
6. Push to SAP B1 via DI API or Service Layer
7. Send confirmation back to WhatsApp group

---

## 16. HOW TO RUN THE EVAL

### Step 1: Run all models
```bash
python eval_harness_v2.py
```
Outputs: `WhatsApp_Eval_Results_v2.xlsx` + `raw_model_outputs.json`
Takes ~30-45 min for 7 models × 24 scenarios.

### Step 2: Score against ground truth
```bash
python score_against_ground_truth.py
```
Outputs: `ground_truth_scores.xlsx` — the final scorecard.

### Step 3: Verify ground truth (optional, one-time)
```bash
python verify_ground_truth.py
```
Confirms ground truth matches SAP data.

---

## 17. SAP B1 SQL REFERENCE

### Get daily order count (MS SQL Server)
```sql
SELECT
    CAST(T0.DocDate AS DATE) AS OrderDate,
    COUNT(DISTINCT T0.DocNum) AS OrderCount
FROM ORDR T0
WHERE T0.DocDate >= DATEADD(DAY, -90, GETDATE())
GROUP BY CAST(T0.DocDate AS DATE)
ORDER BY OrderDate DESC
```

### Get product conversion factors
```sql
SELECT T0.ItemCode, T0.ItemName, T0.SalPackUn, T0.BWeight1, T0.SalUnitMsr
FROM OITM T0
WHERE T0.ItemCode IN (/* item codes */)
```

### Get customer orders for a date
```sql
SELECT T0.DocNum, T0.DocDate, T0.CardCode, T0.CardName,
       T1.ItemCode, T1.Dscription, T1.Quantity, T1.Price
FROM ORDR T0
INNER JOIN RDR1 T1 ON T0.DocEntry = T1.DocEntry
WHERE T0.CardCode = 'CG00313'
  AND T0.DocDate = '2025-12-03'
```
