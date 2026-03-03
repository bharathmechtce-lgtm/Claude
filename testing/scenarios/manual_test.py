#!/usr/bin/env python3
"""
Manual Testing Simulator for WhatsApp Order Bot.

YOU chat as the customer. The LLM responds as the bot.
Each failed scenario is loaded with full context so you can see
what the customer originally said and what SAP expects.

Workflow:
  1. Pick model (Haiku or Gemini)
  2. Tool loads the next failed scenario
  3. You see: customer messages (reference) + SAP expected items
  4. You type messages to the bot — or press Enter to auto-send next customer message
  5. When done, type /done to extract the order and score it
  6. Type /next to move to next scenario, /skip to skip

All conversations are recorded to testing/results/manual_test_logs/

Usage:
    python testing/scenarios/manual_test.py
"""

import json
import os
import sys
import time
import re
import requests
from datetime import datetime

# ── Paths ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BENCHMARKS_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "benchmarks")
RESULTS_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "results")
LOG_DIR = os.path.join(RESULTS_DIR, "manual_test_logs")
REPO_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

sys.path.insert(0, REPO_ROOT)
from src.core.prompts import build_system_prompt as _build_shared_prompt, EXTRACTION_PROMPT
from testing.eval.scorer_utils import extract_json, score_order, filter_testable_items

# ── Load data ──
with open(os.path.join(BENCHMARKS_DIR, "test_scenarios.json")) as f:
    ALL_SCENARIOS = json.load(f)["scenarios"]
SCENARIOS_BY_ID = {f"S{s['scenario_id']:02d}": s for s in ALL_SCENARIOS}

with open(os.path.join(BENCHMARKS_DIR, "product_catalog.json")) as f:
    CATALOG = json.load(f)
CATALOG_BY_CODE = {p["item_code"]: p for p in CATALOG}

# ── .env ──
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

_load_dotenv(os.path.join(REPO_ROOT, ".env"))
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

# ── Models ──
MODELS = {
    "1": {
        "id": "claude-haiku-4-5-20251001",
        "label": "Haiku 4.5",
        "provider": "anthropic",
        "input_cost": 1.0,
        "output_cost": 5.0,
    },
    "2": {
        "id": "gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
        "provider": "google",
        "input_cost": 0.30,
        "output_cost": 2.50,
    },
}

# ── Colors ──
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_DIM = "\033[2m"
C_RED = "\033[31m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_BLUE = "\033[34m"
C_MAGENTA = "\033[35m"
C_CYAN = "\033[36m"
C_WHITE = "\033[37m"
C_BG_BLUE = "\033[44m"
C_BG_GREEN = "\033[42m"


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def header(text):
    print(f"\n{C_BOLD}{C_BG_BLUE}{C_WHITE}  {text}  {C_RESET}\n")


def subheader(text):
    print(f"\n  {C_BOLD}{C_CYAN}{text}{C_RESET}")
    print(f"  {C_CYAN}{'─' * len(text)}{C_RESET}")


# ══════════════════════════════════════════════════════════════
# LLM CALLERS
# ══════════════════════════════════════════════════════════════

def call_anthropic(messages, system_prompt, model_id):
    payload = {
        "model": model_id,
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
            json=payload, timeout=120,
        )
        data = resp.json()
        text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        return text, usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    except Exception as e:
        return f"[API Error: {e}]", 0, 0


def call_google(messages, system_prompt, model_id):
    contents = []
    for m in messages:
        role = "user" if m["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": m["content"]}]})
    try:
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent?key={GEMINI_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": contents,
                "generationConfig": {"maxOutputTokens": 4096},
            },
            timeout=120,
        )
        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        usage = data.get("usageMetadata", {})
        return text, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0)
    except Exception as e:
        return f"[API Error: {e}]", 0, 0


def call_llm(messages, system_prompt, model):
    if model["provider"] == "google":
        return call_google(messages, system_prompt, model["id"])
    return call_anthropic(messages, system_prompt, model["id"])


# ══════════════════════════════════════════════════════════════
# FAILURE LOADER
# ══════════════════════════════════════════════════════════════

