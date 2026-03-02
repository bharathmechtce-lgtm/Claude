#!/usr/bin/env python3
"""
TJUK WhatsApp Order Bot — Webhook Server (FastAPI)

Receives incoming WhatsApp messages via Meta Cloud API webhook,
processes them through the Claude LLM, and sends replies back.

Run locally:
  uvicorn webhook.app:app --host 0.0.0.0 --port 8000 --reload

Production (Docker):
  docker compose up -d
"""

import hashlib
import hmac
import json
import logging
import os
import time

import httpx
from fastapi import FastAPI, Request, Response, Query
from fastapi.responses import PlainTextResponse, JSONResponse

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)


def _load_dotenv(path):
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


_load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

# WhatsApp Cloud API credentials
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WEBHOOK_VERIFY_TOKEN = os.environ.get("WEBHOOK_VERIFY_TOKEN", "tjuk-bot-verify-2024")
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")

# LLM
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "claude-sonnet-4-5-20250929")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tjuk-bot")

# ---------------------------------------------------------------------------
# Product catalog (loaded at startup)
# ---------------------------------------------------------------------------
CATALOG: list = []
CATALOG_BY_CODE: dict = {}


def _load_catalog():
    """Load product catalog from JSON file."""
    global CATALOG, CATALOG_BY_CODE
    catalog_path = os.path.join(PROJECT_ROOT, "scenarios", "product_catalog.json")
    if os.path.exists(catalog_path):
        with open(catalog_path) as f:
            CATALOG = json.load(f)
        CATALOG_BY_CODE = {p["item_code"]: p for p in CATALOG}
        log.info(f"Loaded {len(CATALOG)} products from catalog")
    else:
        log.warning(f"Product catalog not found at {catalog_path}")


# ---------------------------------------------------------------------------
# In-memory conversation state (per phone number)
# ---------------------------------------------------------------------------
conversations: dict = {}

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a WhatsApp order assistant for TJUK, a food distribution company in Mumbai.
You are chatting 1-on-1 with a customer via WhatsApp. Be helpful, concise, and natural.

YOUR BEHAVIOR:
1. When the customer sends an order, acknowledge it naturally ("Got it!" / "Noted!" etc.)
2. Read the items and quantities they mention — confirm what you understood
3. If location/outlet is missing, ask for it
4. If a product name is ambiguous, ask for clarification
5. Handle "add" messages by merging into the current order
6. Handle "cancel" / "remove" messages by updating the order
7. Keep a RUNNING ORDER in your head — after each interaction, you know the full order state
8. Be conversational but efficient — these are busy restaurant/hotel managers

ANTI-HALLUCINATION RULES (CRITICAL — follow these strictly):
- ONLY include items the customer EXPLICITLY mentioned or asked for
- NEVER infer, suggest, or add items the customer did not ask for
- NEVER add items "they might also need" or "usually ordered together"
- If the customer says "5kg amul butter" — that is ONE item (amul butter). Do NOT add cheese, ghee, or anything else
- When extracting the order to JSON, list ONLY the items from the conversation. Zero extras
- If in doubt whether the customer asked for something, DO NOT include it — ask instead
- Count your output items against the customer's message. If you have MORE items than the customer mentioned, you are hallucinating — remove the extras

QUANTITY CONVERSION RULES (customers speak in cases/kg/box, SAP records in PCS):

  CASE/BOX: "X case" or "X box" → quantity = X × PackSize (from catalogue)
    Example: "3 box" of Kinley Soda (PackSize=24) → 3 × 24 = 72 PCS
    Example: "1 box" of Amul Butter 500GMS (PackSize=20) → 1 × 20 = 20 PCS
    Example: "1 box" of Dlecta Cream Cheese (PackSize=8) → 1 × 8 = 8 PCS

  KG: "X kg" → quantity = X ÷ UnitWeight (from catalogue)
    Example: "5 kg" of Amul Butter 500GMS (UnitWeight=0.5kg) → 5 ÷ 0.5 = 10 PCS
    Example: "3 kg" of Amul Cheese Block 1KG (UnitWeight=1.0kg) → 3 ÷ 1.0 = 3 PCS

  DIRECT (no conversion — just count as PCS):
    "X pcs/btl/pkt/nos/block/bulk/tin/bag" → quantity = X PCS
    Example: "24 block" = 24 PCS. Do NOT multiply blocks by pack_size or unit_weight.
    Example: "12 btl" = 12 PCS. Do NOT multiply bottles by anything.
    Example: "15 pkt" = 15 PCS.
  IMPORTANT: "block" means individual units (e.g. ice cream blocks). 1 block = 1 PCS always.

