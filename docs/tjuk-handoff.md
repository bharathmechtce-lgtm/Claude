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
| "X case/box/crate" | Case to PCS | qty = X * SalPackUn | "3 case coke 300ml" PackSize=24 = 72 PCS |
| "X kg" | Weight to PCS | qty = X / BWeight1 | "5kg amul butter 500gms" BWeight=0.5 = 10 PCS |
| "X pcs/nos/units/btl/pkt" | Direct | qty = X | "2 btls sweet chilli sauce" = 2 PCS |
| Just a number | Direct | qty = X | "amul butter 10" = 10 PCS |
### Key SAP Fields for Conversion
- **SalPackUn** (Sales Packing Unit): Number of pieces per case/box. Found in OITM table.
- **BWeight1** (Weight per unit in KG): Used for kg to PCS conversion. Found in OITM table.
### Edge Cases Discovered
- "box" sometimes means individual unit (not case) — e.g., "6 box Baskin Robbins" = 6 PCS
- Some products have BWeight1=0 or NULL — need fallback logic
- Customers use Marathi, Hindi, abbreviations, typos
- "Jogeshwari" in a message is a delivery location, not a product
- Orders span multiple messages: "add X to yesterday's order"
---
## 3. DATA FILES
### whatsapp testing data.xlsx (SAP Export)
| Sheet | Rows | Key Columns |
|---|---|---|
| Customer master | 7,036 | CardCode, CardName, Phone1, Phone2, Cellular, E_Mail |
| Product master | 2,138 | ItemCode, ItemName, FrgnName, ItmsGrpCod, ItmsGrpNam |
| Orders | 11,070 | DocEntry, DocNum, DocDate, CardCode, ItemCode, Quantity, Price |
| Ship to | 82 | CardCode, CardName, Address, Street, City, State |
| weightpack | 2,140 | ItemCode, ItemName, SalPackUn, BWeight1, SWeight1 |
### whatsapp_master_dataset.xlsx (WhatsApp Messages)
| Sheet | Rows | Key Columns |
|---|---|---|
| All Messages | 1,930 | Chat Source, Date, Time, Sender, Message Text, Type |
| Orders Grouped | 696 | Order Group, Chat Source, Date, Sender, Full Order Text |
| Summary | 34 | Stats |
---
## 4. CUSTOMER TO WHATSAPP GROUP MAPPING
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
---
## 5. TEST SCENARIOS (24 Total)
8 EASY, 8 MEDIUM, 8 HARD scenarios organized by difficulty.
---
## 6. GROUND TRUTH BENCHMARK
| Metric | Count |
|---|---|
| Order Messages | 69 |
| Product Lines | 100 |
| Verifiable Lines | 97 |
| Verified PCS | 96 |
---
## 7. EVAL RESULTS — FINAL SCORECARD
| Model | Recall | Product Match | Qty Match | Cost (24) |
|---|---|---|---|---|
| **Claude Sonnet 4.5** | **100%** (69/69) | **100%** (97/97) | **90.6%** (87/96) | $0.89 |
| Claude Haiku 4.5 | 100% (69/69) | 100% (97/97) | 79.2% (76/96) | $0.25 |
| GPT-5.2 | 100% (69/69) | 97.9% (95/97) | 89.4% (84/94) | $0.57 |
| GPT-4o | 98.6% (68/69) | 96.9% (94/97) | 84.9% (79/93) | $0.49 |
| Gemini 2.0 Flash | 100% (66/66*) | 100% (96/96*) | 78.9% (75/95) | $0.02 |
| Claude Opus 4.6 | 85.5% (59/69) | 90.7% (88/97) | 92.0% (80/87) | $1.75 |
| Gemini 2.5 Flash | 59.4% (41/69) | 68.0% (66/97) | 96.9% (63/65) | $0.09 |
### Key Findings
1. Sonnet 4.5 is the clear winner: Perfect recall, perfect product matching, best qty accuracy
2. Quantity conversion is the hardest part: Even Sonnet only gets 91%
3. Haiku is a budget option: Same recall/product as Sonnet but 12% worse on qty, at 1/4 the cost
---
## 8. PRODUCTION COST ESTIMATE
- ~283 orders/day on working days
- ~60% via WhatsApp = ~170 orders/day
- Sonnet cost per order: ~$0.037

| Metric | Value |
|---|---|
| Daily cost | ~$6.29 |
| Monthly cost | ~$189 |
| Yearly cost | ~$2,295 |
---
## 9. SYSTEM PROMPT (Current Best — v2)
The full system prompt is documented in eval_harness_v2.py. Key elements:
- Customer context (CardCode, CardName, ship-to addresses)
- Product catalogue with pack sizes and weights
- Historical order patterns
- Quantity conversion rules (case, weight, direct)
- Flag rules (GREEN/YELLOW/RED)
- JSON output format
---
## 10. API KEYS
> **REDACTED** — API keys for Anthropic, OpenAI, and Gemini are stored securely and NOT committed to version control. Use environment variables.
```
ANTHROPIC_API_KEY=<set in environment>
OPENAI_API_KEY=<set in environment>
GEMINI_API_KEY=<set in environment>
```
---
## 11. SCORING LOGIC
- **Recall**: Of 69 real order messages, how many did the model identify as orders?
- **Product Match**: Of 97 product lines, how many matched to correct SAP ItemCode?
- **Qty Match**: Of matched products, how many had correct PCS quantity (within +/-5%)?
---
## 12. KNOWN ISSUES & NEXT STEPS
### Why Qty is Only 91% (Even for Sonnet)
- Ambiguous "box" vs "case" interpretation
- Products where SalPackUn or BWeight1 is missing/zero
- Multi-line messages where qty gets attributed to wrong product
- "Add X more" messages where addition logic fails
### Recommended Improvements
1. Better prompt engineering with more conversion examples
2. Few-shot examples in system prompt
3. Structured conversion lookup table
4. Post-processing validation against SalPackUn/BWeight1
5. Confidence-based routing: HIGH = auto-process, MEDIUM = human review, LOW = reject
---
*Last updated: 28 February 2026*
