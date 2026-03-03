# WhatsApp Order Bot — Session Handoff (2026-03-03)

> **Copy this entire document as context for a new Claude Code session.**
> **Owner:** Brady (bharathmechtce@gmail.com)
> **Repo:** `bharathmechtce-lgtm/Claude`
> **Active Branch:** `claude/review-structure-logs-COrMg`

---

## 1. PROJECT IN ONE PARAGRAPH

TJUK is a food distribution company in Mumbai. Their customers (hotels, restaurants, caterers) order via WhatsApp group chats — messages like "Coke 3 case, Amul butter 5kg". We're building an AI bot that reads these messages, matches products to a SAP Business One catalogue, converts quantities to SAP's unit (PCS), and outputs structured JSON for order entry. The core challenge is **quantity conversion** (case→PCS, kg→PCS) and **multi-outlet routing** (one WhatsApp group can contain orders for multiple delivery addresses).

---

## 2. GIT BRANCHES

| Branch | Purpose | Status |
|---|---|---|
| `master` | Stable base | Not actively updated |
| `claude/review-structure-logs-COrMg` | **CURRENT** — testing infrastructure, simulator, failure analysis | Active, latest work |
| `claude/review-whatsapp-bot-testing-XKmb1` | Earlier testing work (merged into current) | Merged/inactive |
| `claude/upload-docs-DTBR0` | Documentation uploads | Inactive |

**Always develop on:** `claude/review-structure-logs-COrMg`

---

## 3. REPO STRUCTURE

```
Claude/
├── .env.example              # API key template (ANTHROPIC, OPENAI, GEMINI, WhatsApp)
├── .gitignore
├── Caddyfile                 # Reverse proxy config (whatsapporderbot.duckdns.org)
├── docker-compose.yml        # Webhook deployment
├── FOLDER_STRUCTURE.md
│
├── src/                      # APPLICATION CODE
│   ├── core/
│   │   ├── prompts.py        # Shared system prompt + extraction prompt
│   │   └── client_loader.py  # Client config loader
│   ├── client_adapters/
│   │   ├── generic_adapter.py
│   │   └── sap_b1_adapter.py # SAP Business One adapter
│   └── webhook/
│       ├── app.py            # Flask webhook endpoint
│       ├── Dockerfile
│       └── requirements.txt
│
├── clients/                  # CLIENT DATA (multi-tenant)
│   ├── TJUK/
│   │   ├── config/client_config.json
│   │   ├── data/
│   │   │   ├── Product master_mar.xlsx      # 2,138 items
│   │   │   ├── whatsapp testing data.xlsx   # SAP export (customers, products, orders, ship-to, weightpack)
│   │   │   ├── whatsapp_master_dataset.xlsx # 1,930 WhatsApp messages
│   │   │   ├── all_chats_extracted.txt
│   │   │   └── whatsapp_*.xlsx
│   │   └── chats/                           # Raw WhatsApp exports (.zip)
│   ├── ACS/                                 # Second client (Arvind Snacks, Chennai)
│   │   ├── config/client_config.json
│   │   └── chats/                           # 30+ WhatsApp chat exports
│   └── _template/                           # Template for new clients
│
├── testing/                  # EVALUATION & BENCHMARKING
│   ├── benchmarks/
│   │   ├── test_scenarios.json              # 25 scenarios (EASY/MEDIUM/HARD) with SAP ground truth
│   │   ├── product_catalog.json             # Product catalogue for LLM context
│   │   ├── ground_truth_benchmark.xlsx
│   │   ├── WhatsApp_Eval_Test_Dataset.xlsx
│   │   └── *.xlsx                           # Various benchmark datasets
│   ├── eval/
│   │   ├── eval_harness.py                  # Original 7-model eval harness
│   │   ├── eval_harness_v2.py               # Enhanced eval with SAP truth filtering
│   │   └── scorer_utils.py                  # Shared: fuzzy_match(), extract_json(), filter_testable_items()
│   ├── scenarios/
│   │   ├── simulate_1to1.py    ★            # Interactive 1-to-1 simulator (Haiku/Sonnet/Gemini)
│   │   ├── benchmark_eval.py                # Multi-model benchmark runner
│   │   ├── run_all_haiku.py                 # Full 25-scenario Haiku test
│   │   ├── two_agent_sim.py                 # Opus-as-customer vs Haiku-as-bot
│   │   ├── deterministic_eval.py            # Deterministic eval harness
│   │   ├── build_scenarios.py               # Builds test_scenarios.json from raw data
│   │   └── chat_ui.html                     # Browser-based chat UI
│   └── results/
│       ├── comparison_20260303_145017.json   # ★ LATEST: Haiku vs Gemini, 42 ship_to combos
│       ├── failure_report.txt                # ★ LATEST: 16 failing scenarios detailed analysis
│       ├── failure_report.json               # Machine-readable failure data
│       ├── sim_logs/                         # Per-simulation conversation logs
│       └── *.json, *.xlsx                    # Historical results
│
├── docs/                     # DOCUMENTATION
│   ├── architecture.md       # System architecture
│   ├── process-flow.md       # Order processing flow
│   ├── api-reference.md      # API docs
│   ├── deployment-guide.md   # Deployment instructions
│   ├── sap-b1-service-layer-integration.md
│   ├── product-backlog.md
│   ├── revenue-model.md
│   └── *.md                  # Various docs
│
├── project_management/       # HANDOFF & PLANNING
│   ├── CLAUDE_CODE_HANDOFF.md               # ★ Comprehensive handoff (system prompt, scoring, API patterns)
│   ├── CLAUDE_CODE_HANDOFF_ROOT.md
│   ├── NEXT_SESSION_PROMPT.md               # Quick-start prompt for new sessions
│   ├── WHATSAPP_ORDERING_BOT_PROJECT_BRIEF.md
│   └── WhatsApp_Order_Bot_Plan.docx         # Original product scope (approved)
│
└── data_exchange/            # File exchange area
    ├── inbox/
    └── processed/
```

