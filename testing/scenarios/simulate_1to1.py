#!/usr/bin/env python3
"""
WhatsApp Order Bot — Interactive 1-to-1 Simulator

Simulates a customer sending WhatsApp messages to the TJUK order bot
one at a time, with configurable delays to test the silence-detection
/ batch-window mechanic.

Flow:
  1. Select difficulty (Easy / Medium / Hard)
  2. Select scenario (shows sender, msg count, customer count, total qty)
  3. Set batch window (seconds the bot waits for silence before processing)
  4. Send messages one by one with simulated inter-message delays
  5. When delay >= batch window → bot processes the batch & responds
  6. At end → compare LLM's final extracted order vs SAP ground truth

Usage:
  python3 simulate_1to1.py
"""

import json
import os
import re
import sys
import time
import requests
import pandas as pd
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "TJUK Other files")
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# Shared modules
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, REPO_ROOT)
from src.core.prompts import build_system_prompt as _build_shared_prompt, EXTRACTION_PROMPT
from testing.eval.scorer_utils import extract_json, score_order, filter_testable_items
SCENARIOS_PATH = os.path.join(SCRIPT_DIR, "test_scenarios.json")

# Load .env from project root
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

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DEFAULT_MODEL = "claude-sonnet-4-5-20250929"

# ============================================================
# COLORS (ANSI)
# ============================================================
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_GREEN = "\033[92m"
C_YELLOW = "\033[93m"
C_RED = "\033[91m"
C_CYAN = "\033[96m"
C_BLUE = "\033[94m"
C_MAGENTA = "\033[95m"
C_WHITE = "\033[97m"
C_BG_GREEN = "\033[42m"
C_BG_YELLOW = "\033[43m"
C_BG_RED = "\033[41m"

DIFF_COLORS = {
    "EASY": C_GREEN,
    "MEDIUM": C_YELLOW,
    "HARD": C_RED,
}


def clear_screen():
    os.system("clear" if os.name != "nt" else "cls")


def header(text):
    w = 70
    print(f"\n{C_CYAN}{'=' * w}")
    print(f"  {text}")
    print(f"{'=' * w}{C_RESET}")


def subheader(text):
    print(f"\n{C_BOLD}{C_WHITE}--- {text} ---{C_RESET}")


def prompt_input(text, default=None):
    if default is not None:
        display = f"{C_BOLD}{text}{C_RESET} [{C_DIM}{default}{C_RESET}]: "
    else:
        display = f"{C_BOLD}{text}{C_RESET}: "
    val = input(display).strip()
    if not val and default is not None:
        return str(default)
    return val


# ============================================================
# CONVERSATIONAL SYSTEM PROMPT
# ============================================================
def build_system_prompt(scenario):
    """Build the system prompt using the shared prompt builder."""
    return _build_shared_prompt(
        customer_context={
            "card_codes": scenario["card_codes"],
            "card_names": scenario["card_names"],
            "ship_to_addresses": scenario["ship_to_addresses"],
        },
        historical_patterns=scenario["historical_patterns"],
    )


# ============================================================
# LLM CALLER
# ============================================================
def call_llm(messages, system_prompt, model=DEFAULT_MODEL):
    """Call Claude API with conversation history. Returns (response_text, tokens_in, tokens_out)."""
    payload = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": messages,
    }

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        data = resp.json()
        if resp.status_code != 200:
            err = data.get("error", {}).get("message", str(data))
            return f"[API ERROR: {err}]", 0, 0

        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block["text"]

        in_tok = data["usage"]["input_tokens"]
        out_tok = data["usage"]["output_tokens"]
        return text, in_tok, out_tok

    except Exception as e:
        return f"[EXCEPTION: {str(e)[:200]}]", 0, 0


