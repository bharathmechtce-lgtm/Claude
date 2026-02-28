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
**Sheet columns:** Timestamp, Order ID, Customer, Items, Delivery Date, Ship To, ERP Status (Queued/Synced/Failed), HIL Flag (NEEDS REVIEW / ACTION NEEDED), Notes
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
  "message_type": "order | clarification_response | confirmation | greeting | other",
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
**Current status:** GAP. Need to assess Brady and Akash's Python/FastAPI capability.
### 5.5 ERP Integration Engineer
**Role:** Builds the local agents that connect to Tally and SAP.
**Current status:** GAP. Tally XML integration and SAP B1 Service Layer are specialized.
### 5.6 DevOps / Infrastructure
**Role:** Server setup, Docker, deployment pipelines, monitoring, auto-update mechanism for agents.
**Current status:** Brady has existing Hetzner VPS experience.
### 5.7 QA / Tester
**Role:** Tests ordering scenarios end-to-end.
**Current status:** Brady and Akash can do this manually for two clients.
---
## 6. Data Model
### 6.1 Common Schema (Shared Across All Clients)
```sql
CREATE TABLE clients (
    client_id VARCHAR PRIMARY KEY,
    client_name VARCHAR NOT NULL,
    whatsapp_phone_id VARCHAR NOT NULL,
    whatsapp_token VARCHAR NOT NULL,
    erp_type VARCHAR NOT NULL,
    config JSONB NOT NULL,
    google_sheet_id VARCHAR,
    ops_whatsapp_number VARCHAR,
    active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_code VARCHAR NOT NULL,
    customer_name VARCHAR NOT NULL,
    phone_numbers VARCHAR[] NOT NULL,
    default_address TEXT,
    route_area VARCHAR,
    credit_terms VARCHAR,
    credit_limit DECIMAL,
    active BOOLEAN DEFAULT true,
    metadata JSONB,
    UNIQUE(client_id, customer_code)
);

CREATE TABLE ship_to_addresses (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_code VARCHAR NOT NULL,
    ship_to_code VARCHAR NOT NULL,
    address_name VARCHAR NOT NULL,
    full_address TEXT,
    is_default BOOLEAN DEFAULT false,
    UNIQUE(client_id, customer_code, ship_to_code)
);

CREATE TABLE products (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    sku_code VARCHAR NOT NULL,
    display_name VARCHAR NOT NULL,
    aliases VARCHAR[] NOT NULL,
    category VARCHAR,
    unit_of_measure VARCHAR NOT NULL,
    pack_sizes JSONB,
    active BOOLEAN DEFAULT true,
    metadata JSONB,
    UNIQUE(client_id, sku_code)
);

CREATE TABLE order_history (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_code VARCHAR NOT NULL,
    order_date DATE NOT NULL,
    line_items JSONB NOT NULL,
    source VARCHAR DEFAULT 'whatsapp',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE conversations (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_phone VARCHAR NOT NULL,
    customer_code VARCHAR,
    state VARCHAR NOT NULL DEFAULT 'IDLE',
    current_order JSONB,
    message_history JSONB NOT NULL DEFAULT '[]',
    started_at TIMESTAMP DEFAULT NOW(),
    last_activity_at TIMESTAMP DEFAULT NOW(),
    timeout_minutes INT DEFAULT 240,
    UNIQUE(client_id, customer_phone)
);

CREATE TABLE order_queue (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR UNIQUE NOT NULL,
    client_id VARCHAR REFERENCES clients(client_id),
    customer_code VARCHAR NOT NULL,
    customer_name VARCHAR NOT NULL,
    line_items JSONB NOT NULL,
    delivery_date DATE,
    ship_to_code VARCHAR,
    total_items INT,
    status VARCHAR NOT NULL DEFAULT 'QUEUED',
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

CREATE TABLE agent_heartbeats (
    client_id VARCHAR REFERENCES clients(client_id),
    last_heartbeat TIMESTAMP NOT NULL,
    agent_version VARCHAR,
    erp_status VARCHAR,
    PRIMARY KEY(client_id)
);

CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    client_id VARCHAR,
    event_type VARCHAR NOT NULL,
    customer_phone VARCHAR,
    order_id VARCHAR,
    details JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
```
---
## 7. Conversation Engine (State Machine)
### States
```
IDLE → EXTRACTING → AWAITING_CONFIRMATION → CONFIRMED → order pushed to queue
EXTRACTING → CLARIFYING → EXTRACTING (re-process with accumulated context)
AWAITING_CONFIRMATION → EXTRACTING (customer sends modifications)
ANY STATE → TIMED_OUT (no activity for N minutes)
```
---
## 8. Cloud-to-Agent API Contract
### Agent Polls for Orders
```
GET /api/v1/orders/pending
Headers: X-Agent-Key: {agent_api_key}
```
### Agent Reports Order Status
```
POST /api/v1/orders/{order_id}/status
Headers: X-Agent-Key: {agent_api_key}
```
### Agent Heartbeat
```
POST /api/v1/agent/heartbeat
Headers: X-Agent-Key: {agent_api_key}
```
### Agent Checks for Updates
```
GET /api/v1/agent/latest-version
Headers: X-Agent-Key: {agent_api_key}
```
### Agent Syncs Master Data
```
POST /api/v1/master-data/sync
Headers: X-Agent-Key: {agent_api_key}
```
---
## 9. Implementation Phases
### Phase 1: Foundation (Weeks 1-2)
- Set up FastAPI project structure with client config loading
- Meta WhatsApp Cloud API integration (webhook receive + send)
- Customer identification (phone → customer lookup)
- Basic LLM order extraction from text messages
- Google Sheets output (append row per order)
- Database schema setup (PostgreSQL)
- Basic conversation state machine
### Phase 2: Intelligence (Weeks 3-4)
- SKU matching with confidence scoring
- Clarification conversation flow
- Confirmation flow with YES/NO handling
- Multi-message order accumulation
- Ship-to selection flow (for TJUK)
- PO number collection (for TJUK)
- HIL flagging logic
### Phase 3: ERP Integration (Weeks 5-6)
- Tally agent (for ACS)
- SAP B1 agent (for TJUK)
- Agent polling, heartbeat, status reporting
### Phase 4: Polish & Go Live (Weeks 7-8)
- Error handling and edge cases
- Agent auto-update mechanism
- Centralized logging and monitoring
- Testing with real orders, soft launch, full rollout
### Phase 5: Enhancements (Post-Launch)
- Voice note transcription, Image OCR
- Order history analysis and repeat order suggestions
- Simple web dashboard
---
## 10. Success Metrics
### Technical
- Order extraction accuracy > 90%
- Average order processing time < 2 minutes
- System uptime > 99%
- ERP sync success rate > 95%
- HIL rate < 15%
### Business
- Reduction in manual order entry time
- Reduction in order entry errors
- Client willing to pay ongoing monthly fee
- Referenceable for new client acquisition
---
*Last updated: 28 February 2026*
*Authors: Brady (IIC) + Claude (Architecture & Documentation)*