---

## 4. WHAT WAS DONE THIS SESSION

### A. Created Interactive 1-to-1 Simulator (`testing/scenarios/simulate_1to1.py`)

A 740-line interactive tool that simulates the WhatsApp bot processing customer messages one-by-one with configurable batch windows:

**Workflow:**
1. Select difficulty (EASY/MEDIUM/HARD)
2. Choose test scenario from filtered list
3. Select LLM model (Haiku $1/$5, Sonnet $3/$15, Gemini $0.30/$2.50)
4. Configure batch window (seconds of silence before processing)
5. Messages sent one-by-one with simulated delays
6. Bot processes batches, extracts JSON, scores against SAP truth

**Features:**
- Multi-model support (Anthropic + Google APIs)
- Token & cost tracking per batch and total
- Conversation logging to `testing/results/sim_logs/`
- Color-coded comparison tables (MATCH/EXTRA/MISSED/QTY_MISMATCH)
- Correction rounds (up to 3) for failed extractions

### D. Fixed 3 Critical Bugs in Testing Infrastructure

**Bug 1 — model_id not passed on final batch** (`simulate_1to1.py:592`):
When messages were processed in the final batch, `call_llm()` was called without
`model_id`, defaulting to Haiku regardless of the user's model selection. This means
previous Gemini/Sonnet results for multi-batch scenarios may have had their final batch
processed by Haiku. **Fixed.**

**Bug 2 — Fuzzy matching on SAP item codes** (`scorer_utils.py:304`):
The scorer was fuzzy-matching item codes, not just names. SAP codes from the same vendor
share long prefixes (e.g., `J01IP17A02CHE001` vs `J01IC21A02CRE001` = 81% similar),
causing cheese to match cream, cumin to match chilli, etc. Also raised match threshold
from 0.4 to 0.5 (eliminates false positives, lowest legitimate match is 0.73). **Fixed.**

