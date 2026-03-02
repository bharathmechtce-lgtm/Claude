# WhatsApp Ordering Automation — Complete Project Brief

## Document Purpose

This document is the single source of truth for building a WhatsApp-based ordering automation tool for Indian Insights Company (IIC). It should be used as context when working with Claude Code or any development environment. It covers business context, customer details, architecture decisions, persona responsibilities, data models, LLM strategy, and implementation approach.

---

## 1. Company Context

### Indian Insights Company (IIC)

- **Founders:** Brady (Revenue Management Consultant) and Akash Karunakaran
- **Focus:** Analytics and automation consulting for mid-market F&B/FMCG companies in India
- **Positioning:** Revenue management consulting, not generic data analysis
- **Selling style:** Relationship-based, practical demonstrations, proven outcomes over technical pitches
- **Existing tech stack:** Hetzner VPS, Apollo (lead sourcing), Claude API, Instantly (email delivery), Tally data extraction, WhatsApp bot automation experience

### Why This Project

Multiple FMCG clients have independently asked for the same thing: automate the manual process of reading WhatsApp order messages and entering them into their ERP. This is a repeatable, productizable offering for IIC.

---

## 2. The Problem Being Solved

### Current Manual Workflow (What Happens Today)

1. A customer (retailer, distributor, hospital, corporate buyer) sends an order via WhatsApp to the FMCG company
2. Orders come in varied formats: Hindi text, Tamil text, Hinglish, voice notes, photos of handwritten lists, shorthand references
3. A human employee reads the WhatsApp message
4. The employee identifies who the customer is
5. The employee interprets what products and quantities are being ordered
6. If the order is unclear, the employee messages back asking for clarification
7. Once clear, the employee manually opens SAP or Tally
8. The employee creates a Sales Order in the ERP system, line by line
9. The employee confirms back to the customer on WhatsApp

### Pain Points

- Time-consuming: Each order takes 5-15 minutes of human effort
- Error-prone: Wrong SKUs, wrong quantities, missed items
- Delayed: Orders received at night or weekends sit until the employee is available
- Not scalable: As order volume grows, they need more people doing the same repetitive task
- Inconsistent: Different employees interpret orders differently

### What the Tool Should Do

Automate steps 2-9 completely. A customer sends a WhatsApp message → the system identifies them, extracts the order, clarifies if needed, confirms with the customer, and pushes the order into the ERP. A human only intervenes on edge cases flagged by the system.

---

## 3. Customers

### Customer 1: TJUK

- **ERP System:** SAP (likely SAP Business One)
- **Ship-to complexity:** Yes — one customer can have multiple delivery addresses (ship-to codes)
- **Ordering patterns:** TBD — need to map during onboarding
- **Languages:** English, Hindi likely
- **Access constraint:** Brady cannot easily remote into TJUK's systems for updates/maintenance. Remote access is unreliable and hard to schedule.

### Customer 2: Arvind Chettinadu Snacks (ACS)

- **Profile:** Tamil Nadu-based snack manufacturer, 100+ workers, 50+ product assortments
- **Sales channels:** 6 channels — retail, corporate, hospitals, export, and others
- **ERP System:** Tally (likely Tally Prime)
- **Ship-to complexity:** No — likely single delivery address per customer
- **Ordering patterns:** High variety of SKUs (50+ assortments), orders likely in Tamil and English
- **Access constraint:** Same as TJUK — hard to get reliable remote access for maintenance

### Key Constraint for Both

Brady and Akash cannot reliably remote into client systems on a regular basis. The architecture MUST minimize how often the client's local environment needs to be touched. Ideally, the local component is installed once and auto-updates from the cloud.

---

## 4. Architecture Decisions Made

### 4.1 NOT a SaaS — Per-Client Dedicated Deployment Model

**Decision:** Each client gets their own instance of the tool, not a shared multi-tenant SaaS platform.

**Reasons:**
- Indian mid-market business owners are more comfortable with "their own system"
- Each client will have customizations (different ERP, different business rules, different order workflows)
- Higher perceived value justifies higher monthly fee
- Simpler data isolation — no multi-tenancy risks
- IIC is a 2-person team; SaaS operations overhead is premature

**Implementation:** Single codebase in Git, with per-client configuration files. Docker containers per client. One deployment script that spins up a new client instance from config. Updates pushed to all containers via automation.

**Revenue model:** Setup fee (₹50K-1L) + monthly maintenance/operation fee (₹25-40K).

### 4.2 Cloud Brain + Thin Local Agent

**Decision:** Split the system into two parts:

**Cloud component (90% of logic — Brady controls 100%):**
- WhatsApp webhook receiver
- LLM-powered order extraction and conversation engine
- Customer identification
- SKU matching
- Conversation state management
- Order queue management
- Google Sheets output
- Monitoring and logging

