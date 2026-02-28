# WhatsApp Order Bot — Session Handoff Prompt

Copy everything below this line and paste as your first message in the next Claude conversation.

---

I'm building a WhatsApp order processing agent for TJUK, a food distribution company in Mumbai. They supply hotels, restaurants, and caterers. Customers send orders via WhatsApp groups (text messages like "Coke 3 case, Amul butter 5kg"). The bot needs to parse these messages, match products to SAP B1 catalogue, convert quantities to SAP units, and output structured JSON for order entry.

## What's been done

### 1. Data Collection
- **WhatsApp messages scraped** from 17 chat exports (1,930 messages)
- **SAP data pulled** via SQL queries: Customer master (7,036), Product master (2,138 items), Orders history (11,070 lines / 2,959 orders, Aug 2025–Feb 2026), Ship-to addresses (82)
- **Master dataset built**: 696 order events identified and grouped from the 1,930 messages

### 2. Product Scope Document
- 7-step processing framework documented in WhatsApp_Order_Bot_Plan.docx (approved)
- Covers: message classification → customer identification → product matching → quantity conversion → order structuring → confidence scoring → human review flagging

### 3. Evaluation Framework
- **24 curated test scenarios** across 3 difficulty levels (EASY/MEDIUM/HARD) with SAP ground truth (293 line items)
- **Multi-model eval harness** (eval_harness.py) — runs same 24 scenarios through multiple models via REST APIs
- **Self-scoring** on full 696-order dataset against SAP WhatsApp-tagged orders

### 4. Benchmark Results (24 test scenarios, same input & ground truth)

| Model | Recall | Precision | Qty Accuracy | Cost (24 tests) |
|---|---|---|---|---|
| Claude Sonnet 4.5 | 57.7% | 80.9% | 22.5% | $0.580 |
| Gemini 2.0 Flash | 57.3% | 76.0% | 21.5% | $0.016 |
| Claude Haiku 4.5 | 44.0% | 85.4% | 14.7% | $0.161 |
| Claude Opus 4 (self-score on full data) | 92% recall (adj), 65% product, 20% qty | — | — | N/A |
| GPT-4o / GPT-4o-mini | API errors — need to fix keys/endpoint | — | — | — |

**Key finding**: Gemini Flash nearly matches Sonnet at 36x lower cost ($1.23/month vs $43.56/month at 60 orders/day).