QUANTITY SANITY CHECK:
- After converting, compare the result against the customer's historical order patterns (if provided)
- If the converted quantity is more than 3× or less than 0.3× their typical order for that item, flag it
- Example: customer usually orders 10 PCS of butter, but this order converts to 120 PCS → ask "Just confirming — 120 PCS of butter? That's more than your usual order"
- For first-time items (no history), accept the quantity as-is but confirm during order summary

PRODUCT MATCHING RULES:
- Match customer text to the PRODUCT CATALOGUE provided in context
- Use the catalogue item_code and item_name — do NOT invent item codes
- If a customer's product text matches multiple catalogue items, pick the one with the closest name match
- If match confidence is low (customer said something vague), ASK for clarification rather than guessing
- If you genuinely cannot find a match, say so — do NOT fabricate a product or code

ORDER CONFIRMATION (before finalizing):
- When the customer seems done ordering (or says "that's it" / "done" / "confirm"), show a COMPLETE ORDER SUMMARY
- Format: numbered list with item name, quantity, and delivery location
- Ask: "Please confirm this order, or let me know if any changes are needed"
- Only after customer confirms should you consider the order final

CRITICAL: Keep track of the cumulative order. When asked to summarize or when you
sense the order is complete, list all items with quantities.

Respond naturally as a WhatsApp assistant. Keep responses SHORT (2-4 lines max).
Do NOT output JSON unless specifically asked. Just chat naturally.
"""

# ---------------------------------------------------------------------------
# Async HTTP client (shared across requests)
# ---------------------------------------------------------------------------
http_client: httpx.AsyncClient | None = None

# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="TJUK WhatsApp Order Bot", version="1.0.0")


@app.on_event("startup")
async def startup():
    global http_client
    http_client = httpx.AsyncClient(timeout=30)

    _load_catalog()

    missing = []
    if not WHATSAPP_TOKEN:
        missing.append("WHATSAPP_TOKEN")
    if not WHATSAPP_PHONE_NUMBER_ID:
        missing.append("WHATSAPP_PHONE_NUMBER_ID")
    if not ANTHROPIC_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if missing:
        log.warning(f"Missing env vars: {', '.join(missing)}")

    log.info(f"TJUK WhatsApp Bot started — model: {LLM_MODEL}")


@app.on_event("shutdown")
async def shutdown():
    if http_client:
        await http_client.aclose()


# ---------------------------------------------------------------------------
# GET /webhook — Meta verification
# ---------------------------------------------------------------------------
@app.get("/webhook")
async def verify_webhook(
    request: Request,
):
    """
    Meta sends a GET with:
      hub.mode=subscribe & hub.verify_token=<token> & hub.challenge=<challenge>
    Return the challenge if token matches.
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        log.info("Webhook verified successfully")
        return PlainTextResponse(challenge, status_code=200)

    log.warning("Webhook verification failed: token mismatch")
    return PlainTextResponse("Forbidden", status_code=403)


