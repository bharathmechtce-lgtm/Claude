#!/usr/bin/env python3
"""
TJUK WhatsApp Order Bot — Webhook Server

Receives incoming WhatsApp messages via Meta Cloud API webhook,
processes them through the Claude LLM, and sends replies back.

Setup:
  1. Set environment variables (see .env.example)
  2. Run: python webhook/app.py
  3. Expose to internet (ngrok, cloudflare tunnel, etc.)
  4. Register the public URL as webhook in Meta Developer Dashboard
"""

import hashlib
import hmac
import json
import logging
import os
import sys
import time
import requests
from flask import Flask, request, jsonify

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

# Batch window: seconds of silence before processing queued messages
BATCH_WINDOW = int(os.environ.get("BATCH_WINDOW", "60"))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("tjuk-bot")

# ---------------------------------------------------------------------------
# In-memory conversation state (per phone number)
# ---------------------------------------------------------------------------
# conversations[phone] = {
#   "history": [...],          # Claude messages format
#   "pending_messages": [...], # messages waiting to be batched
#   "last_message_time": float,
#   "system_prompt": str,
# }
conversations = {}

# ---------------------------------------------------------------------------
# System prompt (simplified for live use — no scenario/SAP context)
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

QUANTITY CONVERSION RULES (customers speak in cases/kg, SAP records in PCS):
  CASE/BOX: "X case" → quantity = X × PackSize (from catalogue)
  KG: "X kg" → quantity = X ÷ UnitWeight (from catalogue)
  DIRECT: "X pcs/btl/pkt/nos" → quantity = X PCS

CRITICAL: Keep track of the cumulative order. When asked to summarize or when you
sense the order is complete, list all items with quantities.

Respond naturally as a WhatsApp assistant. Keep responses SHORT (2-4 lines max).
Do NOT output JSON unless specifically asked. Just chat naturally.
"""

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


# ---------------------------------------------------------------------------
# Webhook verification (GET) — Meta sends this to verify your endpoint
# ---------------------------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """
    Meta sends a GET request with these query params:
      hub.mode = subscribe
      hub.verify_token = <your verify token>
      hub.challenge = <random string>

    You must return the challenge value if the verify_token matches.
    """
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        log.info("Webhook verified successfully")
        return challenge, 200
    else:
        log.warning("Webhook verification failed: token mismatch")
        return "Forbidden", 403


# ---------------------------------------------------------------------------
# Webhook message handler (POST) — Meta sends incoming messages here
# ---------------------------------------------------------------------------
@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """
    Receive incoming WhatsApp messages from Meta Cloud API.
    Payload structure (simplified):
    {
      "entry": [{
        "changes": [{
          "value": {
            "messages": [{
              "from": "919876543210",
              "type": "text",
              "text": {"body": "I need 5 cases of butter"}
            }],
            "metadata": {"phone_number_id": "..."}
          }
        }]
      }]
    }
    """
    body = request.get_json()

    if not body:
        return "OK", 200

    # Validate signature if app secret is configured
    if WHATSAPP_APP_SECRET:
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(request.get_data(), signature):
            log.warning("Invalid webhook signature — ignoring request")
            return "Forbidden", 403

    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])

                for msg in messages:
                    sender_phone = msg.get("from", "")
                    msg_type = msg.get("type", "")

                    # Only handle text messages for now
                    if msg_type == "text":
                        text = msg.get("text", {}).get("body", "")
                        if text:
                            log.info(f"Message from {sender_phone}: {text[:80]}")
                            _handle_incoming_message(sender_phone, text)
                    else:
                        log.info(f"Ignoring non-text message type: {msg_type} from {sender_phone}")

    except Exception as e:
        log.error(f"Error processing webhook: {e}", exc_info=True)

    # Always return 200 quickly — Meta retries on non-200
    return "OK", 200


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------
def _verify_signature(payload, signature_header):
    """Verify the X-Hub-Signature-256 from Meta."""
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", signature_header)


# ---------------------------------------------------------------------------
# Message processing
# ---------------------------------------------------------------------------
def _handle_incoming_message(phone, text):
    """
    Process an incoming message:
    1. Add to conversation state
    2. For now, process immediately (batch window logic can be added later
       with a background scheduler like APScheduler or Celery)
    3. Call Claude and send reply
    """
    # Initialize conversation state if new
    if phone not in conversations:
        conversations[phone] = {
            "history": [],
            "pending_messages": [],
            "last_message_time": 0,
            "system_prompt": SYSTEM_PROMPT,
        }

    conv = conversations[phone]
    conv["last_message_time"] = time.time()

    # Add user message to history
    conv["history"].append({
        "role": "user",
        "content": text,
    })

    # Call LLM
    response_text = _call_claude(conv["history"], conv["system_prompt"])

    # Add assistant response to history
    conv["history"].append({
        "role": "assistant",
        "content": response_text,
    })

    # Send reply via WhatsApp
    _send_whatsapp_message(phone, response_text)

    # Keep history manageable (last 50 messages)
    if len(conv["history"]) > 50:
        conv["history"] = conv["history"][-50:]


# ---------------------------------------------------------------------------
# Claude API caller
# ---------------------------------------------------------------------------
def _call_claude(messages, system_prompt):
    """Call Claude API and return the response text."""
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": LLM_MODEL,
                "max_tokens": 1024,
                "system": system_prompt,
                "messages": messages,
            },
            timeout=30,
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
# WhatsApp Cloud API — send message
# ---------------------------------------------------------------------------
def _send_whatsapp_message(to_phone, text):
    """Send a text message via WhatsApp Cloud API."""
    url = f"https://graph.facebook.com/v21.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"

    payload = {
        "messaging_product": "whatsapp",
        "to": to_phone,
        "type": "text",
        "text": {"body": text},
    }

    try:
        resp = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
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
@app.route("/", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "TJUK WhatsApp Order Bot",
        "active_conversations": len(conversations),
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Validate required config
    missing = []
    if not WHATSAPP_TOKEN:
        missing.append("WHATSAPP_TOKEN")
    if not WHATSAPP_PHONE_NUMBER_ID:
        missing.append("WHATSAPP_PHONE_NUMBER_ID")
    if not ANTHROPIC_KEY:
        missing.append("ANTHROPIC_API_KEY")

    if missing:
        log.warning(f"Missing env vars: {', '.join(missing)}")
        log.warning("The server will start but some features won't work.")

    port = int(os.environ.get("PORT", 5000))
    log.info(f"Starting TJUK WhatsApp Bot on port {port}")
    log.info(f"Webhook URL: http://0.0.0.0:{port}/webhook")
    log.info(f"LLM Model: {LLM_MODEL}")
    log.info(f"Batch window: {BATCH_WINDOW}s")

    app.run(host="0.0.0.0", port=port, debug=True)