def print_comparison(result):
    """Print a formatted comparison table."""
    header("FINAL COMPARISON: LLM vs SAP Ground Truth")

    total_sap = result["target_count"]
    matched = result["matched"]
    qty_correct = result["qty_correct"]
    extra = result["extra"]
    missed = result["missed"]

    # Summary
    prod_acc = matched / total_sap * 100 if total_sap > 0 else 0
    qty_acc = qty_correct / matched * 100 if matched > 0 else 0

    print(f"\n  {C_BOLD}Product Match:{C_RESET}  {matched}/{total_sap} "
          f"({prod_acc:.0f}%)  ", end="")
    if prod_acc >= 80:
        print(f"{C_GREEN}GOOD{C_RESET}")
    elif prod_acc >= 50:
        print(f"{C_YELLOW}PARTIAL{C_RESET}")
    else:
        print(f"{C_RED}POOR{C_RESET}")

    print(f"  {C_BOLD}Quantity Match:{C_RESET} {qty_correct}/{matched} "
          f"({qty_acc:.0f}%)  ", end="")
    if qty_acc >= 80:
        print(f"{C_GREEN}GOOD{C_RESET}")
    elif qty_acc >= 50:
        print(f"{C_YELLOW}PARTIAL{C_RESET}")
    else:
        print(f"{C_RED}POOR{C_RESET}")

    print(f"  {C_BOLD}Extra Items:{C_RESET}    {extra}")
    print(f"  {C_BOLD}Missed Items:{C_RESET}   {missed}")

    # Detail table
    subheader("Line-by-Line Detail")
    print(f"  {'Status':<14} {'LLM Item':<30} {'LLM Qty':>8} {'SAP Item':<30} {'SAP Qty':>8} {'Match':>6}")
    print(f"  {'-' * 100}")

    for d in sorted(result["details"], key=lambda x: (x["status"] != "MATCH",
                                                        x["status"] != "QTY_MISMATCH",
                                                        x["status"] != "EXTRA")):
        status = d["status"]
        if status == "MATCH":
            color = C_GREEN
            tag = "  MATCH"
        elif status == "QTY_MISMATCH":
            color = C_YELLOW
            tag = "  QTY DIFF"
        elif status == "EXTRA":
            color = C_RED
            tag = "  EXTRA"
        else:
            color = C_RED
            tag = "  MISSED"

        llm_item = str(d["llm_item"])[:28]
        sap_item = str(d["sap_item"])[:28]
        llm_qty = d["llm_qty"]
        sap_qty = d["sap_qty"]
        score = d["match_score"]

        print(f"  {color}{tag:<14}{C_RESET} {llm_item:<30} {llm_qty:>8.0f} "
              f"{sap_item:<30} {sap_qty:>8.0f} {score:>5.0%}")

    # Overall score
    overall = prod_acc * qty_acc / 100 if total_sap > 0 else 0
    print(f"\n  {C_BOLD}Overall Score: {overall:.1f}%{C_RESET}", end="  ")
    if overall >= 70:
        print(f"{C_BG_GREEN}{C_WHITE} PASS {C_RESET}")
    elif overall >= 40:
        print(f"{C_BG_YELLOW}{C_WHITE} PARTIAL {C_RESET}")
    else:
        print(f"{C_BG_RED}{C_WHITE} FAIL {C_RESET}")


# ============================================================
# MAIN SIMULATION
# ============================================================
def load_scenarios():
    with open(SCENARIOS_PATH) as f:
        data = json.load(f)
    return data["scenarios"]


