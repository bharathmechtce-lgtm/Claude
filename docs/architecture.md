# WhatsApp Order Bot — Architecture Document

## Architecture Decisions (Finalized 28 Feb 2026)

### Core Decisions

1. **Real-time conversational bot** — not batch. Order comes in → bot responds (confirm or question) → conversation continues until final confirmation.
2. **1-to-1 chats only for orders** — customers message the WhatsApp Business API number directly. Group chats are read-only for context, bot does NOT respond or accept orders from groups.
3. **Cloud Brain + Thin Local Agent** — 90% of logic on cloud (Brady controls), 10% on client machine (ERP connector).
4. **Pull-based agent** — local agent polls cloud for confirmed orders. No inbound ports needed at client site.
5. **Per-client deployment** — separate config, separate order queue, shared codebase.
6. **Google Sheets as visibility/HIL layer** — no custom UI to build.

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
│  │  CUSTOMER IDENTIFIER                                         │     │
│  │  Lookup sender phone → customers table                       │     │
│  │  Known? → proceed                                            │     │
│  │  Unknown? → HIL(a): ask identity, notify ops, pause          │     │
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
│  │  ORDER PROCESSOR (LLM Pipeline)                              │     │
│  │                                                               │     │
│  │  1. Build enriched prompt:                                    │     │
│  │     - Customer context (name, code, address)                  │     │
│  │     - Product catalog (with SalPackUn, BWeight1 for conv.)    │     │
│  │     - Order history (typical products + quantities)           │     │
│  │     - Conversation history (full thread so far)               │     │
│  │     - Current message(s)                                      │     │
│  │                                                               │     │
│  │  2. Sonnet 4.5: extract order → structured JSON               │     │
│  │     - Product matching against catalog                        │     │
│  │     - Quantity conversion (case→PCS, kg→PCS, direct)          │     │
│  │     - Confidence scoring per line item                        │     │
│  │                                                               │     │
│  │  3. Post-processing validation:                               │     │
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
| (a) | New customer | Phone not in `customers` table | BLOCKS order. Bot asks identity. Ops notified. |
| (b) | New product for customer | Product not in `order_history` for this customer | FLAGS line item. Order still processes. Shown in confirmation + Google Sheet. |
| (c) | Qty ±20% from typical | Compare against min/max/median in `order_history` | FLAGS line item. Bot mentions it: "You usually order X, this is Y — confirming." |

- (b) and (c) are warnings, not blockers — customer still confirms, ops reviews in Sheet
- (a) is a blocker — cannot process without knowing who's ordering

---

## Message Types the Bot Handles

| Message Type | Bot Action |
|---|---|
| Order (text) | Process → confirm |
| Order modification ("add X", "change to Y") | Update running order → re-confirm |
| Cancellation ("cancel the butter") | Remove from order → re-confirm |
| Confirmation ("YES", "ok", "confirmed") | Push to queue |
| Correction ("no, 8 not 10") | Update → re-confirm |
| Greeting ("hi", "good morning") | Respond politely, ask for order |
| Voice note | Phase 1: "Could you type your order? Voice support coming soon." |
| Image | Phase 1: "Could you type your order? Image support coming soon." |
| Unrelated/chatter | Politely redirect to ordering |

---

## Data Flow Summary

```
WhatsApp msg → Webhook → Identify customer → Buffer → LLM extract
→ HIL check → Clarify or Confirm → Customer says YES
→ Order Queue (PostgreSQL) → Google Sheet updated
→ Agent polls → Picks up order → Writes to SAP/Tally
→ Reports SYNCED → Google Sheet status updated → Done
```

---

## Key Design Principles

1. **Customer always confirms** — no order goes to SAP without explicit "YES"
2. **LLM is grounded** — only matches against provided product catalog, never invents products
3. **Post-processing validates LLM** — programmatic check of unit conversions after LLM
4. **Agent is stateless** — pulls work, does it, reports. No local state to manage.
5. **Google Sheet is the ops dashboard** — no custom UI needed
6. **Per-client config, shared codebase** — new client = new YAML file + new container

---

*Last updated: 28 February 2026*