def load_failures():
    """Load the 16 failed scenarios from the latest comparison results."""
    comparison_files = sorted(
        [f for f in os.listdir(RESULTS_DIR) if f.startswith("comparison_") and f.endswith(".json")],
        reverse=True,
    )
    if not comparison_files:
        print(f"{C_RED}No comparison results found. Run deterministic_eval.py first.{C_RESET}")
        sys.exit(1)

    with open(os.path.join(RESULTS_DIR, comparison_files[0])) as f:
        results = json.load(f)

    haiku = {(r["scenario_id"], r["ship_to"]): r for r in results.get("claude-haiku-4-5-20251001", [])}
    gemini = {(r["scenario_id"], r["ship_to"]): r for r in results.get("gemini-2.5-flash", [])}

    failures = []
    all_keys = sorted(set(haiku.keys()) | set(gemini.keys()))
    for key in all_keys:
        h = haiku.get(key, {})
        g = gemini.get(key, {})
        if not h.get("complete_order", False) or not g.get("complete_order", False):
            failures.append({
                "scenario_id": key[0],
                "ship_to": key[1],
                "haiku": h,
                "gemini": g,
            })
    return failures


def build_system_prompt(scenario):
    return _build_shared_prompt(
        customer_context={
            "card_codes": scenario["card_codes"],
            "card_names": scenario["card_names"],
            "ship_to_addresses": scenario["ship_to_addresses"],
        },
        product_catalog=scenario["historical_patterns"],
        catalog_by_code=CATALOG_BY_CODE,
    )


# ══════════════════════════════════════════════════════════════
# DISPLAY
# ══════════════════════════════════════════════════════════════

def show_scenario_context(failure, scenario, failure_num, total):
    """Show the scenario info, customer messages, and SAP truth."""
    sid = failure["scenario_id"]
    ship_to = failure["ship_to"]
    h = failure["haiku"]
    g = failure["gemini"]

    header(f"SCENARIO {failure_num}/{total}: {sid} | {ship_to}")

    # Metadata
    print(f"  {C_BOLD}Difficulty:{C_RESET}  {scenario.get('difficulty', '?')}")
    print(f"  {C_BOLD}Chat Name:{C_RESET}   {scenario.get('chat_name', '?')}")
    print(f"  {C_BOLD}Date:{C_RESET}        {scenario.get('date_iso', '?')}")

    h_status = f"{C_GREEN}PASS{C_RESET}" if h.get("complete_order") else f"{C_RED}FAIL({h.get('order_score', 0):.0f}%){C_RESET}"
    g_status = f"{C_GREEN}PASS{C_RESET}" if g.get("complete_order") else f"{C_RED}FAIL({g.get('order_score', 0):.0f}%){C_RESET}"
    print(f"  {C_BOLD}Prev Haiku:{C_RESET}  {h_status}")
    print(f"  {C_BOLD}Prev Gemini:{C_RESET} {g_status}")

    # Customer messages
    subheader("CUSTOMER MESSAGES (reference — what the customer originally sent)")
    msg_index = 0
    customer_msgs = []
    for conv in scenario.get("conversations_1to1", []):
        for m in conv.get("messages", []):
            msg_index += 1
            mtype = m.get("type", "?")
            role = m.get("role", "?")
            prefix = f"{C_GREEN}ORDER{C_RESET}" if mtype in ("order", "order_addition") else f"{C_DIM}{mtype}{C_RESET}"
            print(f"  {C_DIM}[{m.get('time', '?')}]{C_RESET} {prefix} {m.get('text', '')[:120]}")
            if role == "customer" and mtype in ("order", "order_addition"):
                customer_msgs.append(m.get("text", ""))

    # SAP expected
    sap_items = [t for t in scenario.get("sap_truth", [])
                 if t.get("ship_to_code", "") == ship_to]

    subheader(f"SAP EXPECTED ORDER ({len(sap_items)} items for {ship_to})")
    for i, item in enumerate(sap_items, 1):
        print(f"  {C_BOLD}{i}.{C_RESET} {item.get('description', '?'):<50} "
              f"{C_YELLOW}qty={item.get('quantity', 0)}{C_RESET}  "
              f"{C_DIM}[{item.get('item_code', '?')}]{C_RESET}")

    return customer_msgs, sap_items