**Bug 3 — Missing PackSize/UnitWeight in LLM prompt** (`simulate_1to1.py:140`):
The simulator was NOT loading `product_catalog.json` or passing `catalog_by_code` to the
prompt builder. The LLM received item names and order counts but **no conversion factors**
(PackSize, UnitWeight). Every other test script (`run_all_haiku.py`, `deterministic_eval.py`,
etc.) correctly loads and passes the catalog. **Fixed.** Also corrected file paths from
`testing/scenarios/` to `testing/benchmarks/`.

**Impact:** Previous Haiku vs Gemini comparison results are unreliable due to bugs 1 and 3.
Re-running is recommended after these fixes.

### B. Ran Full Haiku vs Gemini Comparison (42 ship_to combinations)

**Results file:** `testing/results/comparison_20260303_145017.json` (20,117 lines)

| Model | Overall Score | Complete Orders | Avg Score |
|---|---|---|---|
| **Haiku 4.5** | 34/42 passed (81%) | 34 complete | 87.5 avg |
| **Gemini 2.5 Flash** | 32/42 passed (76%) | 32 complete | 73.8 avg |

### C. Generated Detailed Failure Report (16 failures)

**File:** `testing/results/failure_report.txt` (556 lines)

Every failing scenario+ship_to combination documented with:
- Exact customer messages
- SAP expected items (product, code, quantity)
- LLM output (what it actually produced)
- Per-item status table
- Root cause analysis

---

## 5. KEY FINDINGS — FAILURE ANALYSIS

### Gemini Failures (10 total)
| Pattern | Count | Scenarios |
|---|---|---|
| **Zero output** (no parseable JSON at all) | 6 | S03, S04, S07, S11, S18-Pokkido, S20-Gymkhana |
| Partial match with extras | 2 | S09-Gymkhana, S15 |
| Item misidentification | 2 | S01, S18-Kurla |

**Critical insight:** 6 of Gemini's 10 failures are complete JSON output failures (0 items, 0 correction rounds). Fixing the output parsing/prompting could jump Gemini from 76% to ~88%+.

### Haiku Failures (8 total)
| Pattern | Count | Scenarios |
|---|---|---|
| **Extra items from other outlets** (multi-location confusion) | 4 | S09, S15, S20-Dadar, S24 |
| **Quantity aggregation across outlets** | 1 | S25 |
| Wrong ship_to name (items all correct) | 1 | S12 |
| SAP expected vastly different from customer message | 1 | S22 |
| Complex multi-entity confusion | 1 | S18-Pokkido |

**Critical insight:** Haiku's main weakness is **multi-outlet routing** — it includes items belonging to other delivery addresses.

### Both Failed (4 scenarios)
- **S09** (Good Food Concept, 2 ship_tos): Multi-outlet split confusion
- **S15** (Hotel Bawa Regency): Multi-hotel order, can't isolate items per hotel
- **S18** (Pokkido Junior): Extremely complex multi-entity order

---

## 6. BENCHMARK RESULTS (Historical — 7 Models, 24 Scenarios)

From `project_management/CLAUDE_CODE_HANDOFF.md`:

| Model | Recall | Product Match | Qty Match | Cost (24 tests) |
|---|---|---|---|---|
| **Claude Sonnet 4.5** | **100%** | **100%** | **90.6%** | $0.89 |
| Claude Haiku 4.5 | 100% | 100% | 79.2% | $0.25 |
| GPT-5.2 | 100% | 97.9% | 89.4% | $0.57 |
| GPT-4o | 98.6% | 96.9% | 84.9% | $0.49 |
| Gemini 2.0 Flash | 100% | 100% | 78.9% | $0.02 |
| Claude Opus 4.6 | 85.5% | 90.7% | 92.0% | $1.75 |
| Gemini 2.5 Flash | 59.4% | 68.0% | 96.9% | $0.09 |

**Winner:** Sonnet 4.5 — perfect recall, perfect product match, best qty accuracy.