# ---------------------------------------------------------------------------
# POST /webhook — incoming messages
# ---------------------------------------------------------------------------
@app.post("/webhook")
async def handle_webhook(request: Request):
    """Receive incoming WhatsApp messages from Meta Cloud API."""
    raw_body = await request.body()
    body = await request.json()

    if not body:
        return PlainTextResponse("OK", status_code=200)

    # Validate signature if app secret is configured
    if WHATSAPP_APP_SECRET:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(raw_body, signature):
            log.warning("Invalid webhook signature — ignoring request")
            return PlainTextResponse("Forbidden", status_code=403)

    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])

                for msg in messages:
                    sender_phone = msg.get("from", "")
                    msg_type = msg.get("type", "")

                    if msg_type == "text":
                        text = msg.get("text", {}).get("body", "")
                        if text:
                            log.info(f"Message from {sender_phone}: {text[:80]}")
                            await _handle_incoming_message(sender_phone, text)
                    else:
                        log.info(
                            f"Ignoring non-text message type: {msg_type} "
                            f"from {sender_phone}"
                        )

    except Exception as e:
        log.error(f"Error processing webhook: {e}", exc_info=True)

    # Always return 200 quickly — Meta retries on non-200
    return PlainTextResponse("OK", status_code=200)


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------
def _verify_signature(payload: bytes, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 from Meta."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------
async def _handle_incoming_message(phone: str, text: str):
    """Process an incoming message: call Claude, send reply."""
    if phone not in conversations:
        conversations[phone] = {
            "history": [],
            "last_message_time": 0,
            "order_confirmed": False,
        }

    conv = conversations[phone]
    conv["last_message_time"] = time.time()

    # Reset order confirmation if new order comes in after a long gap (>30 min)
    if conv["order_confirmed"] and (time.time() - conv.get("confirmed_at", 0)) > 1800:
        conv["order_confirmed"] = False

    # Add user message
    conv["history"].append({"role": "user", "content": text})

    # Call LLM
    response_text = await _call_claude(conv["history"])

    # Track if the bot is presenting a confirmation summary
    confirm_keywords = ["confirm this order", "please confirm", "any changes"]
    if any(kw in response_text.lower() for kw in confirm_keywords):
        conv["awaiting_confirmation"] = True

    # Track if customer confirmed
    if conv.get("awaiting_confirmation"):
        confirm_words = ["yes", "ok", "confirm", "done", "all good", "correct",
                         "perfect", "looks good", "go ahead", "confirmed"]
        text_lower = text.lower().strip()
        if any(w in text_lower for w in confirm_words):
            conv["order_confirmed"] = True
            conv["confirmed_at"] = time.time()
            conv["awaiting_confirmation"] = False
            log.info(f"Order confirmed by {phone}")

    # Add assistant response
    conv["history"].append({"role": "assistant", "content": response_text})

    # Send reply via WhatsApp
    await _send_whatsapp_message(phone, response_text)

    # Keep history manageable (last 50 messages)
    if len(conv["history"]) > 50:
        conv["history"] = conv["history"][-50:]


# ---------------------------------------------------------------------------
# Claude API caller (async)
# ---------------------------------------------------------------------------
async def _call_claude(messages: list) -> str:
    """Call Claude API and return the response text."""
    try:
        resp = await http_client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "max_tokens": 1024,
                "system": SYSTEM_PROMPT,
                "messages": messages,
            },
        )
        data = resp.json()

        if resp.status_code != 200:
            err = data.get("error", {}).get("message", str(data))
            log.error(f"Claude API error: {err}")
            return "Sorry, I'm having trouble processing your message. Please try again."

        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block["text"]

        in_tok = data["usage"]["input_tokens"]
        out_tok = data["usage"]["output_tokens"]
        log.info(f"Claude: {in_tok} in / {out_tok} out tokens")
        return text

    except Exception as e:
        log.error(f"Claude API exception: {e}")
        return "Sorry, I'm having trouble right now. Please try again in a moment."


# ---------------------------------------------------------------------------
# WhatsApp Cloud API — send message (async)
# ---------------------------------------------------------------------------
async def _send_whatsapp_message(to_phone: str, text: str):
    """Send a text message via WhatsApp Cloud API."""
    url = (
        f"https://graph.facebook.com/v21.0/"
        f"{WHATSAPP_PHONE_NUMBER_ID}/messages"
    )
    try:
        resp = await http_client.post(
            url,
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "messaging_product": "whatsapp",
                "to": to_phone,
                "type": "text",
                "text": {"body": text},
            },
        )
        if resp.status_code == 200:
            log.info(f"Reply sent to {to_phone}")
        else:
            log.error(f"WhatsApp send failed ({resp.status_code}): {resp.text}")

    except Exception as e:
        log.error(f"WhatsApp send exception: {e}")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/")
async def health():
    return {
        "status": "ok",
        "service": "TJUK WhatsApp Order Bot",
        "active_conversations": len(conversations),
    }