**Local agent (10% of logic — installed once on client's machine):**
- Runs on the same machine as Tally/SAP
- Polls cloud API for confirmed orders (HTTPS pull — no inbound ports needed)
- Translates order JSON → ERP-specific format (Tally XML or SAP B1 API)
- Reports success/failure back to cloud
- Sends health heartbeat to cloud
- Auto-updates from cloud (downloads new versions and restarts)
- Optionally syncs master data (customer master, product master) from ERP to cloud

**Why pull-based (agent pulls from cloud) instead of push-based (cloud pushes to agent):**
- Works through any firewall — agent makes outbound HTTPS calls only
- No VPN, no port forwarding, no firewall configuration at client site
- If agent is offline, orders queue on cloud and sync when agent comes back
- Brady never needs to "get into" the client's network

### 4.3 Separate Order Queues Per Client

Each client has an independent order queue on the cloud. If TJUK's agent goes down, ACS orders keep flowing. Monitoring is per-client.

### 4.4 Google Sheets as Visibility Layer + Human-in-Loop (HIL)

Each client gets a Google Sheet that shows all orders with live status. This serves as:
- Visibility for the client's ops team
- A manual override interface for edge cases
- An audit trail

**Sheet columns:** Timestamp, Order ID, Customer, Items, Delivery Date, Ship To, ERP Status (Queued/Synced ✅/Failed ❌), HIL Flag (⚠️ NEEDS REVIEW / ⚠️ ACTION NEEDED), Notes

**HIL triggers:**
- LLM confidence below threshold on any line item
- Customer didn't confirm within timeout
- ERP sync failed after retries
- Order value unusually high/low vs history
- New/unrecognized customer (phone number not in master)

### 4.5 WhatsApp Integration via Meta Cloud API (Direct)

No BSP (Gupshup, AiSensy, etc.) middleman. Each client registers their own phone number on Meta's WhatsApp Cloud API. The cloud backend receives webhooks from all clients, routing by phone_number_id.

**Each client provides:** A dedicated phone number (not already on WhatsApp/WhatsApp Business app), registered on Meta Cloud API via their own Meta Business Manager account.

---

## 5. Personas Required to Build This

### 5.1 Solution Architect

**Role:** Designs overall system, decides components, data flows, integration patterns, technology choices. Makes "how does this all fit together" decisions.

**Key decisions to make:**
- API contract between cloud and local agent
- Database schema design
- Message processing pipeline design
- Error handling and retry strategies
- Deployment automation approach
- Monitoring and alerting strategy

**Current status:** Brady + Claude have been doing this (this document is the output).

### 5.2 Business Analyst

**Role:** Maps each client's actual ordering workflow end-to-end BEFORE any code is written. Understands the real-world process, edge cases, business rules, and what the ops team actually does today.

**Key tasks:**
- Interview TJUK and ACS to document their current ordering process
- Map every type of order message they receive (text, voice, image, forwarded messages)
- Document all product SKUs with every alias and local language name customers use
- Identify edge cases: partial orders, order modifications, cancellations, credit hold customers, out-of-stock scenarios
- Define business rules per client: minimum order quantities, order cutoff times, delivery schedules, pricing tiers
- Define what "success" looks like for each client
- Create the customer onboarding checklist

**Discovery questions to ask each client:**
1. Walk me through what happens from when a customer sends a WhatsApp message to when the order is in your ERP
2. How many orders do you receive per day via WhatsApp?
3. Show me 20 real WhatsApp order messages (varied formats)
4. How many unique customers order via WhatsApp? How often do new customers appear?
5. What languages do customers order in?
6. Do customers send voice notes? Images? Or only text?
7. What happens when an order is unclear — how does your team currently handle it?
8. Do customers ever modify or cancel orders after sending?
9. Do you have customer-specific pricing?
10. What's your delivery schedule? Any cutoff times?
11. For TJUK: How do ship-to codes work? Does the customer specify the delivery location?
12. What does the Sales Order look like in your ERP? What fields are mandatory?
13. Are there any credit limits or hold conditions that should block an order?
14. Who on your team currently handles WhatsApp orders? Can we shadow them for a day?

**Current status:** PARTIAL. Brady has relationships with both clients and some knowledge from prior conversations (especially ACS — has met them, knows about their scale and channels). Needs structured deep-dive sessions.

### 5.3 Prompt / AI Engineer

**Role:** Designs the LLM interaction strategy. How to structure prompts for reliable SKU matching, multilingual extraction, conversation management. Tunes the "AI brain."

**Key tasks:**
- Design system prompt template that dynamically loads per-client config, SKU catalog, and customer context
- Build the SKU matching strategy (fuzzy matching against catalog with confidence scoring)
- Design the clarification conversation flow (when to ask, how to ask, in what language)
- Design the confirmation message format
- Handle multilingual messages (Hindi, Tamil, Hinglish, English)
- Define confidence thresholds for auto-processing vs HIL flagging
- Optimize prompt structure to minimize token usage (cost control)
- Test with real order messages from both clients

**LLM Design Principles:**
- Pre-process before sending to LLM: transcribe voice notes (Whisper), OCR images, identify customer — then send structured prompt with context
- Use SKU catalog as grounding: pass filtered catalog in prompt, instruct LLM to only match against provided SKUs
- Structured output: LLM returns JSON with confidence scores per line item
- Conversation memory: stitch multi-message orders together, pass full thread to LLM
- Language handling: respond in the same language the customer used
- Cost optimization: use Haiku for simple routing (is this an order or just "thanks"?), Sonnet for actual extraction

**LLM Output Schema:**
```json
{
  "message_type": "order" | "clarification_response" | "confirmation" | "greeting" | "other",
  "line_items": [
    {
      "raw_text": "mixture 10 bag",
      "matched_sku": "CHT-MIX-HOT-500G",
      "matched_name": "Chettinadu Hot Mixture 500g",
      "quantity": 10,
      "unit": "bag",
      "confidence": 0.6,
      "clarification_needed": true,
      "suggested_question": "Which mixture - Hot Mixture or Mild Mixture? And is this 200g or 500g bags?"
    }
  ],
  "delivery_date": "2026-03-01",
  "delivery_date_confidence": 0.9,
  "ship_to": null,
  "ship_to_needed": false,
  "overall_confidence": 0.6,
  "language_detected": "tamil"
}
```

**Current status:** Can be done by Brady + Claude iteratively. Needs real order message samples from both clients to start.

### 5.4 Backend Software Engineer

**Role:** Builds the cloud backend — APIs, database, queue system, WhatsApp integration, Google Sheets integration, conversation state machine.

**Key tasks:**
- Set up FastAPI project structure
- Implement Meta WhatsApp Cloud API webhook receiver
- Implement message sending via WhatsApp API
- Build conversation state machine (IDLE → EXTRACTING → CLARIFYING → AWAITING_CONFIRMATION → CONFIRMED)
- Build order queue with per-client isolation
- Implement Google Sheets API integration (append rows, update status)
- Build the REST API that local agents poll for orders
- Build the REST API that local agents report status to
- Implement agent health monitoring (heartbeat tracking)
- Build client configuration loading from YAML/JSON files
- Implement conversation timeout handling
- Build logging and basic monitoring

**Tech stack:**
- Language: Python 3.11+
- Framework: FastAPI
- Database: PostgreSQL (SQLite acceptable for initial dev)
- Queue: Redis (optional — can start with DB-based queue for simplicity)
- WhatsApp: Meta Cloud API (direct REST calls, no SDK needed)
- Google Sheets: Google Sheets API v4 via google-api-python-client
- LLM: Anthropic Claude API (anthropic Python SDK)
- Deployment: Docker
- Hosting: AWS Mumbai or Hetzner (Singapore or Germany initially)

**Current status:** GAP. Need to assess Brady and Akash's Python/FastAPI capability. May need to contract out or have Claude Code generate significant portions.

### 5.5 ERP Integration Engineer

**Role:** Builds the local agents that connect to Tally and SAP. Specialist knowledge of ERP internals.

**Key tasks:**

**Tally Agent (for ACS):**
- Understand Tally Prime's data import mechanisms (XML, ODBC, API)
- Build a Python service that reads confirmed orders from cloud API and creates Sales Vouchers in Tally
- Map the common order schema to Tally's XML voucher format
- Handle Tally-specific requirements: ledger names, stock items, godowns, voucher types
- Test with ACS's actual Tally setup
- Package as Windows service or scheduled task

**SAP B1 Agent (for TJUK):**
- Understand SAP B1 Service Layer REST API
- Build a Python service that reads confirmed orders from cloud API and creates Sales Orders via Service Layer
- Map the common order schema to SAP B1's Sales Order structure (CardCode, ItemCode, Quantity, ShipToCode, etc.)
- Handle SAP-specific requirements: authentication, session management, mandatory fields
- Test with TJUK's actual SAP setup
- Package as Windows service

**Common agent features (both):**
- HTTPS polling mechanism with configurable interval
- Success/failure reporting back to cloud
- Health heartbeat (ping cloud every minute)
- Auto-update mechanism (check for new version, download, replace, restart)
- Local logging + log shipping to cloud
- Configuration file for ERP connection details
- Self-healing: auto-restart on crash, retry failed orders with exponential backoff

**Current status:** GAP. Tally XML integration and SAP B1 Service Layer are specialized. Need to research or contract.

### 5.6 DevOps / Infrastructure

**Role:** Server setup, Docker, deployment pipelines, monitoring, auto-update mechanism for agents.

**Key tasks:**
- Set up cloud server (AWS Mumbai or Hetzner)
- Dockerize the application
- Create per-client deployment scripts (new client = new config + docker-compose up)
- Set up centralized logging (Grafana + Loki or similar)
- Set up health monitoring and alerting
- Build the agent auto-update distribution mechanism
- SSL certificates, domain setup
- Backup strategy for PostgreSQL

**Current status:** Brady has existing Hetzner VPS experience. Docker and deployment automation can be learned/assisted by Claude Code.

### 5.7 QA / Tester

**Role:** Tests ordering scenarios end-to-end. Sends real WhatsApp messages, verifies extraction accuracy, checks ERP entries, validates edge cases.

**Key tasks:**
- Create test scenarios from real order messages
- Test each scenario end-to-end (WhatsApp → extraction → confirmation → ERP)
- Test edge cases: multi-message orders, order modifications, unclear messages, unsupported message types, new customers
- Test failure modes: agent offline, LLM API down, ERP down, timeout scenarios
- Test multilingual scenarios
- Validate ERP entries match expected output
- Load testing: what happens with 50 simultaneous orders

**Current status:** Brady and Akash can do this manually for two clients.

---

## 6. Data Model

### 6.1 Common Schema (Shared Across All Clients)

```sql
-- Client configuration
CREATE TABLE clients (
    client_id VARCHAR PRIMARY KEY,        -- 'tjuk', 'acs'
    client_name VARCHAR NOT NULL,
    whatsapp_phone_id VARCHAR NOT NULL,    -- Meta Cloud API phone_number_id
    whatsapp_token VARCHAR NOT NULL,       -- Meta API access token
    erp_type VARCHAR NOT NULL,             -- 'tally', 'sap_b1'
    config JSONB NOT NULL,                 -- Full client config (languages, business rules, templates)
    google_sheet_id VARCHAR,               -- Google Sheet ID for this client
    ops_whatsapp_number VARCHAR,           -- Internal team WhatsApp for notifications
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Customer master (per client)
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_code VARCHAR NOT NULL,        -- SAP CardCode or Tally ledger name or internal ID
    customer_name VARCHAR NOT NULL,
    phone_numbers VARCHAR[] NOT NULL,      -- Array: one customer may have multiple numbers
    default_address TEXT,
    route_area VARCHAR,                    -- Useful for ACS (delivery routes)
    credit_terms VARCHAR,
    credit_limit DECIMAL,
    active BOOLEAN DEFAULT true,
    metadata JSONB,                        -- Client-specific fields
    UNIQUE(client_id, customer_code)
);

-- Ship-to addresses (only populated for clients like TJUK that need it)
CREATE TABLE ship_to_addresses (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_code VARCHAR NOT NULL,
    ship_to_code VARCHAR NOT NULL,
    address_name VARCHAR NOT NULL,         -- "Warehouse A", "Main Office"
    full_address TEXT,
    is_default BOOLEAN DEFAULT false,
    UNIQUE(client_id, customer_code, ship_to_code)
);

-- Product master (per client)
CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    sku_code VARCHAR NOT NULL,             -- SAP ItemCode or Tally stock item name
    display_name VARCHAR NOT NULL,
    aliases VARCHAR[] NOT NULL,            -- All names customers use: ["maggi", "maggi masala", "2min noodle"]
    category VARCHAR,
    unit_of_measure VARCHAR NOT NULL,      -- 'case', 'kg', 'piece', 'bag', 'dozen'
    pack_sizes JSONB,                      -- {"small": "200g", "large": "500g"}
    active BOOLEAN DEFAULT true,
    metadata JSONB,                        -- Client-specific fields (price, tax code, etc.)
    UNIQUE(client_id, sku_code)
);

-- Order history (for LLM context — what does this customer usually order?)
CREATE TABLE order_history (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_code VARCHAR NOT NULL,
    order_date DATE NOT NULL,
    line_items JSONB NOT NULL,             -- [{"sku_code": "X", "qty": 50, "uom": "case"}]
    source VARCHAR DEFAULT 'whatsapp',     -- Could also import historical ERP orders
    created_at TIMESTAMP DEFAULT NOW()
);

-- Active conversations (state machine tracking)
CREATE TABLE conversations (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_phone VARCHAR NOT NULL,
    customer_code VARCHAR,                 -- NULL if unrecognized customer
    state VARCHAR NOT NULL DEFAULT 'IDLE', -- IDLE, EXTRACTING, CLARIFYING, AWAITING_CONFIRMATION, CONFIRMED, TIMED_OUT
    current_order JSONB,                   -- Accumulated order data for this conversation
    message_history JSONB NOT NULL DEFAULT '[]', -- Full thread for LLM context
    started_at TIMESTAMP DEFAULT NOW(),
    last_activity_at TIMESTAMP DEFAULT NOW(),
    timeout_minutes INT DEFAULT 240,       -- 4 hours default
    UNIQUE(client_id, customer_phone)      -- One active conversation per customer per client
);

-- Order queue (confirmed orders waiting for ERP sync)
CREATE TABLE order_queue (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR UNIQUE NOT NULL,      -- Client-prefixed: 'ACS-20260301-001'
    client_id VARCHAR REFERENCES clients(client_id),
    customer_code VARCHAR NOT NULL,
    customer_name VARCHAR NOT NULL,
    line_items JSONB NOT NULL,             -- [{"sku_code": "X", "display_name": "Y", "qty": 50, "uom": "case"}]
    delivery_date DATE,
    ship_to_code VARCHAR,                  -- NULL for clients without ship-to
    total_items INT,
    status VARCHAR NOT NULL DEFAULT 'QUEUED', -- QUEUED, PICKED_UP, SYNCED, FAILED
    hil_flag BOOLEAN DEFAULT false,
    hil_reason VARCHAR,
    created_at TIMESTAMP DEFAULT NOW(),
    picked_up_at TIMESTAMP,
    synced_at TIMESTAMP,
    failure_reason TEXT,
    retry_count INT DEFAULT 0,
    max_retries INT DEFAULT 3,
    conversation_id INT REFERENCES conversations(id)
);

-- Agent health tracking
CREATE TABLE agent_heartbeats (
    client_id VARCHAR REFERENCES clients(client_id),
    last_heartbeat TIMESTAMP NOT NULL,
    agent_version VARCHAR,
    erp_status VARCHAR,                    -- 'connected', 'disconnected', 'error'
    PRIMARY KEY(client_id)
);

-- Audit log (every message and action)
CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR,
    event_type VARCHAR NOT NULL,           -- 'message_received', 'message_sent', 'order_extracted', 'order_confirmed', 'erp_synced', 'erp_failed', 'hil_flagged'
    customer_phone VARCHAR,
    order_id VARCHAR,
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### 6.2 Client-Specific Data Differences

| Data Entity | TJUK (SAP B1) | ACS (Tally) |
|---|---|---|
| customer_code | SAP CardCode (e.g., "C00042") | Tally Ledger Name or internal ID |
| ship_to_addresses | Populated — customers have multiple delivery points | Empty — single address per customer |
| products.sku_code | SAP ItemCode | Tally Stock Item Name |
| products.metadata | May include SAP price lists, tax groups | May include Tally godown, category |
| Business rules | May require PO number, customer-specific pricing | Delivery route-based, category-level pricing |

### 6.3 Client Configuration File Format

```yaml
# config/acs.yaml
client_id: "acs"
client_name: "Arvind Chettinadu Snacks"

# WhatsApp
whatsapp_phone_id: "XXXXXXXXXX"           # From Meta Cloud API
whatsapp_api_token: "${ACS_WA_TOKEN}"      # Environment variable reference
whatsapp_verify_token: "${ACS_WA_VERIFY}"

# Languages
supported_languages: ["tamil", "english", "hinglish"]
default_response_language: "english"

# ERP
erp_type: "tally"
erp_agent_api_key: "${ACS_AGENT_KEY}"      # Agent authenticates with this

# Business Rules
ship_to_required: false
requires_po_number: false
order_cutoff_time: "18:00"                 # IST
delivery_days: ["mon", "tue", "wed", "thu", "fri", "sat"]
min_order_value: 500                       # INR
max_order_value: 500000                    # INR — flag as HIL above this
confidence_threshold: 0.75                 # Below this → clarification
hil_confidence_threshold: 0.5             # Below this → flag for human

# Conversation
conversation_timeout_minutes: 240          # 4 hours
max_clarification_rounds: 3               # After 3 failed clarifications → flag HIL

# Confirmation Template
confirmation_template: |
  Order #{order_id}:
  {line_items_formatted}
  Delivery: {delivery_date}
  
  Reply YES to confirm or send corrections.

# Google Sheet
google_sheet_id: "SHEET_ID_HERE"
ops_notification_number: "+91XXXXXXXXXX"   # Ops team WhatsApp

# SKU Matching
fuzzy_match_threshold: 0.7                 # Minimum similarity for alias matching
```

```yaml
# config/tjuk.yaml
client_id: "tjuk"
client_name: "TJUK"

whatsapp_phone_id: "YYYYYYYYYY"
whatsapp_api_token: "${TJUK_WA_TOKEN}"
whatsapp_verify_token: "${TJUK_WA_VERIFY}"

supported_languages: ["english", "hindi", "hinglish"]
default_response_language: "english"

erp_type: "sap_b1"
erp_agent_api_key: "${TJUK_AGENT_KEY}"

ship_to_required: true
ship_to_prompt: "Which location should we deliver to?\n{address_list}"
requires_po_number: true
po_number_prompt: "Please provide your PO number for this order."
order_cutoff_time: "17:00"
delivery_days: ["mon", "wed", "fri"]
min_order_value: 1000
max_order_value: 1000000
confidence_threshold: 0.75
hil_confidence_threshold: 0.5

conversation_timeout_minutes: 240
max_clarification_rounds: 3

confirmation_template: |
  Order #{order_id}:
  {line_items_formatted}
  Delivery to: {ship_to_name}
  PO#: {po_number}
  Delivery: {delivery_date}
  
  Reply YES to confirm or send corrections.

google_sheet_id: "SHEET_ID_HERE"
ops_notification_number: "+91XXXXXXXXXX"

fuzzy_match_threshold: 0.7
```

---

## 7. Conversation Engine (State Machine)

### States

```
IDLE
  ↓ (customer sends a message)
EXTRACTING
  ↓ (order clear, confidence above threshold)
AWAITING_CONFIRMATION
  ↓ (customer replies YES)
CONFIRMED → order pushed to queue → Google Sheet updated
  
EXTRACTING
  ↓ (order unclear, confidence below threshold)
CLARIFYING
  ↓ (customer responds with clarification)
EXTRACTING (re-process with accumulated context)

AWAITING_CONFIRMATION
  ↓ (customer sends modifications)
EXTRACTING (re-process modified order)

ANY STATE
  ↓ (timeout: no activity for N minutes)
TIMED_OUT → notify ops if partial order existed
```

### Conversation Flow Example (ACS)

```
Customer (phone: +91-98765-43210): "mixture 10 bag murukku 5 packet"

System:
1. Webhook received → route to ACS (by whatsapp_phone_id)
2. Lookup +91-98765-43210 → Customer: "Kumar Stores" (customer_code: KS001)
3. State: IDLE → EXTRACTING
4. LLM prompt built with:
   - ACS product catalog (filtered to snacks category based on customer history)
   - Customer context: "Kumar Stores, retail channel, typically orders weekly"
   - Message: "mixture 10 bag murukku 5 packet"
5. LLM returns:
   - "mixture" → matched to CHT-MIX-HOT-500G (confidence: 0.55) — BELOW threshold
     - Multiple mixture variants exist, bag size unclear
   - "murukku" → matched to CHT-MRK-REG-200G (confidence: 0.85) — ABOVE threshold
6. State: EXTRACTING → CLARIFYING
7. Bot sends WhatsApp message (in detected language):
   "Hi Kumar Stores! Got your order for Murukku 5 packets (200g).
    For Mixture — which type do you need?
    1. Hot Mixture
    2. Mild Mixture
    3. Special Mixture
    And what bag size — 200g or 500g?"
8. Customer replies: "hot mixture 500g"
9. State: CLARIFYING → EXTRACTING (with accumulated context)
10. LLM re-processes with full thread → both items now high confidence
11. State: EXTRACTING → AWAITING_CONFIRMATION
12. Bot sends:
    "Order #ACS-20260301-005:
     1. Hot Mixture 500g × 10 bags
     2. Regular Murukku 200g × 5 packets
     Delivery: 2 March
     
     Reply YES to confirm or send corrections."
13. Customer: "YES"
14. State: AWAITING_CONFIRMATION → CONFIRMED
15. Order written to queue (status: QUEUED)
16. Google Sheet updated (new row)
17. WhatsApp notification sent to ACS ops team
18. Bot sends customer: "Order confirmed! Order #ACS-20260301-005. Thank you!"
19. ACS local agent picks up order → writes to Tally → status → SYNCED
20. Google Sheet status updated to ✅
```

### Conversation Flow Example (TJUK — with Ship-To)

```
Customer (phone: +91-87654-32109): "need 100 cases maggi masala and 50 cases atta noodles by friday"

System:
1. Route to TJUK
2. Lookup → Customer: "Metro Foods" (CardCode: C00042)
3. Has 3 ship-to addresses: Warehouse A, Warehouse B, Head Office
4. LLM extracts order: 100 cases Maggi Masala (confidence: 0.95), 50 cases Atta Noodles (confidence: 0.92)
5. Delivery date "by friday" → 2026-03-06 (confidence: 0.9)
6. ship_to_required = true but not specified → need to ask
7. requires_po_number = true but not provided → need to ask
8. Bot sends:
   "Hi Metro Foods! Order received:
    1. Maggi Masala 2-Min × 100 cases
    2. Maggi Atta Noodles × 50 cases
    Delivery by: Friday 6 March
    
    Which delivery location?
    1. Warehouse A — [address]
    2. Warehouse B — [address]
    3. Head Office — [address]
    
    Also, please share your PO number."
9. Customer: "warehouse B, PO 4521"
10. Bot sends confirmation with all details → customer confirms → order queued → synced to SAP
```

---

## 8. Cloud-to-Agent API Contract

### Agent Polls for Orders

```
GET /api/v1/orders/pending
Headers: X-Agent-Key: {agent_api_key}
Query: client_id={client_id}

Response:
{
  "orders": [
    {
      "order_id": "ACS-20260301-005",
      "customer_code": "KS001",
      "customer_name": "Kumar Stores",
      "line_items": [
        {"sku_code": "CHT-MIX-HOT-500G", "display_name": "Hot Mixture 500g", "quantity": 10, "uom": "bag"},
        {"sku_code": "CHT-MRK-REG-200G", "display_name": "Regular Murukku 200g", "quantity": 5, "uom": "packet"}
      ],
      "delivery_date": "2026-03-02",
      "ship_to_code": null,
      "po_number": null,
      "notes": ""
    }
  ]
}
```

### Agent Reports Order Status

```
POST /api/v1/orders/{order_id}/status
Headers: X-Agent-Key: {agent_api_key}
Body:
{
  "status": "SYNCED",                      // or "FAILED"
  "erp_reference": "TV-20260301-042",      // Tally voucher number or SAP DocEntry
  "failure_reason": null,                  // Populated on failure
  "timestamp": "2026-03-01T09:15:00+05:30"
}
```

### Agent Heartbeat

```
POST /api/v1/agent/heartbeat
Headers: X-Agent-Key: {agent_api_key}
Body:
{
  "client_id": "acs",
  "agent_version": "1.2.0",
  "erp_status": "connected",              // or "disconnected", "error"
  "pending_count": 0,                     // Orders agent has picked up but not yet synced
  "timestamp": "2026-03-01T09:15:00+05:30"
}
```

### Agent Checks for Updates

```
GET /api/v1/agent/latest-version
Headers: X-Agent-Key: {agent_api_key}

Response:
{
  "version": "1.3.0",
  "download_url": "https://your-cloud/agent/releases/agent-1.3.0.zip",
  "changelog": "Improved Tally XML handling for GST vouchers",
  "mandatory": false
}
```

### Agent Syncs Master Data (Optional — Inbound from ERP)

```
POST /api/v1/master-data/sync
Headers: X-Agent-Key: {agent_api_key}
Body:
{
  "client_id": "acs",
  "data_type": "customers",               // or "products"
  "records": [
    {"customer_code": "KS001", "customer_name": "Kumar Stores", "phone": "+919876543210", ...}
  ],
  "sync_timestamp": "2026-03-01T00:00:00+05:30"
}
```

---

## 9. LLM Prompt Strategy

### System Prompt Template (Loaded Per Client)

```
You are an order processing assistant for {client_name}. Your job is to extract structured order information from WhatsApp messages sent by customers.

## Customer Context
- Customer: {customer_name} (Code: {customer_code})
- Location: {customer_address}
- Usual order pattern: {typical_items_summary}
- Last 3 orders: {recent_orders_summary}

## Product Catalog
Only match against these products. If you cannot confidently match a product, set clarification_needed to true.

{filtered_product_catalog_as_structured_list}

## Rules
1. Extract each line item with: matched SKU code, quantity, unit of measure
2. Assign a confidence score (0.0-1.0) to each match
3. If confidence is below {confidence_threshold}, generate a clarification question in {detected_language}
4. Detect and respect the customer's language — respond in the same language
5. If the customer mentions a delivery date, parse it relative to today ({today_date})
6. If the customer mentions a location/address and ship_to is required, match against known ship-to addresses
7. Never hallucinate products not in the catalog — if unsure, ask
8. Common abbreviations: "cs" = case, "pkt" = packet, "dz" = dozen, "bg" = bag

## Conversation History
{full_message_thread}

## New Message
{current_message}

## Output Format
Respond with ONLY valid JSON matching this schema:
{json_schema}
```

### Cost Optimization

- **Router (Haiku):** First, send message through Haiku to classify: is this an order, a confirmation ("YES"), a greeting ("thanks bhai"), or irrelevant? Only route actual orders and substantive messages to Sonnet.
- **Catalog filtering:** Don't send the entire 50+ SKU catalog every time. Filter by customer's typical categories or use embedding-based pre-filtering.
- **Token management:** Keep conversation history trimmed to last 10 messages. Summarize older context if needed.

---

## 10. Implementation Phases

### Phase 1: Foundation (Weeks 1-2)
- [ ] Set up FastAPI project structure with client config loading
- [ ] Meta WhatsApp Cloud API integration (webhook receive + send)
- [ ] Customer identification (phone → customer lookup)
- [ ] Basic LLM order extraction from text messages
- [ ] Google Sheets output (append row per order)
- [ ] Database schema setup (PostgreSQL)
- [ ] Basic conversation state machine

### Phase 2: Intelligence (Weeks 3-4)
- [ ] SKU matching with confidence scoring
- [ ] Clarification conversation flow
- [ ] Confirmation flow with YES/NO handling
- [ ] Multi-message order accumulation
- [ ] Ship-to selection flow (for TJUK)
- [ ] PO number collection (for TJUK)
- [ ] HIL flagging logic
- [ ] WhatsApp notification to ops team

### Phase 3: ERP Integration (Weeks 5-6)
- [ ] Tally agent: order JSON → Tally XML voucher (for ACS)
- [ ] SAP B1 agent: order JSON → SAP Sales Order (for TJUK)
- [ ] Agent polling mechanism
- [ ] Agent heartbeat and health monitoring
- [ ] Status reporting (SYNCED/FAILED) back to cloud
- [ ] Google Sheet status update from agent reports

### Phase 4: Polish & Go Live (Weeks 7-8)
- [ ] Error handling and edge cases
- [ ] Conversation timeout handling
- [ ] Agent auto-update mechanism
- [ ] Centralized logging and monitoring
- [ ] Testing with real orders from both clients
- [ ] Client onboarding: load master data, configure WhatsApp numbers
- [ ] Soft launch with limited customers
- [ ] Full rollout

### Phase 5: Enhancements (Post-Launch)
- [ ] Voice note transcription (Whisper API)
- [ ] Image OCR for handwritten order lists
- [ ] Order history analysis and repeat order suggestions ("Order same as last week?")
- [ ] Master data sync from ERP (agent pulls customer/product updates)
- [ ] Simple web dashboard (alternative to Google Sheets)
- [ ] Order modification and cancellation handling
- [ ] Analytics: orders per day, automation rate, HIL rate, error rate

---

## 11. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| LLM mismatches SKU | Wrong product delivered | Confidence scoring + mandatory customer confirmation before processing |
| Customer sends voice note (Phase 1 doesn't support) | Order not processed | Bot replies: "I received a voice note. Could you type your order instead?" Flag for ops team |
| Customer sends image (Phase 1 doesn't support) | Order not processed | Same as above — polite redirect + ops flag |
| Agent goes offline | Orders not synced to ERP | Orders queue on cloud. Ops team sees them in Google Sheet. Manual entry as fallback |
| Tally/SAP is down | Agent can't write orders | Agent retries with backoff. Reports failure. Cloud alerts ops team |
| New customer (unknown phone) | Can't identify who's ordering | Bot asks "Hi! I don't have your number on file. Could you share your company/shop name and location?" Flag as HIL |
| Customer orders product not in catalog | LLM can't match | Low confidence → clarification. If still unresolved → HIL flag |
| Meta WhatsApp API goes down | No messages received/sent | Meta has SLA. Messages are retried by Meta for up to 7 days. No data loss |
| High message volume spike | System overload | Redis/queue-based processing. Messages queued, processed in order |
| Client's internet goes down | Agent can't reach cloud | Orders queue on cloud. Agent catches up when back online |

---

## 12. Project File Structure (Proposed)

```
whatsapp-order-bot/
├── README.md
├── docker-compose.yml
├── .env.example
│
├── config/                              # Per-client configuration
│   ├── acs.yaml
│   └── tjuk.yaml
│
├── app/                                 # Cloud backend (FastAPI)
│   ├── main.py                          # FastAPI app entry point
│   ├── config.py                        # Config loader
│   ├── models.py                        # SQLAlchemy/Pydantic models
│   ├── database.py                      # DB connection
│   │
│   ├── whatsapp/                        # WhatsApp integration
│   │   ├── webhook.py                   # Receive messages
│   │   └── sender.py                    # Send messages
│   │
│   ├── conversation/                    # Conversation engine
│   │   ├── state_machine.py             # State transitions
│   │   ├── router.py                    # Message type classification (Haiku)
│   │   └── processor.py                 # Order extraction orchestration
│   │
│   ├── llm/                             # LLM integration
│   │   ├── prompts.py                   # Prompt templates and builders
│   │   ├── extractor.py                 # Order extraction (Sonnet)
│   │   └── schemas.py                   # Output JSON schemas
│   │
│   ├── matching/                        # SKU matching
│   │   ├── fuzzy.py                     # Fuzzy text matching
│   │   └── catalog.py                   # Catalog loading and filtering
│   │
│   ├── output/                          # Output integrations
│   │   ├── sheets.py                    # Google Sheets API
│   │   └── notifications.py             # WhatsApp notifications to ops
│   │
│   ├── queue/                           # Order queue
│   │   ├── manager.py                   # Queue operations
│   │   └── hil.py                       # Human-in-loop flagging logic
│   │
│   └── agent_api/                       # API for local agents
│       ├── orders.py                    # Pending orders endpoint
│       ├── status.py                    # Status reporting endpoint
│       ├── heartbeat.py                 # Health monitoring
│       └── updates.py                   # Agent version management
│
├── agent/                               # Local agent (deployed to client machines)
│   ├── agent.py                         # Main agent loop
│   ├── config.py                        # Local config loader
│   ├── updater.py                       # Self-update mechanism
│   ├── heartbeat.py                     # Health reporting
│   │
│   ├── connectors/                      # ERP-specific connectors
│   │   ├── base.py                      # Abstract connector interface
│   │   ├── tally.py                     # Tally Prime connector
│   │   └── sap_b1.py                    # SAP Business One connector
│   │
│   └── build/                           # Packaging scripts
│       └── build_exe.py                 # PyInstaller config for Windows exe
│
├── scripts/                             # Utility scripts
│   ├── onboard_client.py                # New client setup script
│   ├── import_customers.py              # Import customer master from CSV
│   ├── import_products.py               # Import product master from CSV
│   ├── import_order_history.py          # Import historical orders
│   └── deploy.py                        # Deployment automation
│
├── tests/                               # Test suite
│   ├── test_extraction.py               # LLM extraction tests with real messages
│   ├── test_state_machine.py            # Conversation flow tests
│   ├── test_sku_matching.py             # SKU matching accuracy tests
│   └── test_agent_api.py                # Agent API contract tests
│
└── data/                                # Sample data for testing
    ├── sample_orders_acs.json           # Real order messages from ACS
    └── sample_orders_tjuk.json          # Real order messages from TJUK
```

---

## 13. Open Questions (To Resolve During Onboarding)

### For Both Clients
1. Can we get 20+ real WhatsApp order message screenshots to train and test the LLM?
2. What format will customer master and product master be provided in? (Excel, CSV, ERP export?)
3. Who is the main point of contact for technical questions during setup?
4. What are peak ordering hours? Any seasonal patterns?
5. How should the bot handle messages outside business hours?

### For TJUK Specifically
6. SAP B1 version? Is the Service Layer API accessible?
7. How many ship-to addresses does a typical customer have?
8. Is PO number always required or only for some customers?
9. Is there customer-specific pricing in SAP? How does it work?
10. What machine will the agent run on? Is it always on?

### For ACS Specifically
11. Tally Prime version? Is the XML import mechanism enabled?
12. Are there multiple godowns (warehouses)? Does the order need to specify which one?
13. Do hospital/corporate channel orders have different formats than retail?
14. Are prices the same across channels or channel-specific?
15. What machine does Tally run on? Is it always on during business hours?

### For IIC (Internal)
16. What is Brady and Akash's Python coding capability? (Determines how much code Claude Code needs to generate vs them writing)
17. Budget for infrastructure? (AWS Mumbai vs Hetzner)
18. Budget for contracting ERP integration if needed?
19. Timeline expectation from clients?

---

## 14. Success Metrics

### Technical
- Order extraction accuracy > 90% (correct SKU + quantity)
- Average order processing time < 2 minutes (message to ERP entry)
- System uptime > 99% (cloud component)
- ERP sync success rate > 95%
- HIL rate < 15% (85%+ orders fully automated)

### Business
- Reduction in manual order entry time for client's ops team
- Reduction in order entry errors vs manual process
- Client willing to pay ongoing monthly fee after pilot
- Referenceable for new client acquisition

---

*Last updated: 28 February 2026*
*Authors: Brady (IIC) + Claude (Architecture & Documentation)*