---

## 7. THE CORE CHALLENGE — QUANTITY CONVERSION

SAP records everything in **PCS (pieces)**. Customers order in cases, boxes, kg, btl, pkt.

| Customer Says | Conversion | Formula | Example |
|---|---|---|---|
| "X case/box" | Case → PCS | qty = X × SalPackUn | "3 case coke 300ml" → PackSize=24 → 72 PCS |
| "X kg" | Weight → PCS | qty = X ÷ BWeight1 | "5kg amul butter 500gms" → BWeight=0.5 → 10 PCS |
| "X pcs/btl/pkt" | Direct | qty = X | "2 btls sweet chilli sauce" → 2 PCS |

Key SAP fields: `SalPackUn` (pieces per case), `BWeight1` (weight per unit in KG).

---

## 8. DATA AVAILABLE

### SAP Export (`clients/TJUK/data/whatsapp testing data.xlsx`)
| Sheet | Rows | Key Columns |
|---|---|---|
| Customer master | 7,036 | CardCode, CardName, Phone, GroupCode, SlpCode |
| Product master | 2,138 | ItemCode, ItemName, FrgnName, SalUnitMsr |
| Orders | 11,070 | DocEntry, DocDate, CardCode, ItemCode, Quantity, Price |
| Ship to | 82 | CardCode, CardName, Address, Street, City |
| weightpack | 2,140 | ItemCode, SalPackUn, BWeight1, SWeight1 |

### Test Scenarios (`testing/benchmarks/test_scenarios.json`)
- 25 scenarios (8 EASY, 9 MEDIUM, 8 HARD)
- Each has: customer messages, SAP ground truth, ship_to addresses, product catalogue
- 42 unique ship_to combinations when expanded

### WhatsApp Groups → SAP Customer Mapping
```python
CHAT_TO_CARDS = {
    "Good Food Concept": ["CG00313"],
    "Cremure Order Group": ["CC00815"],
    "Jalapeno Food Ordering": ["CJ00241"],
    "Snow World & TJUK ORDERING": ["CS01645", "CP00706", "CP00762"],
    "Monarch Liberty": ["CM01282", "CM01292"],
    "Urban Gourmet UGIPL": ["CU00010", "CU00107", "CU00126"],
    "BAWA GROUP ORDERING": ["CK00392", "CH00177"],
    "Kulturd Kombucha": ["CK00598"],
    "Bellona VS TJUK": ["CB00367"],
    "Ketan Oberoi Tower": ["CO00008"],
    "Laxmi Foods Pillsbury": ["CL00037"],
    "Pillsbury & Bake Wish": ["CB00642"],
    "DU Hospitality X TJUK": ["CI00164"],
    "Sankalp Orders": ["CS01197", "CL00050"],
}
```

---

## 9. API KEYS & ENVIRONMENT

`.env` file (not committed):
```
ANTHROPIC_API_KEY=<key>
OPENAI_API_KEY=<key>
GEMINI_API_KEY=<key>
WHATSAPP_TOKEN=<key>
WHATSAPP_PHONE_NUMBER_ID=<id>
WEBHOOK_VERIFY_TOKEN=tjuk-bot-verify-2024
WHATSAPP_APP_SECRET=<key>
```

---

## 10. HOW TO RUN THINGS

### Interactive Simulator (single scenario)
```bash
cd /home/user/Claude
python testing/scenarios/simulate_1to1.py
# Prompts: difficulty → scenario → model → batch window → run
```

### Full Haiku Test (all 25 scenarios)
```bash
python testing/scenarios/run_all_haiku.py
```

### Multi-Model Benchmark (7 models × 24 scenarios)
```bash
python testing/eval/eval_harness_v2.py
```

---

## 11. WHAT NEEDS TO BE DONE NEXT

### Immediate (High Priority)
1. **Re-run Haiku vs Gemini comparison** — Previous results are unreliable due to 3 bugs now fixed (model_id fallback, missing PackSize/UnitWeight in prompt, false match scoring). A fresh run will give accurate baselines.

