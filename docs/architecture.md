# WhatsApp Order Bot — Architecture Document

## Architecture Decisions (Finalized 28 Feb 2026)

### Core Decisions

1. **Real-time conversational bot** — not batch. Order comes in → bot responds (confirm or question) → conversation continues until final confirmation.
2. **1-to-1 chats only for orders** — customers message the WhatsApp Business API number directly. Group chats are read-only for context, bot does NOT respond or accept orders from groups.
3. **Cloud Brain + Thin Local Agent** — 90% of logic on cloud (Brady controls), 10% on client machine (ERP connector).
4. **Pull-based agent** — local agent polls cloud for confirmed orders. No inbound ports needed at client site.
5. **Per-client deployment** — separate config, separate order queue, shared codebase.
6. **Google Sheets as visibility/HIL layer** — no custom UI to build.
7. **Sales reps can order on behalf of customers** — not just direct customers. A salesperson's phone maps to multiple customer accounts.
8. **Bot is a full order assistant** — not just an order extractor. Handles price queries, catalog questions, delivery dates, and order modifications (with cutoff rules).
9. **All input types from day 1** — text, images (handwritten lists, product photos), PDFs (purchase orders), OCR. Not deferred to a later phase.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                      CLOUD SERVER (Hetzner VPS)                      │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐     │
│  │  WEBHOOK RECEIVER (FastAPI)                                  │     │
│  │  POST /webhook — receives all WhatsApp messages              │     │
│  │  Routes by phone_number_id → client config                   │     │
│  │  Filters: only 1-to-1 messages (ignores group messages)      │     │
│  └──────────────────────────┬──────────────────────────────────┘     │
│                              │                                        │
│  ┌──────────────────────────▼──────────────────────────────────┐     │
│  │  SENDER IDENTIFIER                                           │     │
│  │  Lookup sender phone:                                        │     │
│  │    → Customer? → proceed with their context                  │     │
│  │    → Sales rep? → identify which customer (from msg or ask)  │     │
│  │    → Unknown? → HIL(a): ask identity, notify ops, pause      │     │
│  └──────────────────────────┬──────────────────────────────────┘     │
│                              │                                        │
│  ┌──────────────────────────▼──────────────────────────────────┐     │
│  │  INPUT PREPROCESSOR                                          │     │
│  │  Text → pass through                                         │     │
│  │  Image → send to Sonnet vision (multimodal)                  │     │
│  │  PDF → extract text / send pages as images to Sonnet         │     │
│  │  Voice note → Whisper transcription (future) / ask to type   │     │
│  └──────────────────────────┬──────────────────────────────────┘     │
│                              │                                        │
│  ┌──────────────────────────▼──────────────────────────────────┐     │
│  │  MESSAGE BUFFER                                              │     │
│  │  Per-customer buffer with 1-2 min silence window             │     │
│  │  Customer sends multiple messages back-to-back → accumulate  │     │
│  │  Silence detected → trigger processing                       │     │
│  │  Sends immediate ack on first message of conversation        │     │
│  └──────────────────────────┬──────────────────────────────────┘     │
│                              │ (buffer complete)                      │
│  ┌──────────────────────────▼──────────────────────────────────┐     │
│  │  INTENT CLASSIFIER + PROCESSOR (Two-Model LLM Pipeline)       │     │
│  │                                                               │     │
│  │  STEP 1 — CLASSIFY (Haiku 4.5 — fast, cheap):                │     │
│  │     Input: message text + minimal context                     │     │
│  │     Output: intent label                                      │     │
│  │       ORDER, QUERY_PRICE, QUERY_CATALOG, QUERY_DELIVERY,     │     │
│  │       MODIFY_ORDER, CANCEL_ORDER, GREETING, COMPLEX/OTHER    │     │
│  │                                                               │     │
│  │  STEP 2 — ROUTE by intent:                                    │     │
│  │                                                               │     │
│  │    ORDER/MODIFY/CANCEL → STEP 3 (Sonnet — heavy)             │     │
│  │    IMAGE/PDF input     → STEP 3 (Sonnet — needs vision)      │     │
│  │    QUERY_PRICE         → Haiku answers from price list        │     │
│  │    QUERY_CATALOG       → Haiku answers from product catalog   │     │
│  │    QUERY_DELIVERY      → Haiku answers from delivery schedule │     │
│  │    GREETING            → Haiku responds politely              │     │
│  │    COMPLEX/OTHER       → HIL escalation                       │     │
│  │                                                               │     │
│  │  STEP 3 — EXTRACT ORDER (Sonnet 4.5 — accurate, multimodal): │     │
│  │     Build enriched prompt:                                    │     │
│  │     - Customer context (name, code, address)                  │     │
│  │     - Product catalog (with SalPackUn, BWeight1 for conv.)    │     │
│  │     - Price list (customer's pricing tier)                    │     │
│  │     - Delivery schedule + cutoff rules                        │     │
│  │     - Order history (typical products + quantities)           │     │
│  │     - Conversation history (full thread so far)               │     │
│  │     - Current message(s) / extracted image+PDF content        │     │
│  │     → Extract items, match SKUs, convert quantities           │     │
│  │                                                               │     │
│  │  STEP 4 — POST-PROCESSING (code, not LLM):                   │     │
│  │     - Verify conversions programmatically (SalPackUn/BWeight) │     │
│  │     - Check HIL(b): any product not in customer's history?    │     │
│  │     - Check HIL(c): any qty ±20% from customer's typical?     │     │
│  └──────────────────────────┬──────────────────────────────────┘     │
│                              │                                        │
│                 ┌────────────┴────────────┐                           │
│                 ▼                         ▼                           │
│  ┌──────────────────────┐  ┌──────────────────────────┐              │
│  │  LOW CONFIDENCE       │  │  HIGH CONFIDENCE          │              │
│  │  → Send clarification │  │  → Send confirmation      │              │
│  │    question to        │  │    summary to customer    │              │
│  │    customer           │  │    with HIL flags noted   │              │
│  │  → Wait for reply     │  │  → Wait for YES           │              │
│  │  → Re-process         │  │                           │              │
│  └──────────────────────┘  └────────────┬───────────────┘              │
│                                          │                             │
│                              ┌───────────▼──────────────┐              │
│                              │  CUSTOMER RESPONSE        │              │
│                              ├───────────────────────────┤              │
│                              │  "YES" → CONFIRMED        │              │
│                              │  Correction → Re-process  │              │
│                              │  Silence 4hr → TIMEOUT    │              │
│                              └───────────┬──────────────┘              │
│                                          │ (CONFIRMED)                 │
│  ┌───────────────────────────────────────▼─────────────────────┐      │
│  │  ORDER QUEUE (PostgreSQL)                                    │      │
│  │  Per-client isolation                                        │      │
│  │  Status: QUEUED → PICKED_UP → SYNCED / FAILED               │      │
│  └────────────┬──────────────────────────┬─────────────────────┘      │
│               │                          │                             │
│  ┌────────────▼────────────┐  ┌──────────▼──────────────────┐         │
│  │  GOOGLE SHEETS WRITER   │  │  AGENT API (REST endpoints)  │         │
│  │  - New row per order    │  │  GET  /orders/pending        │         │
│  │  - Status updates       │  │  POST /orders/{id}/status    │         │
│  │  - HIL flags visible    │  │  POST /agent/heartbeat       │         │
│  └─────────────────────────┘  └──────────┬──────────────────┘         │
│                                           │                            │
└───────────────────────────────────────────┼────────────────────────────┘
                                            │ HTTPS pull (outbound only)
                                            │
┌───────────────────────────────────────────▼────────────────────────────┐
│                  CLIENT SITE (TJUK / ACS machine)                       │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  LOCAL AGENT                                                       │  │
│  │  - Polls cloud every 30s for confirmed orders                      │  │
│  │  - Converts order JSON → SAP B1 API call / Tally XML              │  │
│  │  - Reports SYNCED or FAILED back to cloud                          │  │
│  │  - Heartbeat every 60s                                             │  │
│  │  - Auto-updates from cloud                                         │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │  ERP (SAP Business One / Tally Prime)                              │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Conversation State Machine

```
IDLE
  │ (customer sends a message)
  ▼
BUFFERING ←──────────────────────┐
  │ (1-2 min silence)            │ (customer sends another msg
  ▼                              │  while still buffering)
PROCESSING                       │
  │ (LLM extracts order)         │
  ├──── low confidence ──▶ CLARIFYING
  │                          │ (send question)
  │                          │ (customer replies)
  │                          └──▶ BUFFERING (re-accumulate)
  │
  ▼ (high confidence)
CONFIRMING
  │ (send order summary)
  ├──── "YES" ──────────▶ CONFIRMED → order queued
  ├──── correction ──────▶ BUFFERING (re-process with correction)
  └──── 4hr silence ─────▶ TIMED_OUT → notify ops
```

**One active conversation per customer.** New messages from same customer feed into the active conversation.

---

## Human-in-Loop (HIL) Triggers

| ID | Trigger | Detection Method | Action |
|----|---------|-----------------|--------|
| (a) | New customer | Phone not in `customers` or `sales_reps` table | BLOCKS order. Bot asks identity. Ops notified. |
| (b) | New product for customer | Product not in `order_history` for this customer | FLAGS line item. Order still processes. Shown in confirmation + Google Sheet. |
| (c) | Qty ±20% from typical | Compare against min/max/median in `order_history` | FLAGS line item. Bot mentions it: "You usually order X, this is Y — confirming." |
| (d) | Query bot can't answer | Price not in system, delivery date unknown, complex question | Bot says "Let me check with the team." Ops notified. |
| (e) | Modification past cutoff | Customer/rep wants to change order after cutoff window | Bot explains cutoff. Flags for ops to handle manually. |

- **(a)** is a blocker — cannot process without knowing who's ordering
- **(b)** and **(c)** are warnings — customer still confirms, ops reviews in Sheet
- **(d)** and **(e)** are escalations — bot hands off to human gracefully

---

## Sender Types

| Sender Type | Identification | Behavior |
|---|---|---|
| **Customer** | Phone found in `customers` table | Bot proceeds with that customer's context (catalog, prices, history) |
| **Sales rep** | Phone found in `sales_reps` table | Bot asks "Which customer?" or detects customer name from message. Rep's phone maps to N customers. |
| **Unknown** | Phone not in either table | HIL(a): Bot asks for identity, notifies ops. Order paused. |

### Sales Rep Flow
```
Sales rep sends: "Order for Kumar Stores — mixture 10, murukku 5"
  → Bot detects "Kumar Stores" → loads Kumar Stores context → processes order
  → Confirmation sent to rep (not to Kumar Stores directly)

Sales rep sends: "mixture 10, murukku 5" (no customer mentioned)
  → Bot asks: "Which customer is this order for?"
  → Rep replies: "Kumar Stores"
  → Bot loads context → processes
```

---

## Message Types the Bot Handles

### Input Formats
| Format | Processing |
|---|---|
| Text | Direct to LLM |
| Image (photo of handwritten list, product photo) | Sonnet 4.5 vision — multimodal, processes image directly |
| PDF (purchase orders, typed lists) | Extract text + send pages as images to Sonnet |
| Voice note | Phase 1: ask to type. Phase 2: Whisper transcription → text → LLM |

### Intent Types
| Intent | Bot Action |
|---|---|
| **ORDER** (text, image, or PDF) | Extract items → match SKU → convert qty → confirm |
| **QUERY_PRICE** ("how much is X?", "price for mixture?") | Answer from customer's price list. If no price data → HIL. |
| **QUERY_CATALOG** ("do you have X?", "what flavors?") | Answer from product catalog for this client. |
| **QUERY_DELIVERY** ("when will my order come?", "delivery date?") | Answer from delivery schedule rules. |
| **MODIFY_ORDER** ("add X", "change to Y", "make it 8 not 10") | If within cutoff window → update order → re-confirm. If past cutoff → "Sorry, cutoff was X hours ago." → HIL if they push. |
| **CANCEL_ORDER** ("cancel my order", "cancel the butter") | If within cutoff window → cancel → confirm cancellation. If past cutoff → HIL. |
| **CONFIRMATION** ("YES", "ok", "confirmed") | Push to queue |
| **GREETING** ("hi", "good morning") | Respond politely |
| **COMPLEX / OTHER** | HIL escalation — anything the bot can't handle confidently |

### Order Modification Cutoff
- Each client config has `modification_cutoff_hours` (e.g., 4 hours before delivery)
- If a customer/rep wants to modify/cancel after cutoff → bot says "The cutoff for changes was [time]. I've flagged this for the team."
- Ops team sees it in Google Sheet → handles manually

---

## Data Flow Summary

```
WhatsApp msg (text/image/PDF) → Webhook → Identify sender (customer or sales rep)
→ Preprocess input (OCR/PDF extraction if needed) → Buffer (1-2 min)
→ LLM: classify intent
  → ORDER: extract + match + convert → HIL check → Confirm → YES → Queue → ERP
  → QUERY: answer from data (prices/catalog/delivery) → respond
  → MODIFY/CANCEL: check cutoff → update or escalate → re-confirm
  → COMPLEX: HIL escalation → ops handles
```

---

## Data Requirements Per Client

The bot needs the following data loaded per client to answer questions and process orders:

| Data | Used For | Source |
|---|---|---|
| Product catalog (SKUs, names, aliases, pack sizes, weights) | SKU matching, catalog queries | ERP export |
| Price lists (per customer or per tier) | Price queries, order value validation | ERP export |
| Customer master (codes, names, phones, addresses) | Customer identification | ERP export |
| Sales rep master (phones, assigned customers) | Sales rep identification + customer mapping | Client provides |
| Order history (per customer) | HIL checks (b) and (c), LLM context | ERP export |
| Delivery schedule (days, cutoff times) | Delivery queries, modification cutoff | Client config |
| Ship-to addresses (for clients that need it) | Delivery location selection | ERP export |

---

## Key Design Principles

1. **Customer/rep always confirms** — no order goes to SAP without explicit "YES"
2. **LLM is grounded** — only matches against provided product catalog, never invents products
3. **Post-processing validates LLM** — programmatic check of unit conversions after LLM
4. **Bot answers what it can, escalates what it can't** — prices, catalog, delivery = bot handles. Complex queries = HIL.
5. **Agent is stateless** — pulls work, does it, reports. No local state to manage.
6. **Google Sheet is the ops dashboard** — no custom UI needed
7. **Per-client config, shared codebase** — new client = new YAML file + new container
8. **All input types supported** — text, images, PDFs. Sonnet's multimodal capability handles images natively.
9. **Sales reps are first-class users** — not an afterthought. Rep → customer mapping is core.

---

---

## Multi-Tenant Architecture (v0.3.0)

> Full decision record: [ADR-001: Multi-Tenant Architecture](adr-001-multi-tenant-architecture.md)

### Summary

| Aspect | Decision |
|--------|----------|
| Meta apps | One per client, all owned by IIC |
| Bot deployment | Single shared instance (Option A) |
| Routing | `phone_number_id` from webhook payload → client lookup |
| Client config | Centralized registry (DB table) with encrypted tokens |
| Webhook URL | Single URL, all clients point here |
| Verify token | Shared across all Meta apps |

### Routing Flow

```
Incoming webhook → extract phone_number_id
  → lookup client in registry
  → load client's catalog, prompt, token
  → process message with client context
  → reply using client's own WhatsApp token
```

### Client Registry Schema

```
clients:
  client_id          (string, unique)
  phone_number_id    (string, unique, indexed)
  access_token       (string, encrypted)
  business_name      (string)
  catalog_file       (string, path/reference)
  system_prompt      (text)
  welcome_message    (text)
  delivery_schedule  (JSON)
  google_sheet_id    (string)
  active             (boolean)
```

### MVP Preparation

Even in v0.1.0 (single client), the code follows patterns that ease multi-tenant migration:

1. Centralized config loading (no scattered `os.getenv()` calls)
2. Single reply function (one place to swap token)
3. Modular catalog loading (path-based, not hardcoded)
4. Client-tagged log lines

---

*Last updated: 01 March 2026*
*Change log:*
*- v1: Initial architecture (real-time bot, 1-to-1 chats, basic order flow)*
*- v2: Added sales rep support, query handling (prices/catalog/delivery), order modification with cutoff, image/PDF/OCR input support, HIL triggers (d) and (e)*
*- v3: Two-model LLM pipeline (Haiku for classification + simple tasks, Sonnet for order extraction + vision)*
*- v4: Multi-tenant architecture section added (ADR-001)*
