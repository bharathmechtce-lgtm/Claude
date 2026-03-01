# Process Flow — WhatsApp Ordering Bot

> **Last Updated:** 2026-03-01 (MVP)
> **Update this document** whenever the message processing pipeline changes.

---

## End-to-End Message Flow (MVP — v0.1.0)

```
Customer                   Meta Cloud API              VPS (Docker)
   │                            │                          │
   │  WhatsApp message          │                          │
   │ ─────────────────────────► │                          │
   │                            │  POST /webhook           │
   │                            │ ────────────────────────►│
   │                            │                          │
   │                            │         ┌────────────────┤
   │                            │         │ 1. Verify      │
   │                            │         │    signature   │
   │                            │         │    (HMAC-256)  │
   │                            │         │                │
   │                            │         │ 2. Extract     │
   │                            │         │    sender +    │
   │                            │         │    message     │
   │                            │         │    text        │
   │                            │         │                │
   │                            │         │ 3. Load/create │
   │                            │         │    conversation│
   │                            │         │    history     │
   │                            │         │                │
   │                            │         │ 4. Call Claude  │
   │                            │         │    API with    │
   │                            │         │    system      │
   │                            │         │    prompt +    │
   │                            │         │    history     │
   │                            │         │                │
   │                            │         │ 5. Receive     │
   │                            │         │    Claude      │
   │                            │         │    response    │
   │                            │         │                │
   │                            │         │ 6. Save to     │
   │                            │         │    conv history│
   │                            │         └───────────────►│
   │                            │                          │
   │                            │  POST /messages          │
   │                            │ ◄────────────────────────│
   │  WhatsApp reply            │                          │
   │ ◄───────────────────────── │                          │
```

---

## Step-by-Step Detail

### 1. Webhook Verification (One-time setup)
```
Meta ──GET /webhook──► Bot
       hub.mode=subscribe
       hub.verify_token=<token>
       hub.challenge=<challenge>

Bot returns challenge if token matches → Meta activates webhook
```

### 2. Incoming Message Processing

| Step | Component | Action |
|------|-----------|--------|
| 2.1 | Meta Cloud API | Receives customer WhatsApp message, sends POST to `/webhook` |
| 2.2 | FastAPI `/webhook` | Receives raw body + JSON payload |
| 2.3 | Signature check | Validates `X-Hub-Signature-256` using `WHATSAPP_APP_SECRET` |
| 2.4 | Message extraction | Loops `entry[] → changes[] → value.messages[]`, extracts `from` + `text.body` |
| 2.5 | Conversation lookup | Finds or creates in-memory conversation for sender phone number |
| 2.6 | History append | Adds `{role: "user", content: text}` to conversation history |
| 2.7 | Claude API call | Sends system prompt + full history to Claude API |
| 2.8 | Response capture | Extracts text from Claude response, logs token usage |
| 2.9 | History append | Adds `{role: "assistant", content: response}` to history |
| 2.10 | WhatsApp reply | Sends response to customer via `POST /v21.0/{phone_id}/messages` |
| 2.11 | History trim | Keeps last 50 messages to prevent unbounded growth |

### 3. Always Return 200
The webhook always returns HTTP 200 to Meta, even on errors. Meta retries on non-200, which would cause duplicate processing.

---

## Planned Flow Changes (Future Versions)

| Version | Change |
|---------|--------|
| v0.2.0 | Add media download step for PDF/image → pass to Claude vision or OCR |
| v0.3.0 | Route by `phone_number_id` to per-client config |
| v0.4.0 | Replace in-memory conversations with PostgreSQL |
| v0.5.0 | After order confirmation, generate Excel and send as WhatsApp document |
| v1.0.0 | Push confirmed orders to ERP via local agent |