def select_scenario(scenarios):
    """Interactive scenario selection."""
    clear_screen()
    header("WhatsApp Order Bot — 1-to-1 Simulator")

    # Step 1: Difficulty
    subheader("Step 1: Select Difficulty")
    print(f"  {C_GREEN}[1] EASY{C_RESET}   — Simple orders, 1-2 items, clear units")
    print(f"  {C_YELLOW}[2] MEDIUM{C_RESET} — Multi-item, conversions, multiple senders")
    print(f"  {C_RED}[3] HARD{C_RESET}   — Corrections, cancellations, multi-sender, heavy chatter")
    print(f"  {C_CYAN}[4] ALL{C_RESET}    — Show all scenarios")

    choice = prompt_input("\n  Pick", "4")
    diff_map = {"1": "EASY", "2": "MEDIUM", "3": "HARD", "4": None}
    diff_filter = diff_map.get(choice)

    if diff_filter:
        filtered = [s for s in scenarios if s["difficulty"] == diff_filter]
    else:
        filtered = scenarios

    if not filtered:
        print(f"{C_RED}  No scenarios found.{C_RESET}")
        return None

    # Step 2: Pick scenario
    subheader("Step 2: Select Scenario")
    print(f"  {'#':<4} {'Diff':<7} {'Chat':<28} {'Date':<10} {'Msgs':>5} "
          f"{'Cust':>5} {'SAP':>4}  Features")
    print(f"  {'-' * 95}")

    for s in filtered:
        dc = DIFF_COLORS.get(s["difficulty"], "")
        # Count unique customer senders
        cust_count = sum(
            1 for c in s["conversations_1to1"]
            if any(m["role"] == "customer" and m.get("type") in ("order", "order_addition")
                   for m in c["messages"])
        )
        total_cust_msgs = sum(
            sum(1 for m in c["messages"] if m["role"] == "customer")
            for c in s["conversations_1to1"]
        )

        print(f"  {dc}S{s['scenario_id']:02d}{C_RESET}  "
              f"{dc}{s['difficulty']:<7}{C_RESET} "
              f"{s['chat_name']:<28} "
              f"{s['date']:<10} "
              f"{total_cust_msgs:>5} "
              f"{cust_count:>5} "
              f"{s['sap_truth_count']:>4}  "
              f"{s['features'][:35]}")

    sid = prompt_input(f"\n  Enter scenario # (e.g. 1)")
    try:
        sid = int(sid)
    except ValueError:
        print(f"{C_RED}  Invalid input.{C_RESET}")
        return None

    selected = next((s for s in scenarios if s["scenario_id"] == sid), None)
    if not selected:
        print(f"{C_RED}  Scenario S{sid:02d} not found.{C_RESET}")
        return None

    return selected


def show_scenario_detail(scenario):
    """Show detailed view of the selected scenario."""
    s = scenario
    dc = DIFF_COLORS.get(s["difficulty"], "")

    header(f"S{s['scenario_id']:02d} [{dc}{s['difficulty']}{C_RESET}] "
           f"{s['chat_name']} — {s['date']}")

    print(f"  Customer:    {', '.join(s['card_codes'])} — {', '.join(s['card_names'])}")
    print(f"  Ship-to:     {', '.join(s['ship_to_addresses'][:3])}")
    print(f"  SAP truth:   {s['sap_truth_count']} line items")
    print(f"  Conversations: {s['conversation_count']} (1-to-1)")

    subheader("All Messages (in chronological order)")
    # Collect all customer messages from all conversations (dedup by text+time)
    all_msgs = []
    seen = set()
    for conv in s["conversations_1to1"]:
        for m in conv["messages"]:
            key = (m["time"], m["text"][:50], m["role"])
            if key not in seen:
                seen.add(key)
                all_msgs.append({
                    **m,
                    "sender_label": conv["sender"] if m["role"] == "customer" else m.get("sender", "BOT"),
                })
    all_msgs.sort(key=lambda x: x["time"])

    for i, m in enumerate(all_msgs, 1):
        role = m["role"]
        msg_type = m.get("type", "")
        if role == "customer":
            role_color = C_GREEN
            role_tag = "CUSTOMER"
        else:
            role_color = C_BLUE
            role_tag = "BOT"

        type_tag = f" [{msg_type}]" if msg_type else ""
        text_preview = m["text"].replace("\n", " | ")[:65]
        sender = m["sender_label"][:20]

        print(f"  {C_DIM}M{i:02d}{C_RESET} [{m['time']:>10}] "
              f"{role_color}{role_tag:<8}{C_RESET} "
              f"{C_DIM}{sender:<22}{C_RESET} "
              f"{text_preview}{C_DIM}{type_tag}{C_RESET}")