### 5. Self-Scoring on Full Dataset (696 orders, 1930 messages)
- **Recall**: 71% raw, **92% adjusted** (64 of 81 misses had no WhatsApp data in export at all)
- **Misclassification rate**: 4% (11 real orders wrongly tagged as operational)
- **False positive rate**: 0.1% (1 out of 696 wasn't a real order)
- **Product match**: 65%
- **Quantity match**: 20%

## THE BIG PROBLEM TO FIX: Quantity Accuracy (20% across all models)

### Root Cause Analysis (data-proven)
SAP records **everything in PCS** (11,068 of 11,070 order lines). Customers speak in kg, cases, boxes. Three conversion gaps:

**A. Case/Box multiplier unknown**
- Customer: "3 case coke" → Model returns: 3 CASE
- SAP records: 72 PCS (1 case = 24 cans)
- Proof: ALL Coke Can 300ML orders in SAP are multiples of 24 (24, 48, 72, 96, 120, 144...)

**B. Weight-to-PCS conversion**
- Customer: "5kg amul butter" → Model returns: 5 KG
- SAP records: 10 PCS (each piece is AMUL BUTTER 500GMS, so 5000g ÷ 500g = 10)
- 80% of products have weight in their name (1,711 of 2,138)

**C. Already-correct cases**
- Customer: "12 cheese block" → SAP: 12 PCS (1KG block = 1 PCS)
- Models sometimes try to convert when they shouldn't

### The Fix (needs to be implemented this session)
1. **Enrich product catalogue with 2 new columns**:
   - `pack_weight_g` — auto-extract from product names via regex (80% coverage)
   - `pcs_per_case` — TJUK business knowledge needed (e.g., Coke=24, certain items=6, 12 etc.)

2. **Add conversion instructions to the prompt**: "If customer says X kg and pack is Y grams, quantity = (X × 1000) ÷ Y PCS"

3. **Use historical order ranges as sanity check**: Build typical qty ranges per customer×item from 11,070 historical order lines (1,133 unique customer-item combos with history)

4. **Human-in-loop flagging tiers**:
   - GREEN (auto-process): Exact item match, qty in historical range, HIGH confidence
   - YELLOW (review): Unit conversion applied, qty outside typical range, name-only match, add-ons/modifications
   - RED (must review): UNKNOWN item, LOW confidence, qty anomaly (e.g., 3 when typical is 72), first-time product for customer

## Files (all in the user's selected folder)

| File | What it is |
|---|---|
| `whatsapp_master_dataset.xlsx` | 1,930 messages from 17 chats. Sheets: All Messages, Orders Grouped (696), Summary |
| `whatsapp testing data.xlsx` | SAP data. Sheets: Customer master (7,036), Product master (2,138), Orders (11,070 lines), Ship to (82) |
| `WhatsApp_Eval_Test_Dataset.xlsx` | 24 test scenarios. Sheets: Scenario Variations, Coverage Analysis, WhatsApp Messages (INPUT), SAP Orders (TRUTH), Customer Mapping |
| `eval_harness.py` | Python eval script — calls 5 models via REST API, scores against ground truth, outputs results xlsx |
| `WhatsApp_Eval_Results.xlsx` | Results from eval run. Sheets: Model Comparison, By Difficulty, Detailed Results, Cost Analysis, Raw Outputs |
| `Model_Benchmark_Results.xlsx` | Clean comparison table with recall, precision, qty accuracy, cost |
| `WhatsApp_Order_Bot_Plan.docx` | Product scope document (approved) |
| `run_eval.bat` | Windows batch file to install deps and run eval |

## SAP Data Available

Already pulled and in `whatsapp testing data.xlsx`:
- **Customer master** (OCRD): 7,036 customers, CardCode, CardName, GroupCode, SlpCode (34 reps)
- **Product master** (OITM): 2,138 items — ItemCode, ItemName, FrgnName, ItmsGrpCod, ItmsGrpNam, SalUnitMsr (99.8% PCS), NumInSale (all=1), InvntryUom, LastPurPrc, LstSalDate
- **Orders** (ORDR/RDR1): 11,070 lines, 2,959 orders, 27 customers, Aug 2025–Feb 2026. Has Comments field that tags WhatsApp orders ("kumud wtsp", "anushka--whats" etc — 70% are WA-tagged)
- **Ship-to** (CRD1): 82 addresses for the 30 matched CardCodes

**What we might still need from SAP**:
- Case/box sizes per product (UoM Group tables: OUGP, UGP1) — `SELECT T0."UgpCode", T0."UgpName", T1."UomCode", T1."BaseQty", T1."AltQty" FROM OUGP T0 JOIN UGP1 T1 ON T0."UgpEntry"=T1."UgpEntry"`
- Or ask Brady's team for a simple case-size mapping for top 200 products

## API Keys
- Anthropic: sk-ant-api03-f31rMJkPCBArFfLcE5kVBe_SFHcvFN9bpETge1meXqd-3kQ2oGDlHxYVngrVH7mOr3OR0GW7joufSZFd3Vpb5Q-8ePCjwAA
- Google Gemini: AIzaSyBQrlO-UCoWx7Aik-Re0LkI9FX6TMpkGPY
- OpenAI: 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

## What to do in this session

1. **Build enriched product catalogue** — extract pack_weight_g from product names, add conversion factors
2. **Update the prompt** with explicit conversion instructions and the enriched catalogue format
3. **Add historical order ranges** as context per customer-item for sanity checking
4. **Design the flags/confidence output format** for human-in-loop
5. **Fix GPT-4o/4o-mini** API calls in eval_harness.py (they returned errors last time)
6. **Rerun eval** with the improved prompt + enriched catalogue and compare before/after quantity accuracy
7. Ideally, pull UoM Group data from SAP if we can get case sizes

My name is Brady, email bharathmechtce@gmail.com. I'm doing this myself (no separate dev). The eval harness runs on my Windows machine locally (sandbox can't make outbound API calls).