def show_score(result):
    """Print the scoring result."""
    total = result["target_count"]
    matched = result["matched"]
    qty_ok = result["qty_correct"]
    extra = result["extra"]
    missed = result["missed"]
    complete = result["complete_order"]

    prod_pct = matched / total * 100 if total > 0 else 0
    qty_pct = qty_ok / matched * 100 if matched > 0 else 0

    status = f"{C_GREEN}{C_BOLD}PASS{C_RESET}" if complete else f"{C_RED}{C_BOLD}FAIL{C_RESET}"

    subheader(f"SCORE: {status}")
    print(f"  Product Match:  {matched}/{total} ({prod_pct:.0f}%)")
    print(f"  Quantity Match: {qty_ok}/{matched} ({qty_pct:.0f}%)")
    print(f"  Extra Items:    {extra}")
    print(f"  Missed Items:   {missed}")
    print(f"  Ship-to OK:     {'Yes' if result.get('ship_to_correct') else 'No'}")

    if result["details"]:
        print(f"\n  {'Status':<14} {'LLM Item':<35} {'Qty':>6} {'SAP Item':<35} {'Qty':>6}")
        print(f"  {'─'*100}")
        for d in sorted(result["details"], key=lambda x: x["status"] != "MATCH"):
            s = d["status"]
            color = C_GREEN if s == "MATCH" else C_RED if s in ("MISSED", "EXTRA") else C_YELLOW
            print(f"  {color}{s:<14}{C_RESET} "
                  f"{str(d.get('llm_item', '-'))[:35]:<35} {d.get('llm_qty', 0):>6.0f} "
                  f"{str(d.get('sap_item', '-'))[:35]:<35} {d.get('sap_qty', 0):>6.0f}")

    return complete


# ══════════════════════════════════════════════════════════════
# CONVERSATION SESSION
# ══════════════════════════════════════════════════════════════