def run_simulation(scenario):
    """Run the interactive simulation for a scenario."""
    s = scenario

    # Step 3: Batch window
    subheader("Step 3: Configure Batch Window")
    print(f"\n  The batch window is how long the bot waits for silence before processing.")
    print(f"  If you send messages faster than this → they get batched together.")
    print(f"  If the gap exceeds this → bot processes the batch and responds.\n")
    batch_window = int(prompt_input("  Batch window (seconds)", "60"))

    # Build system prompt
    system_prompt = build_system_prompt(s)

    # Collect all customer messages across conversations (in order)
    customer_messages = []
    for conv in s["conversations_1to1"]:
        for m in conv["messages"]:
            if m["role"] == "customer":
                customer_messages.append({
                    "sender": conv["sender"],
                    "time": m["time"],
                    "text": m["text"],
                    "type": m.get("type", ""),
                    "location": m.get("location", ""),
                })

    # Sort by time
    customer_messages.sort(key=lambda x: x["time"])

    if not customer_messages:
        print(f"\n{C_RED}  No customer messages to simulate!{C_RESET}")
        return

    header("Simulation Start")
    print(f"  Batch window: {C_BOLD}{batch_window}s{C_RESET}")
    print(f"  Customer messages to send: {C_BOLD}{len(customer_messages)}{C_RESET}")
    print(f"  SAP truth items: {C_BOLD}{s['sap_truth_count']}{C_RESET}")
    print(f"\n  {C_DIM}For each message, enter the simulated delay in seconds since the previous message.")
    print(f"  If delay < {batch_window}s → message gets batched with previous.")
    print(f"  If delay >= {batch_window}s → bot processes batch first, then queues this message.{C_RESET}")

    # Conversation state
    conversation_history = []  # Anthropic messages format
    current_batch = []  # Messages waiting to be sent
    total_in_tokens = 0
    total_out_tokens = 0
    batch_num = 0
    simulated_clock = 0  # running clock in seconds

    for msg_idx, msg in enumerate(customer_messages):
        subheader(f"Message {msg_idx + 1}/{len(customer_messages)}")
        print(f"  {C_GREEN}From:{C_RESET} {msg['sender']}")
        print(f"  {C_GREEN}Time:{C_RESET} {msg['time']}")
        print(f"  {C_GREEN}Type:{C_RESET} {msg['type']}")
        if msg["location"]:
            print(f"  {C_GREEN}Location:{C_RESET} {msg['location']}")
        print(f"\n  {C_BOLD}{C_WHITE}{msg['text']}{C_RESET}\n")

        # Get simulated delay
        if msg_idx == 0:
            delay = 0
            print(f"  {C_DIM}(First message — no delay){C_RESET}")
        else:
            delay_input = prompt_input(
                f"  Seconds since previous message",
                "5"
            )
            try:
                delay = int(delay_input)
            except ValueError:
                delay = 5

        simulated_clock += delay

        # Check if we need to process the current batch first
        if delay >= batch_window and current_batch:
            # Process batch BEFORE adding this message
            batch_num += 1
            batch_text = "\n\n".join(
                f"[{m['time']}] {m['text']}" for m in current_batch
            )

            print(f"\n  {C_CYAN}>> BATCH {batch_num} TRIGGERED (silence detected: "
                  f"{delay}s >= {batch_window}s window){C_RESET}")
            print(f"  {C_DIM}Processing {len(current_batch)} message(s)...{C_RESET}")

            # Send to LLM
            conversation_history.append({
                "role": "user",
                "content": batch_text,
            })

            response, in_tok, out_tok = call_llm(
                conversation_history, system_prompt
            )
            total_in_tokens += in_tok
            total_out_tokens += out_tok

            conversation_history.append({
                "role": "assistant",
                "content": response,
            })

            print(f"\n  {C_BLUE}{C_BOLD}BOT:{C_RESET}")
            for line in response.split("\n"):
                print(f"  {C_BLUE}{line}{C_RESET}")
            print(f"  {C_DIM}[{in_tok} in / {out_tok} out tokens]{C_RESET}")

            current_batch = []

        # Add this message to the batch
        current_batch.append(msg)
        print(f"  {C_DIM}→ Added to batch ({len(current_batch)} msg(s) queued, "
              f"clock: {simulated_clock}s){C_RESET}")

    # Process final batch
    if current_batch:
        batch_num += 1
        batch_text = "\n\n".join(
            f"[{m['time']}] {m['text']}" for m in current_batch
        )

        print(f"\n  {C_CYAN}>> FINAL BATCH {batch_num} (all messages sent){C_RESET}")
        print(f"  {C_DIM}Processing {len(current_batch)} message(s)...{C_RESET}")

        conversation_history.append({
            "role": "user",
            "content": batch_text,
        })

        response, in_tok, out_tok = call_llm(
            conversation_history, system_prompt
        )
        total_in_tokens += in_tok
        total_out_tokens += out_tok

        conversation_history.append({
            "role": "assistant",
            "content": response,
        })

        print(f"\n  {C_BLUE}{C_BOLD}BOT:{C_RESET}")
        for line in response.split("\n"):
            print(f"  {C_BLUE}{line}{C_RESET}")
        print(f"  {C_DIM}[{in_tok} in / {out_tok} out tokens]{C_RESET}")

    # Step 5: Extract final order
    header("Extracting Final Order (JSON)")
    print(f"  {C_DIM}Asking the bot to output its final structured order...{C_RESET}\n")

    conversation_history.append({
        "role": "user",
        "content": EXTRACTION_PROMPT,
    })

    extraction_response, in_tok, out_tok = call_llm(
        conversation_history, system_prompt
    )
    total_in_tokens += in_tok
    total_out_tokens += out_tok

    print(f"  {C_DIM}{extraction_response[:500]}...{C_RESET}" if len(extraction_response) > 500
          else f"  {C_DIM}{extraction_response}{C_RESET}")

    # Parse JSON
    llm_json = extract_json(extraction_response)
    if not llm_json:
        print(f"\n  {C_RED}Failed to parse JSON from LLM response!{C_RESET}")
        print(f"  {C_DIM}Raw response saved for debugging.{C_RESET}")
    else:
        # Count lines
        total_lines = sum(
            len(order.get("lines", []))
            for order in llm_json.get("orders", [])
        )
        print(f"\n  {C_GREEN}Parsed: {total_lines} line items across "
              f"{len(llm_json.get('orders', []))} order(s){C_RESET}")

    # Step 6: Compare with SAP truth
    # Collect customer message text for filtering
    all_customer_text = "\n".join(
        m["text"] for conv in s["conversations_1to1"]
        for m in conv["messages"] if m["role"] == "customer"
    )
    testable_truth = filter_testable_items(s["sap_truth"], all_customer_text)
    result = score_order(llm_json, testable_truth)
    print_comparison(result)

    # Cost summary
    subheader("Token Usage")
    cost = (total_in_tokens / 1_000_000) * 3.0 + (total_out_tokens / 1_000_000) * 15.0
    print(f"  Input:  {total_in_tokens:,} tokens")
    print(f"  Output: {total_out_tokens:,} tokens")
    print(f"  Batches processed: {batch_num}")
    print(f"  Est. cost: ${cost:.4f}")

    # Save results
    results_path = os.path.join(
        SCRIPT_DIR,
        f"sim_result_S{s['scenario_id']:02d}_{datetime.now().strftime('%H%M%S')}.json"
    )
    with open(results_path, "w") as f:
        json.dump({
            "scenario_id": s["scenario_id"],
            "chat_name": s["chat_name"],
            "date": s["date"],
            "difficulty": s["difficulty"],
            "batch_window": batch_window,
            "batches": batch_num,
            "total_in_tokens": total_in_tokens,
            "total_out_tokens": total_out_tokens,
            "cost": cost,
            "conversation_history": conversation_history,
            "llm_extracted_json": llm_json,
            "comparison": {k: v for k, v in result.items() if k != "details"},
            "comparison_details": result["details"],
        }, f, indent=2, default=str)
    print(f"\n  {C_DIM}Results saved to: {results_path}{C_RESET}")


# ============================================================
# ENTRY POINT
# ============================================================
def main():
    global ANTHROPIC_KEY

    # Check API key
    if not ANTHROPIC_KEY:
        print(f"\n{C_YELLOW}  ANTHROPIC_API_KEY not set in environment.{C_RESET}")
        key = prompt_input("  Enter your Anthropic API key").strip()
        if not key:
            print(f"{C_RED}  No API key provided. Exiting.{C_RESET}")
            return
        ANTHROPIC_KEY = key

    scenarios = load_scenarios()

    while True:
        scenario = select_scenario(scenarios)
        if not scenario:
            break

        show_scenario_detail(scenario)

        proceed = prompt_input(f"\n  Start simulation? (y/n)", "y")
        if proceed.lower() != "y":
            continue

        run_simulation(scenario)

        again = prompt_input(f"\n  Run another scenario? (y/n)", "y")
        if again.lower() != "y":
            break

    print(f"\n{C_CYAN}Goodbye!{C_RESET}\n")


if __name__ == "__main__":
    main()
