#!/usr/bin/env python3
"""
Deterministic Evaluation Harness — WhatsApp Order Bot

Flow:
  Step 1-3: Send raw customer messages as-is from test scenarios
  Step 4:   Bot returns summary → Python compares against SAP ground truth
  Step 5:   If wrong → deterministic Python corrections (no LLM phrasing)
  Step 6:   Bot updates → Python compares again
  Step 7:   Cap at 3 correction rounds → pass or fail

KPIs tracked:
  Tier 1 (First Pass) — did the bot get it right from raw human messages alone?
  Tier 2 (Correctability) — when told exactly what's wrong, can the bot fix it?

  Per-order metrics:
    - customer_match: did the bot identify the right customer?
    - ship_to_match: correct delivery address?
    - products_identified: how many products correctly matched?
    - qty_correct: how many quantities exactly right?
    - no_phantom_items: no items the customer didn't order?
    - no_missing_items: all customer items captured?
    - total_line_items: expected vs actual count
    - turns_to_complete: conversation turns needed
    - correction_rounds: how many deterministic correction rounds needed (0 = first pass)
    - time_taken_s: wall-clock seconds
    - order_score: matched_fields / total_fields × 100

Usage:
  # Single model:
  export ANTHROPIC_API_KEY=sk-...
  python3 deterministic_eval.py --model claude-haiku-4-5-20251001

  # Gemini:
  export GEMINI_API_KEY=AIza...
  python3 deterministic_eval.py --model gemini-2.5-flash

  # Both (comparison):
  python3 deterministic_eval.py --model haiku,gemini

  # Subset of scenarios:
  python3 deterministic_eval.py --model haiku --scenarios 1,5,10
"""

import json
import os
import re
import sys
import time
import argparse
import requests
from datetime import datetime
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
REPO_ROOT = os.path.dirname(PROJECT_ROOT)

sys.path.insert(0, REPO_ROOT)
from src.core.prompts import build_system_prompt as _build_shared_prompt, EXTRACTION_PROMPT
from testing.eval.scorer_utils import (
    extract_json, score_order, filter_testable_items,
    auto_respond, classify_bot_response,
)

# ── Load .env if present ──
for env_candidate in [
    os.path.join(REPO_ROOT, ".env"),
    os.path.join(PROJECT_ROOT, ".env"),
]:
    if os.path.exists(env_candidate):
        with open(env_candidate) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# ── Model configs ──
MODEL_REGISTRY = {
    # Anthropic
    "claude-haiku-4-5-20251001": {
        "provider": "anthropic", "input_cost": 1.0, "output_cost": 5.0,
    },
    "claude-sonnet-4-5-20250929": {
        "provider": "anthropic", "input_cost": 3.0, "output_cost": 15.0,
    },
    # Google Gemini
    "gemini-2.5-flash": {
        "provider": "google", "input_cost": 0.30, "output_cost": 2.50,
    },
    "gemini-2.0-flash": {
        "provider": "google", "input_cost": 0.10, "output_cost": 0.40,
    },
}

# Shortcuts for --model flag
MODEL_ALIASES = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-5-20250929",
    "gemini": "gemini-2.5-flash",
    "gemini-2.5": "gemini-2.5-flash",
    "gemini-2.0": "gemini-2.0-flash",
}

# ── Load test data ──
BENCHMARKS_DIR = os.path.join(PROJECT_ROOT, "benchmarks")

with open(os.path.join(BENCHMARKS_DIR, "test_scenarios.json")) as f:
    ALL_SCENARIOS = json.load(f)["scenarios"]

with open(os.path.join(BENCHMARKS_DIR, "product_catalog.json")) as f:
    CATALOG = json.load(f)

CATALOG_BY_CODE = {p["item_code"]: p for p in CATALOG}


# ═══════════════════════════════════════════════════════════════
# MESSAGE ROUTING (reused from run_all_haiku.py)
# ═══════════════════════════════════════════════════════════════

_COMMON_ORG_WORDS = {
    "good", "food", "concept", "hospitality", "services", "limited",
    "entertainment", "private", "ltd", "llp", "pvt", "the", "and", "for",
    "bellona", "prasuk", "jain", "worldwide", "liberty", "monarch", "snow",
    "world", "innercircle", "hotel", "bar", "cafe", "restaurant",
}