2. **Fix Gemini JSON output failures** — 6 of 10 Gemini failures were zero-output. Some may have been caused by the model_id bug (final batch falling back to Haiku). After re-run, investigate any remaining zero-output cases — possibly add explicit JSON mode for Google API.

3. **Fix multi-outlet routing** — Haiku's #1 failure mode. When a message contains items for multiple delivery addresses, the bot includes items meant for other outlets. Need better prompt instructions for outlet isolation.

4. **Improve correction round effectiveness** — After 3 correction rounds, models still can't remove EXTRA items or add MISSED ones. The correction prompt needs to be more explicit.

### Medium Priority
5. **Add Sonnet to simulator** — It's already in the model menu but hasn't been benchmarked in the new 1-to-1 conversational format (only in the batch eval harness).

6. **Address quantity conversion edge cases** — Some SAP expected quantities are wildly different from what customers literally order (e.g., S22: customer says "Chocolate tea time 10kg", SAP expects 56 units across 3 different products). Now that PackSize/UnitWeight are in the prompt, many of these should self-correct.

7. **Ship_to name normalization** — S12 failed purely because Haiku used "Govandi Central Kitchen" instead of "URBAN GOURMET INDIA PVT LTD (27 BAKE HOUSE)" — all items were correct.

### Fixed This Session (no longer needed)
- ~~model_id bug on final batch~~ — Fixed
- ~~Fuzzy matching on item codes causing false positives~~ — Fixed, now exact-match only
- ~~Missing PackSize/UnitWeight in simulator prompt~~ — Fixed, catalog now loaded
- ~~Match threshold too low (0.4)~~ — Raised to 0.5
- ~~File paths pointing to wrong directory~~ — Fixed to use testing/benchmarks/

### Future / Production
8. **Production webhook** — Flask app in `src/webhook/app.py` with WhatsApp Business API integration
9. **SAP B1 Service Layer integration** — Push confirmed orders into SAP
10. **Second client (ACS)** — Arvind Snacks in Chennai, chat exports already in `clients/ACS/`
11. **Cost optimization** — Gemini at $0.02 vs Sonnet at $0.89 for same 24 scenarios

---

## 12. KEY FILES TO READ FIRST

| Priority | File | Why |
|---|---|---|
| 1 | `project_management/CLAUDE_CODE_HANDOFF.md` | Full technical context (prompts, API patterns, scoring logic, SAP fields) |
| 2 | `testing/results/failure_report.txt` | Detailed failure analysis for all 16 failing scenarios |
| 3 | `testing/scenarios/simulate_1to1.py` | The interactive simulator (most recent code) |
| 4 | `testing/benchmarks/test_scenarios.json` | The 25 test scenarios with ground truth |
| 5 | `src/core/prompts.py` | The system prompt used by the bot |
| 6 | `testing/eval/scorer_utils.py` | Shared scoring utilities |

---

## 13. PRODUCTION COST ESTIMATES

From actual SAP data (last 3 months):
- ~283 orders/day on working days
- ~60% via WhatsApp = ~170 orders/day

| Model | Cost/Order | Daily | Monthly | Yearly |
|---|---|---|---|---|
| Sonnet 4.5 | $0.037 | $6.29 | $189 | $2,295 |
| Haiku 4.5 | $0.010 | $1.70 | $51 | $621 |
| Gemini 2.5 Flash | $0.004 | $0.68 | $20 | $248 |

---

## 14. SECOND CLIENT — ACS (ARVIND SNACKS)

A second client from Chennai with 30+ WhatsApp chat exports already uploaded to `clients/ACS/chats/`. Config file exists at `clients/ACS/config/client_config.json`. No evaluation work done yet — this is ready for the multi-tenant expansion phase.

---

*Generated: 2026-03-03 | Branch: claude/review-structure-logs-COrMg*