def run_session(failure, scenario, model, failure_num, total):
    """Run one manual chat session for a failed scenario.

    Returns: dict with full session log and results.
    """
    sid = failure["scenario_id"]
    ship_to = failure["ship_to"]

    customer_msgs, sap_items = show_scenario_context(failure, scenario, failure_num, total)

    # Filter SAP truth to testable items
    all_customer_text = "\n".join(customer_msgs)
    testable_truth = filter_testable_items(sap_items, all_customer_text)
    if not testable_truth:
        testable_truth = sap_items

    system_prompt = build_system_prompt(scenario)
    conversation = []  # Full log: [{role, content, timestamp}]
    api_messages = []  # What gets sent to the LLM
    auto_msg_index = 0  # Which customer message to auto-send next
    total_in = 0
    total_out = 0

    subheader("CHAT SESSION")
    print(f"  {C_DIM}Commands:{C_RESET}")
    print(f"  {C_CYAN}Enter{C_RESET}     = Auto-send next customer message")
    print(f"  {C_CYAN}(type){C_RESET}    = Send your own message")
    print(f"  {C_CYAN}/done{C_RESET}     = Extract order & score")
    print(f"  {C_CYAN}/skip{C_RESET}     = Skip this scenario")
    print(f"  {C_CYAN}/quit{C_RESET}     = Quit the tool\n")

    session_start = datetime.now()
    extracted = False
    score_result = None

    while True:
        # Show remaining auto-messages
        if auto_msg_index < len(customer_msgs):
            next_preview = customer_msgs[auto_msg_index][:80]
            print(f"  {C_DIM}[Next auto-message: \"{next_preview}...\"]{C_RESET}")

        # Get user input
        try:
            user_input = input(f"\n  {C_GREEN}{C_BOLD}YOU:{C_RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        # Handle commands
        if user_input.lower() == "/quit":
            return None
        if user_input.lower() == "/skip":
            return {"skipped": True, "scenario_id": sid, "ship_to": ship_to}
        if user_input.lower() == "/done":
            # Extract the order
            print(f"\n  {C_DIM}Extracting order...{C_RESET}")
            api_messages.append({"role": "user", "content": EXTRACTION_PROMPT})
            conversation.append({
                "role": "user", "content": "[EXTRACTION]",
                "timestamp": datetime.now().isoformat(),
            })

            resp, tok_in, tok_out = call_llm(api_messages, system_prompt, model)
            total_in += tok_in
            total_out += tok_out

            api_messages.append({"role": "assistant", "content": resp})
            conversation.append({
                "role": "assistant", "content": resp,
                "timestamp": datetime.now().isoformat(),
            })

            print(f"\n  {C_BLUE}{C_BOLD}BOT (JSON):{C_RESET}")
            for line in resp.split("\n")[:30]:
                print(f"  {C_BLUE}{line}{C_RESET}")

            llm_json = extract_json(resp)
            if llm_json:
                score_result = score_order(llm_json, testable_truth, ship_to)
                passed = show_score(score_result)
                extracted = True
            else:
                print(f"\n  {C_RED}Could not parse JSON from response.{C_RESET}")
                score_result = {"target_count": len(testable_truth), "matched": 0,
                                "qty_correct": 0, "extra": 0, "missed": len(testable_truth),
                                "complete_order": False, "details": []}

            # Ask what to do
            print(f"\n  {C_DIM}[Enter = next scenario | type to continue chatting | /quit to exit]{C_RESET}")
            try:
                choice = input(f"  {C_GREEN}{C_BOLD}YOU:{C_RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                choice = ""

            if choice.lower() == "/quit":
                return None
            if not choice:
                # Move to next scenario
                break
            # Continue chatting
            api_messages.pop()  # Remove assistant extraction response
            api_messages.pop()  # Remove extraction prompt
            user_input = choice  # Fall through to send this message
            extracted = False
            # Continue the loop with the new message
            if user_input.lower() in ("/done", "/skip", "/quit"):
                continue

        # Determine message to send
        if not user_input:
            # Auto-send next customer message
            if auto_msg_index < len(customer_msgs):
                user_input = customer_msgs[auto_msg_index]
                auto_msg_index += 1
                print(f"  {C_GREEN}[Auto]{C_RESET} {user_input[:120]}")
            else:
                print(f"  {C_DIM}No more auto-messages. Type your own or /done to extract.{C_RESET}")
                continue

        # Send to LLM
        api_messages.append({"role": "user", "content": user_input})
        conversation.append({
            "role": "user", "content": user_input,
            "timestamp": datetime.now().isoformat(),
        })

        print(f"  {C_DIM}Thinking...{C_RESET}", end="", flush=True)
        t0 = time.time()
        resp, tok_in, tok_out = call_llm(api_messages, system_prompt, model)
        elapsed = time.time() - t0
        total_in += tok_in
        total_out += tok_out

        api_messages.append({"role": "assistant", "content": resp})
        conversation.append({
            "role": "assistant", "content": resp,
            "timestamp": datetime.now().isoformat(),
        })

        print(f"\r  {C_BLUE}{C_BOLD}BOT:{C_RESET} ({elapsed:.1f}s, {tok_in}+{tok_out} tok)")
        for line in resp.split("\n"):
            print(f"  {C_BLUE}{line}{C_RESET}")

    # Build session result
    cost = (total_in * model["input_cost"] + total_out * model["output_cost"]) / 1_000_000

    return {
        "scenario_id": sid,
        "ship_to": ship_to,
        "difficulty": scenario.get("difficulty", ""),
        "chat_name": scenario.get("chat_name", ""),
        "model": model["id"],
        "model_label": model["label"],
        "conversation": conversation,
        "tokens_in": total_in,
        "tokens_out": total_out,
        "cost": cost,
        "target_count": len(testable_truth),
        "score": score_result,
        "complete": score_result.get("complete_order", False) if score_result else False,
        "started": session_start.isoformat(),
        "ended": datetime.now().isoformat(),
    }


# ══════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════

def main():
    os.makedirs(LOG_DIR, exist_ok=True)

    clear_screen()
    header("WhatsApp Order Bot — Manual Testing Tool")

    # Load failures
    failures = load_failures()
    print(f"  Loaded {C_BOLD}{len(failures)}{C_RESET} failed scenarios\n")

    # Show failures summary
    for i, f in enumerate(failures, 1):
        h = f["haiku"]
        g = f["gemini"]
        h_s = f"{C_GREEN}PASS{C_RESET}" if h.get("complete_order") else f"{C_RED}FAIL{C_RESET}"
        g_s = f"{C_GREEN}PASS{C_RESET}" if g.get("complete_order") else f"{C_RED}FAIL{C_RESET}"
        items = h.get("target_count", g.get("target_count", "?"))
        print(f"  {C_BOLD}{i:>2}.{C_RESET} {f['scenario_id']} | {f['ship_to'][:40]:<40} "
              f"| H={h_s} G={g_s} | {items} items")

    # Pick model
    print(f"\n  {C_BOLD}Select Model:{C_RESET}")
    print(f"  {C_CYAN}[1]{C_RESET} Haiku 4.5        (Anthropic — $1.00/$5.00 per MTok)")
    print(f"  {C_CYAN}[2]{C_RESET} Gemini 2.5 Flash  (Google — $0.30/$2.50 per MTok)")
    model_choice = input(f"\n  Model [1]: ").strip() or "1"
    model = MODELS.get(model_choice, MODELS["1"])
    print(f"  → Using {C_BOLD}{model['label']}{C_RESET}\n")

    # Pick starting scenario
    start = input(f"  Start from failure # [1]: ").strip() or "1"
    try:
        start_idx = int(start) - 1
    except ValueError:
        start_idx = 0

    # Session log
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_log = {
        "model": model["id"],
        "model_label": model["label"],
        "started": datetime.now().isoformat(),
        "results": [],
    }

    # Run through failures
    total = len(failures)
    passed = 0
    tested = 0

    for i in range(start_idx, total):
        failure = failures[i]
        sid = failure["scenario_id"]
        scenario = SCENARIOS_BY_ID.get(sid)

        if not scenario:
            print(f"  {C_RED}Scenario {sid} not found in test data!{C_RESET}")
            continue

        clear_screen()
        result = run_session(failure, scenario, model, i + 1, total)

        if result is None:
            # User quit
            print(f"\n  {C_DIM}Quitting...{C_RESET}")
            break

        session_log["results"].append(result)

        if result.get("skipped"):
            print(f"  {C_DIM}Skipped {sid} | {failure['ship_to']}{C_RESET}")
            continue

        tested += 1
        if result.get("complete"):
            passed += 1
            print(f"\n  {C_GREEN}{C_BOLD}✓ PASSED{C_RESET}")
        else:
            print(f"\n  {C_RED}{C_BOLD}✗ FAILED{C_RESET}")

        # Summary so far
        print(f"\n  {C_DIM}Progress: {passed}/{tested} passed "
              f"({passed/tested*100:.0f}%) | {total - i - 1} remaining{C_RESET}")

        # Wait for next
        print(f"\n  {C_DIM}Press Enter for next scenario, or /quit to stop{C_RESET}")
        try:
            cmd = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            cmd = "/quit"
        if cmd.lower() == "/quit":
            break

    # Save session log
    session_log["ended"] = datetime.now().isoformat()
    session_log["total_tested"] = tested
    session_log["total_passed"] = passed
    log_path = os.path.join(LOG_DIR, f"manual_{model['id']}_{ts}.json")
    with open(log_path, "w") as f:
        json.dump(session_log, f, indent=2, ensure_ascii=False)

    # Final summary
    clear_screen()
    header("MANUAL TEST SESSION COMPLETE")
    print(f"  Model:   {model['label']}")
    print(f"  Tested:  {tested} scenarios")
    print(f"  Passed:  {passed}/{tested} ({passed/tested*100:.0f}%)" if tested else "  Passed:  0/0")

    total_cost = sum(r.get("cost", 0) for r in session_log["results"] if not r.get("skipped"))
    print(f"  Cost:    ${total_cost:.4f}")
    print(f"  Log:     {log_path}")

    # Per-scenario summary
    print(f"\n  {'#':<4} {'Scenario':<8} {'Ship To':<35} {'Result':<8} {'Score'}")
    print(f"  {'─'*70}")
    for r in session_log["results"]:
        if r.get("skipped"):
            print(f"  {'-':<4} {r['scenario_id']:<8} {r['ship_to'][:35]:<35} {C_DIM}SKIP{C_RESET}")
            continue
        score = r.get("score", {})
        complete = r.get("complete", False)
        status = f"{C_GREEN}PASS{C_RESET}" if complete else f"{C_RED}FAIL{C_RESET}"
        m = score.get("matched", 0)
        t = score.get("target_count", r.get("target_count", 0))
        q = score.get("qty_correct", 0)
        print(f"  {'·':<4} {r['scenario_id']:<8} {r['ship_to'][:35]:<35} {status:<8} "
              f"prod={m}/{t} qty={q}/{m}")


if __name__ == "__main__":
    main()