def _ship_to_keywords(ship_to_name):
    words = set()
    for w in re.findall(r'[a-zA-Z]{3,}', ship_to_name.lower()):
        if w not in _COMMON_ORG_WORDS:
            words.add(w)
    return words


def _msg_match_score(msg, ship_words):
    from difflib import SequenceMatcher
    loc = msg.get("location", "").lower()
    msg_text = msg.get("text", "").lower()
    combined = loc + " " + msg_text
    combined_words = set(re.findall(r'[a-zA-Z]{3,}', combined))
    score = 0
    for w in ship_words:
        if len(w) < 4:
            continue
        if w in combined_words:
            score += len(w)
        else:
            for tw in combined_words:
                if len(tw) >= 4 and SequenceMatcher(None, w, tw).ratio() > 0.7:
                    score += len(w) * 0.7
                    break
    return score


def find_relevant_messages(scenario, target_ship_to):
    order_msgs = [m for m in scenario["original_group_messages"]
                  if m["type"] in ("order", "order_addition")]
    if not order_msgs:
        return []

    ship_tos_in_sap = set(i["ship_to_code"] for i in scenario["sap_truth"])
    if len(ship_tos_in_sap) == 1:
        return order_msgs

    all_ship_keywords = {}
    for st in ship_tos_in_sap:
        all_ship_keywords[st] = _ship_to_keywords(st)

    all_msg_texts = [m.get("text", "").lower() + " " + m.get("location", "").lower()
                     for m in order_msgs]
    all_kw = set()
    for kw_set in all_ship_keywords.values():
        all_kw.update(kw_set)
    shared_words = set()
    for kw in all_kw:
        if len(kw) < 4:
            continue
        msg_count = sum(1 for t in all_msg_texts if kw in t)
        if msg_count >= len(order_msgs) * 0.7 and len(order_msgs) >= 3:
            shared_words.add(kw)

    if shared_words:
        for st in all_ship_keywords:
            all_ship_keywords[st] = all_ship_keywords[st] - shared_words

    target_words = all_ship_keywords.get(target_ship_to, set())

    relevant = []
    relevant_groups = set()
    for m in order_msgs:
        scores = {}
        for st, kw in all_ship_keywords.items():
            scores[st] = _msg_match_score(m, kw)
        my_score = scores.get(target_ship_to, 0)
        best_score = max(scores.values()) if scores else 0
        if my_score <= 0:
            continue
        if my_score >= best_score:
            relevant.append(m)
            if m.get("order_group"):
                relevant_groups.add(m["order_group"])

    if relevant_groups:
        for m in order_msgs:
            if m not in relevant and m.get("order_group") in relevant_groups:
                relevant.append(m)

    if not relevant:
        if shared_words and not target_words:
            other_have_kw = any(
                kw_set for st, kw_set in all_ship_keywords.items()
                if st != target_ship_to and kw_set
            )
            if other_have_kw:
                return []
        unassigned = []
        for m in order_msgs:
            scores = {}
            for st, kw in all_ship_keywords.items():
                scores[st] = _msg_match_score(m, kw)
            best = max(scores.values()) if scores else 0
            if best <= 0:
                unassigned.append(m)
        if unassigned:
            relevant = unassigned
        else:
            relevant = order_msgs

    return relevant


# ═══════════════════════════════════════════════════════════════
# API CALLERS (Anthropic + Google)
# ═══════════════════════════════════════════════════════════════

def _call_anthropic(messages, system_prompt, model_id):
    """Call Anthropic Claude API. Returns (text, input_tokens, output_tokens)."""
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model_id,
            "max_tokens": 4096,
            "system": system_prompt,
            "messages": messages,
        },
        timeout=120,
    )
    data = resp.json()
    if resp.status_code == 429:
        return None, 0, 0  # signal retry
    if resp.status_code != 200:
        err = data.get("error", {}).get("message", str(data))
        return f"[API ERROR: {err}]", 0, 0
    text = ""
    for block in data.get("content", []):
        if block.get("type") == "text":
            text += block["text"]
    return text, data["usage"]["input_tokens"], data["usage"]["output_tokens"]


def _call_google(messages, system_prompt, model_id):
    """Call Google Gemini API. Converts Anthropic message format to Gemini format.
    Returns (text, input_tokens, output_tokens)."""
    # Convert messages to Gemini format
    gemini_contents = []
    for msg in messages:
        role = "user" if msg["role"] == "user" else "model"
        gemini_contents.append({
            "role": role,
            "parts": [{"text": msg["content"]}],
        })

    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": gemini_contents,
            "generationConfig": {"maxOutputTokens": 4096},
        },
        timeout=120,
    )
    data = resp.json()
    if resp.status_code == 429:
        return None, 0, 0  # signal retry
    if resp.status_code != 200:
        err = data.get("error", {}).get("message", str(data))
        return f"[API ERROR: {err}]", 0, 0

    # Extract text
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return "[ERROR: No text in response]", 0, 0

    usage = data.get("usageMetadata", {})
    input_tokens = usage.get("promptTokenCount", 0)
    output_tokens = usage.get("candidatesTokenCount", 0)
    return text, input_tokens, output_tokens


def call_api(messages, system_prompt, model_id):
    """Route API call to the correct provider with retry logic."""
    provider = MODEL_REGISTRY.get(model_id, {}).get("provider", "anthropic")
    caller = _call_anthropic if provider == "anthropic" else _call_google

    for attempt in range(3):
        try:
            text, tok_in, tok_out = caller(messages, system_prompt, model_id)
            if text is None:  # rate limited
                wait = min(2 ** (attempt + 1), 30)
                print(f"      Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            return text, tok_in, tok_out
        except requests.exceptions.Timeout:
            if attempt < 2:
                print(f"      Timeout, retrying ({attempt+1}/3)...")
                time.sleep(3)
                continue
            return "[TIMEOUT]", 0, 0
        except Exception as e:
            return f"[ERROR: {e}]", 0, 0
    return "[FAILED after retries]", 0, 0


# ═══════════════════════════════════════════════════════════════
# SYSTEM PROMPT BUILDER
# ═══════════════════════════════════════════════════════════════

def build_system_prompt(scenario):
    return _build_shared_prompt(
        customer_context={
            "card_codes": scenario["card_codes"],
            "card_names": scenario["card_names"],
            "ship_to_addresses": scenario["ship_to_addresses"],
        },
        product_catalog=scenario.get("historical_patterns", []),
        catalog_by_code=CATALOG_BY_CODE,
    )


# ═══════════════════════════════════════════════════════════════
# DETERMINISTIC CORRECTION GENERATOR
# ═══════════════════════════════════════════════════════════════

def build_deterministic_corrections(score_result):
    """Build plain, unambiguous correction messages from score diff.

    Returns list of correction strings. No LLM needed — pure Python.
    """
    corrections = []
    for d in score_result["details"]:
        if d["status"] == "QTY_MISMATCH":
            corrections.append(
                f"{d['sap_item'][:50]} quantity should be {d['sap_qty']:.0f} PCS, not {d['llm_qty']:.0f}"
            )
        elif d["status"] == "MISSED":
            corrections.append(
                f"Missing item: {d['sap_item'][:50]} — need {d['sap_qty']:.0f} PCS"
            )
        elif d["status"] == "EXTRA":
            corrections.append(
                f"Remove {d['llm_item'][:50]} — I didn't order that"
            )
    return corrections


# ═══════════════════════════════════════════════════════════════
# ORDER SCORE CALCULATOR
# ═══════════════════════════════════════════════════════════════

def calculate_order_score(score_result, ship_to_match):
    """Calculate a field-level order score.

    Fields = ship_to (1) + product match per item (N) + qty match per item (N)
    Score = correct_fields / total_fields × 100
    """
    n_items = score_result["target_count"]
    if n_items == 0:
        return 100.0, 0, 0

    # Fields: 1 (ship_to) + N (product matches) + N (qty matches)
    total_fields = 1 + n_items + n_items
    correct_fields = 0

    # Ship-to
    if ship_to_match:
        correct_fields += 1

    # Product matches
    correct_fields += score_result["matched"]

    # Quantity matches
    correct_fields += score_result["qty_correct"]

    # Penalty for extra items (phantom): subtract from score
    # Each extra item is a "wrong field"
    extra_penalty = score_result["extra"]
    total_fields += extra_penalty  # add extra as additional wrong fields

    score = (correct_fields / total_fields) * 100 if total_fields > 0 else 0
    return round(score, 2), correct_fields, total_fields


# ═══════════════════════════════════════════════════════════════
# DISCOVER ALL ORDERS (one per ship-to per scenario)
# ═══════════════════════════════════════════════════════════════

def discover_all_orders(scenario_ids=None):
    orders = []
    for sc in ALL_SCENARIOS:
        if scenario_ids and sc["scenario_id"] not in scenario_ids:
            continue
        ship_tos = set()
        for item in sc["sap_truth"]:
            ship_tos.add(item["ship_to_code"])
        for st in sorted(ship_tos):
            items = [i for i in sc["sap_truth"] if i["ship_to_code"] == st]
            orders.append({
                "scenario_id": sc["scenario_id"],
                "difficulty": sc["difficulty"],
                "chat_name": sc["chat_name"],
                "ship_to": st,
                "item_count": len(items),
            })
    return orders


# ═══════════════════════════════════════════════════════════════
# RUN ONE ORDER
# ═══════════════════════════════════════════════════════════════

MAX_CORRECTION_ROUNDS = 3


def run_one(order_info, model_id):
    """Run a single order through the full eval flow.

    Returns a dict with all KPIs, or None if skipped.
    """
    t_start = time.time()
    sid = order_info["scenario_id"]
    ship_to = order_info["ship_to"]
    scenario = next(s for s in ALL_SCENARIOS if s["scenario_id"] == sid)

    # ── Get relevant messages for this ship-to ──
    relevant_msgs = find_relevant_messages(scenario, ship_to)
    order_text = "\n".join(m["text"] for m in relevant_msgs)
    all_msg_text = order_text

    # ── Get SAP target items ──
    raw_target_items = [i for i in scenario["sap_truth"] if i["ship_to_code"] == ship_to]
    if not raw_target_items:
        return None

    target_items = filter_testable_items(raw_target_items, all_msg_text)
    if not target_items:
        return None

    # ── Build prompts ──
    system_prompt = build_system_prompt(scenario)
    card_names = ", ".join(scenario["card_names"])

    # ── Step 1-3: Send raw customer messages as-is ──
    first_message = f"Hi, this is {card_names}.\n\n{order_text}"
    ship_tos_in_sap = set(i["ship_to_code"] for i in scenario["sap_truth"])
    if len(ship_tos_in_sap) > 1:
        first_message += f"\n\nDelivery to: {ship_to}"

    messages = []
    total_in = total_out = turns = 0
    conv_log = []

    def send(user_msg):
        nonlocal total_in, total_out, turns
        messages.append({"role": "user", "content": user_msg})
        conv_log.append({"role": "customer", "text": user_msg[:500]})
        time.sleep(2)  # rate limit pacing
        resp, tok_in, tok_out = call_api(messages, system_prompt, model_id)
        total_in += tok_in
        total_out += tok_out
        turns += 1
        messages.append({"role": "assistant", "content": resp})
        conv_log.append({"role": "bot", "text": resp[:500]})
        return resp

    # Turn 1: Send the raw order
    bot_resp = send(first_message)

    # Natural conversation — respond to bot's questions
    for turn in range(6):
        response_text, should_extract = auto_respond(bot_resp, ship_to, turn)
        bot_resp = send(response_text)
        if should_extract:
            break

    # ── Step 4: Extract JSON and score (TIER 1 — First Pass) ──
    extraction_resp = send(EXTRACTION_PROMPT)
    llm_json = extract_json(extraction_resp)

    if not llm_json:
        extraction_resp = send("Please output the order as valid JSON only. No other text.")
        llm_json = extract_json(extraction_resp)

    first_pass_score = score_order(llm_json, target_items, ship_to)
    first_pass_order_score, fp_correct, fp_total = calculate_order_score(
        first_pass_score, first_pass_score["ship_to_correct"]
    )

    # ── Step 5-7: Deterministic correction loop (TIER 2 — Correctability) ──
    final_score = first_pass_score
    final_json = llm_json
    correction_rounds = 0

    for round_num in range(MAX_CORRECTION_ROUNDS):
        if final_score["complete_order"]:
            break

        corrections = build_deterministic_corrections(final_score)
        if not corrections:
            break

        correction_rounds += 1
        correction_msg = "Please correct the following:\n" + "\n".join(
            f"  - {c}" for c in corrections
        )
        send(correction_msg)

        re_resp = send(EXTRACTION_PROMPT)
        re_json = extract_json(re_resp)

        if not re_json:
            re_resp = send("Please output the corrected order as valid JSON only.")
            re_json = extract_json(re_resp)

        if re_json:
            final_score = score_order(re_json, target_items, ship_to)
            final_json = re_json

    final_order_score, final_correct, final_total = calculate_order_score(
        final_score, final_score["ship_to_correct"]
    )

    t_end = time.time()
    reg = MODEL_REGISTRY.get(model_id, {"input_cost": 1.0, "output_cost": 5.0})
    cost = (total_in / 1_000_000) * reg["input_cost"] + (total_out / 1_000_000) * reg["output_cost"]

    return {
        # ── Identity ──
        "scenario_id": f"S{sid:02d}",
        "difficulty": order_info["difficulty"],
        "chat_name": order_info["chat_name"],
        "ship_to": ship_to,

        # ── KPI: Counts ──
        "target_count": final_score["target_count"],
        "raw_sap_count": len(raw_target_items),
        "filtered_out": len(raw_target_items) - len(target_items),
        "llm_count": final_score["llm_count"],

        # ── KPI: Tier 1 — First Pass Accuracy ──
        "t1_matched": first_pass_score["matched"],
        "t1_qty_correct": first_pass_score["qty_correct"],
        "t1_extra": first_pass_score["extra"],
        "t1_missed": first_pass_score["missed"],
        "t1_ship_to_correct": first_pass_score["ship_to_correct"],
        "t1_complete": first_pass_score["complete_order"],
        "t1_order_score": first_pass_order_score,

        # ── KPI: Tier 2 — After Corrections (Final) ──
        "matched": final_score["matched"],
        "qty_correct": final_score["qty_correct"],
        "extra": final_score["extra"],
        "missed": final_score["missed"],
        "ship_to_correct": final_score["ship_to_correct"],
        "complete_order": final_score["complete_order"],
        "order_score": final_order_score,

        # ── KPI: Effort ──
        "turns": turns,
        "correction_rounds": correction_rounds,
        "time_taken_s": round(t_end - t_start, 1),

        # ── KPI: Cost ──
        "tokens_in": total_in,
        "tokens_out": total_out,
        "cost": cost,

        # ── Debug ──
        "details": final_score["details"],
        "first_pass_details": first_pass_score["details"],
        "conversation": conv_log,
        "llm_json": final_json,
    }


# ═══════════════════════════════════════════════════════════════
# SUMMARY PRINTER
# ═══════════════════════════════════════════════════════════════

def print_summary(results, model_id, skipped):
    n = len(results)
    if n == 0:
        print("  No results to summarize.")
        return

    # ── Tier 1: First Pass ──
    t1_passed = sum(1 for r in results if r["t1_complete"])
    t1_matched = sum(r["t1_matched"] for r in results)
    t1_target = sum(r["target_count"] for r in results)
    t1_qty = sum(r["t1_qty_correct"] for r in results)
    t1_extra = sum(r["t1_extra"] for r in results)
    t1_missed = sum(r["t1_missed"] for r in results)
    t1_avg_score = sum(r["t1_order_score"] for r in results) / n

    # ── Tier 2: Final (after corrections) ──
    t2_passed = sum(1 for r in results if r["complete_order"])
    t2_matched = sum(r["matched"] for r in results)
    t2_qty = sum(r["qty_correct"] for r in results)
    t2_extra = sum(r["extra"] for r in results)
    t2_avg_score = sum(r["order_score"] for r in results) / n

    # ── Effort ──
    avg_turns = sum(r["turns"] for r in results) / n
    avg_time = sum(r["time_taken_s"] for r in results) / n
    avg_corrections = sum(r["correction_rounds"] for r in results) / n
    total_cost = sum(r["cost"] for r in results)

    print(f"\n{'=' * 74}")
    print(f"  DETERMINISTIC EVAL RESULTS — {model_id}")
    print(f"{'=' * 74}")

    print(f"\n  Orders tested:      {n} (skipped {skipped} with no testable items)")

    print(f"\n  ── TIER 1: First Pass (raw messages only) ──")
    print(f"  Complete PASS:      {t1_passed}/{n} ({t1_passed*100//n}%)")
    print(f"  Product recall:     {t1_matched}/{t1_target} ({t1_matched*100//t1_target if t1_target else 0}%)")
    print(f"  Qty accuracy:       {t1_qty}/{t1_matched} ({t1_qty*100//t1_matched if t1_matched else 0}%)")
    print(f"  Extra (phantom):    {t1_extra}")
    print(f"  Missed:             {t1_missed}")
    print(f"  Avg order score:    {t1_avg_score:.1f}%")

    print(f"\n  ── TIER 2: After Deterministic Corrections ──")
    print(f"  Complete PASS:      {t2_passed}/{n} ({t2_passed*100//n}%)")
    print(f"  Product recall:     {t2_matched}/{t1_target} ({t2_matched*100//t1_target if t1_target else 0}%)")
    print(f"  Qty accuracy:       {t2_qty}/{t2_matched} ({t2_qty*100//t2_matched if t2_matched else 0}%)")
    print(f"  Extra (phantom):    {t2_extra}")
    print(f"  Avg order score:    {t2_avg_score:.1f}%")

    print(f"\n  ── EFFORT & COST ──")
    print(f"  Avg turns:          {avg_turns:.1f}")
    print(f"  Avg corrections:    {avg_corrections:.1f} rounds")
    print(f"  Avg time:           {avg_time:.1f}s per order")
    print(f"  Total cost:         ${total_cost:.4f}")

    # ── By difficulty ──
    for diff in ["EASY", "MEDIUM", "HARD"]:
        dr = [r for r in results if r["difficulty"] == diff]
        if not dr:
            continue
        dn = len(dr)
        d_t1_pass = sum(1 for r in dr if r["t1_complete"])
        d_t2_pass = sum(1 for r in dr if r["complete_order"])
        d_t1_score = sum(r["t1_order_score"] for r in dr) / dn
        d_t2_score = sum(r["order_score"] for r in dr) / dn
        d_target = sum(r["target_count"] for r in dr)
        d_matched = sum(r["t1_matched"] for r in dr)
        d_qty = sum(r["t1_qty_correct"] for r in dr)
        d_extra = sum(r["t1_extra"] for r in dr)
        print(f"\n  {diff}:")
        print(f"    T1 pass: {d_t1_pass}/{dn}  |  recall {d_matched}/{d_target}  |  qty {d_qty}/{d_matched if d_matched else 1}  |  extra {d_extra}  |  avg score {d_t1_score:.1f}%")
        print(f"    T2 pass: {d_t2_pass}/{dn}  |  avg score {d_t2_score:.1f}%")

    # ── Failures detail ──
    failures = [r for r in results if not r["complete_order"]]
    if failures:
        print(f"\n  ── REMAINING FAILURES ({len(failures)}) ──")
        for r in failures:
            print(f"    {r['scenario_id']} | {r['ship_to'][:40]} | score {r['order_score']:.0f}%")
            for d in r["details"]:
                if d["status"] != "MATCH":
                    label = d.get("sap_item", d.get("llm_item", "?"))[:45]
                    if d["status"] == "QTY_MISMATCH":
                        print(f"      {d['status']}: {label} (got {d['llm_qty']:.0f}, want {d['sap_qty']:.0f})")
                    elif d["status"] == "EXTRA":
                        print(f"      {d['status']}: {d.get('llm_item', '?')[:45]}")
                    else:
                        print(f"      {d['status']}: {label}")


# ═══════════════════════════════════════════════════════════════
# COMPARISON PRINTER (multi-model)
# ═══════════════════════════════════════════════════════════════

def print_comparison(all_model_results):
    """Print side-by-side comparison table for multiple models."""
    if len(all_model_results) < 2:
        return

    print(f"\n{'=' * 74}")
    print(f"  HEAD-TO-HEAD COMPARISON")
    print(f"{'=' * 74}")

    # Header
    models = list(all_model_results.keys())
    header = f"  {'Metric':<28s}"
    for m in models:
        short = m.split("/")[-1][:18]
        header += f" | {short:>18s}"
    print(header)
    print("  " + "-" * (30 + 21 * len(models)))

    def row(label, values, fmt="{}", bold_best=None):
        line = f"  {label:<28s}"
        for v in values:
            line += f" | {fmt.format(v):>18s}"
        print(line)

    for label, key, fmt, higher_better in [
        ("T1 Complete PASS", "t1_pass", "{}%", True),
        ("T1 Avg Order Score", "t1_avg", "{:.1f}%", True),
        ("T1 Product Recall", "t1_recall", "{}%", True),
        ("T1 Qty Accuracy", "t1_qty_acc", "{}%", True),
        ("T1 Phantom Items", "t1_extra", "{}", False),
        ("T2 Complete PASS", "t2_pass", "{}%", True),
        ("T2 Avg Order Score", "t2_avg", "{:.1f}%", True),
        ("Avg Turns", "avg_turns", "{:.1f}", False),
        ("Avg Corrections", "avg_corr", "{:.1f}", False),
        ("Avg Time (s)", "avg_time", "{:.1f}", False),
        ("Total Cost", "cost", "${:.4f}", False),
        ("Cost per Order", "cost_per", "${:.4f}", False),
    ]:
        vals = []
        for m in models:
            r = all_model_results[m]["results"]
            n = len(r)
            if n == 0:
                vals.append(0)
                continue
            t1_target = sum(x["target_count"] for x in r)
            t1_matched = sum(x["t1_matched"] for x in r)
            t2_matched = sum(x["matched"] for x in r)
            if key == "t1_pass":
                vals.append(sum(1 for x in r if x["t1_complete"]) * 100 // n)
            elif key == "t1_avg":
                vals.append(sum(x["t1_order_score"] for x in r) / n)
            elif key == "t1_recall":
                vals.append(t1_matched * 100 // t1_target if t1_target else 0)
            elif key == "t1_qty_acc":
                vals.append(sum(x["t1_qty_correct"] for x in r) * 100 // t1_matched if t1_matched else 0)
            elif key == "t1_extra":
                vals.append(sum(x["t1_extra"] for x in r))
            elif key == "t2_pass":
                vals.append(sum(1 for x in r if x["complete_order"]) * 100 // n)
            elif key == "t2_avg":
                vals.append(sum(x["order_score"] for x in r) / n)
            elif key == "avg_turns":
                vals.append(sum(x["turns"] for x in r) / n)
            elif key == "avg_corr":
                vals.append(sum(x["correction_rounds"] for x in r) / n)
            elif key == "avg_time":
                vals.append(sum(x["time_taken_s"] for x in r) / n)
            elif key == "cost":
                vals.append(sum(x["cost"] for x in r))
            elif key == "cost_per":
                vals.append(sum(x["cost"] for x in r) / n)
        row(label, vals, fmt)


# ═══════════════════════════════════════════════════════════════
# RUN ONE MODEL (full eval)
# ═══════════════════════════════════════════════════════════════

def run_model(model_id, all_orders, scenario_ids=None):
    """Run all orders for a single model. Returns (results, skipped)."""
    short_name = model_id.replace("claude-", "").replace("-20251001", "").replace("-20250929", "")
    print(f"\n{'=' * 74}")
    print(f"  DETERMINISTIC EVAL — {len(all_orders)} orders × {short_name}")
    print(f"  Flow: raw msgs → score → deterministic corrections → rescore")
    print(f"  Max correction rounds: {MAX_CORRECTION_ROUNDS}")
    print(f"{'=' * 74}")

    results = []
    skipped = 0

    for i, order in enumerate(all_orders):
        sid = order["scenario_id"]
        ship_to = order["ship_to"]
        diff = order["difficulty"]

        print(f"\n  [{i+1}/{len(all_orders)}] S{sid:02d} ({diff}) | {ship_to[:45]} | {order['item_count']} SAP items")

        try:
            result = run_one(order, model_id)
            if result is None:
                print(f"    SKIP: No testable items")
                skipped += 1
                continue
            result["model"] = model_id
            results.append(result)

            t1_tag = "T1:PASS" if result["t1_complete"] else "T1:FAIL"
            t2_tag = "T2:PASS" if result["complete_order"] else "T2:FAIL"
            filtered_note = ""
            if result["filtered_out"] > 0:
                filtered_note = f" (filtered {result['filtered_out']})"

            print(f"    {t1_tag} -> {t2_tag} | "
                  f"Match: {result['t1_matched']}/{result['target_count']}{filtered_note} | "
                  f"Qty: {result['t1_qty_correct']}/{result['t1_matched']} | "
                  f"Extra: {result['t1_extra']} | "
                  f"Score: {result['t1_order_score']:.0f}%->{result['order_score']:.0f}% | "
                  f"Corrections: {result['correction_rounds']} | "
                  f"${result['cost']:.4f} | "
                  f"{result['time_taken_s']:.0f}s")

            if not result["t1_complete"]:
                for d in result["first_pass_details"]:
                    if d["status"] != "MATCH":
                        label = d.get("llm_item", d.get("sap_item", "?"))[:45]
                        print(f"      {d['status']}: {label}")

        except Exception as e:
            print(f"    ERROR: {str(e)[:80]}")
            import traceback
            traceback.print_exc()
            results.append({
                "model": model_id,
                "scenario_id": f"S{sid:02d}", "difficulty": diff,
                "chat_name": order["chat_name"], "ship_to": ship_to,
                "target_count": order["item_count"], "raw_sap_count": order["item_count"],
                "filtered_out": 0, "llm_count": 0,
                "t1_matched": 0, "t1_qty_correct": 0, "t1_extra": 0, "t1_missed": order["item_count"],
                "t1_ship_to_correct": False, "t1_complete": False, "t1_order_score": 0,
                "matched": 0, "qty_correct": 0, "extra": 0, "missed": order["item_count"],
                "ship_to_correct": False, "complete_order": False, "order_score": 0,
                "turns": 0, "correction_rounds": 0, "time_taken_s": 0,
                "tokens_in": 0, "tokens_out": 0, "cost": 0,
                "details": [], "first_pass_details": [],
                "conversation": [], "llm_json": None, "error": str(e),
            })

    return results, skipped


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def resolve_model(name):
    """Resolve model alias or full ID."""
    name = name.strip()
    if name in MODEL_ALIASES:
        return MODEL_ALIASES[name]
    if name in MODEL_REGISTRY:
        return name
    # Try partial match
    for full_id in MODEL_REGISTRY:
        if name in full_id:
            return full_id
    return name  # pass through, will fail at API call if invalid


def main():
    parser = argparse.ArgumentParser(description="Deterministic Order Bot Eval")
    parser.add_argument("--model", default="haiku",
                        help="Model(s) to test. Comma-separated for comparison. "
                             "Aliases: haiku, sonnet, gemini, gemini-2.0")
    parser.add_argument("--scenarios", default=None,
                        help="Comma-separated scenario IDs (e.g. 1,5,10). Default: all")
    parser.add_argument("--dry-run", action="store_true",
                        help="List orders without calling API")
    args = parser.parse_args()

    # Resolve model names
    model_names = [resolve_model(m) for m in args.model.split(",")]

    # Check API keys
    for model_id in model_names:
        provider = MODEL_REGISTRY.get(model_id, {}).get("provider", "anthropic")
        if provider == "anthropic" and not ANTHROPIC_API_KEY and not args.dry_run:
            print(f"ERROR: No ANTHROPIC_API_KEY for {model_id}")
            print("Set it: export ANTHROPIC_API_KEY=sk-...")
            sys.exit(1)
        if provider == "google" and not GEMINI_API_KEY and not args.dry_run:
            print(f"ERROR: No GEMINI_API_KEY for {model_id}")
            print("Set it: export GEMINI_API_KEY=AIza...")
            sys.exit(1)

    scenario_ids = None
    if args.scenarios:
        scenario_ids = [int(x) for x in args.scenarios.split(",")]

    all_orders = discover_all_orders(scenario_ids)

    if args.dry_run:
        print(f"\n  DRY RUN — {len(all_orders)} orders, models: {', '.join(model_names)}\n")
        for i, order in enumerate(all_orders):
            print(f"  [{i+1:2d}] S{order['scenario_id']:02d} [{order['difficulty']:6s}] "
                  f"{order['chat_name'][:30]:<30s} | {order['ship_to'][:40]} | {order['item_count']} SAP items")
        print(f"\n  Total: {len(all_orders)} orders x {len(model_names)} model(s)")
        return

    # Run each model
    all_model_results = {}
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    results_dir = os.path.join(PROJECT_ROOT, "results")
    os.makedirs(results_dir, exist_ok=True)

    for model_id in model_names:
        results, skipped = run_model(model_id, all_orders, scenario_ids)

        # Save per-model results
        safe_name = model_id.replace("/", "_").replace(".", "_")
        out_path = os.path.join(results_dir, f"deterministic_eval_{safe_name}_{ts}.json")
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

        print_summary(results, model_id, skipped)
        print(f"\n  Results saved: {out_path}")

        all_model_results[model_id] = {"results": results, "skipped": skipped}

    # Comparison table if multiple models
    if len(model_names) > 1:
        print_comparison(all_model_results)

        # Save combined results
        combined_path = os.path.join(results_dir, f"comparison_{ts}.json")
        combined = {}
        for model_id, data in all_model_results.items():
            combined[model_id] = data["results"]
        with open(combined_path, "w") as f:
            json.dump(combined, f, indent=2, default=str)
        print(f"\n  Comparison saved: {combined_path}")


if __name__ == "__main__":
    main()
