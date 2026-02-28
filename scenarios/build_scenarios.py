#!/usr/bin/env python3
"""
Build 25 test scenarios for WhatsApp Order Bot — 1-to-1 format.

Takes group chat messages from whatsapp_master_dataset.xlsx and translates them
into 1-to-1 message format as if each customer/sender messaged the bot directly.

Group → 1-to-1 translation:
  - Group has multiple senders posting orders for different outlets
  - In 1-to-1: each order becomes a direct message from customer to bot
  - TJUK staff messages (acks, operational) become bot context
  - We keep the raw message text exactly as-is
"""

import json
import os
import sys
import openpyxl
import pandas as pd
from collections import defaultdict
from datetime import datetime
import statistics

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(os.path.dirname(SCRIPT_DIR), "TJUK Other files")

# ============================================================
# CHAT → CUSTOMER MAPPING
# ============================================================
CHAT_TO_CARDS = {
    "Bellona VS TJUK": ["CB00367"],
    "Snow World & TJUK ORDERING": ["CS01645", "CP00706", "CP00762"],
    "Sankalp Orders": ["CS01197", "CL00050"],
    "Monarch Liberty": ["CM01282", "CM01292"],
    "Good Food Concept": ["CG00313"],
    "Cremure Order Group": ["CC00815"],
    "Urban Gourmet UGIPL": ["CU00010", "CU00107", "CU00126"],
    "Jalapeno Food Ordering": ["CJ00241"],
    "Kulturd Kombucha": ["CK00598"],
    "BAWA GROUP ORDERING": ["CK00392", "CH00177"],
    "DU Hospitality X TJUK": ["CI00164"],
    "Ketan Oberoi Tower": ["CO00008"],
    "Laxmi Foods Pillsbury": ["CL00037"],
    "Pillsbury & Bake Wish": ["CB00642"],
    "Tjuk group od": ["CG00313"],  # General orders - Good Food Concept
    "BBQ Nation Order Group": ["CB00367"],  # Placeholder
    "Grandmamas - TJUK": ["CG00313"],  # Placeholder
}

# Known TJUK staff senders (they are the company side, not customers)
TJUK_STAFF = {
    "Phone With Kumudini", "Phone With Anushka", "Hem....Tjuk",
    "Phone With Kumu", "Ajay Tjuk", "Tjuk Sanjyokta",
}

# ============================================================
# 25 SELECTED SCENARIOS
# ============================================================
# Selected for diversity: different chats, difficulties, features
# (chat_name, date_dd/mm/yy, difficulty, features_description)
SCENARIOS = [
    # --- EASY (8): Simple orders, clear products, 1-2 senders ---
    ("Good Food Concept", "03/12/25", "EASY",
     "Simple 3-item order with kg and btl units"),
    ("Cremure Order Group", "30/10/25", "EASY",
     "Single product order with kg unit"),
    ("Jalapeno Food Ordering", "13/10/25", "EASY",
     "Simple single-item order"),
    ("Laxmi Foods Pillsbury", "07/10/25", "EASY",
     "Simple order, food products"),
    ("Urban Gourmet UGIPL", "01/09/25", "EASY",
     "Clean multi-item order"),
    ("Cremure Order Group", "26/01/26", "EASY",
     "Multiple items with kg conversion"),
    ("DU Hospitality X TJUK", "07/01/26", "EASY",
     "Simple order, clear product names"),
    ("Ketan Oberoi Tower", "13/08/25", "EASY",
     "Single sender, simple products"),

    # --- MEDIUM (9): Multiple products, conversions, some chatter ---
    ("Good Food Concept", "25/12/25", "MEDIUM",
     "Holiday order, multiple items"),
    ("Bellona VS TJUK", "09/02/26", "MEDIUM",
     "Multiple outlets in single group, case conversions"),
    ("Jalapeno Food Ordering", "18/12/25", "MEDIUM",
     "Multi-product with mixed units"),
    ("Urban Gourmet UGIPL", "26/09/25", "MEDIUM",
     "Multiple items, potential kg/case mix"),
    ("Ketan Oberoi Tower", "20/01/26", "MEDIUM",
     "Multiple products, some operational chatter"),
    ("Cremure Order Group", "16/01/26", "MEDIUM",
     "Orders with location mentions"),
    ("BAWA GROUP ORDERING", "27/10/25", "MEDIUM",
     "Multi-outlet (Bawa Continental + others), kg conversions"),
    ("DU Hospitality X TJUK", "19/02/26", "MEDIUM",
     "Multiple senders, additions"),
    ("Monarch Liberty", "08/01/26", "MEDIUM",
     "Multi-outlet orders (Powai, Santacruz) + fries dispatch"),

    # --- HARD (8): Multi-sender, corrections, cancellations, heavy chatter ---
    ("Snow World & TJUK ORDERING", "06/02/26", "HARD",
     "Multi-customer group (3 CardCodes), heavy chatter"),
    ("Monarch Liberty", "02/01/26", "HARD",
     "Multiple outlets, corrections, operational noise"),
    ("Good Food Concept", "15/12/25", "HARD",
     "Multiple orders, additions, location references"),
    ("DU Hospitality X TJUK", "22/01/26", "HARD",
     "Cancellation (perrier), additions, multi-sender"),
    ("Cremure Order Group", "08/12/25", "HARD",
     "Multiple locations (Malad, Jogeshwari), kg conversions, chatter"),
    ("Bellona VS TJUK", "20/02/26", "HARD",
     "Multiple outlets, case conversions, stock-out handling"),
    ("Snow World & TJUK ORDERING", "17/02/26", "HARD",
     "Multi-customer, mixed products"),
    ("Kulturd Kombucha", "06/02/26", "HARD",
     "Niche products, potential fuzzy matching challenge"),
]


def load_whatsapp_messages():
    """Load all messages from the master dataset."""
    wa_path = os.path.join(DATA_DIR, "whatsapp_master_dataset.xlsx")
    wb = openpyxl.load_workbook(wa_path, read_only=True)
    ws = wb["All Messages"]

    messages = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[1]:
            continue
        messages.append({
            "seq": row[0],
            "chat": str(row[1]),
            "date": str(row[2]),
            "time": str(row[3]) if row[3] else "",
            "sender": str(row[4]) if row[4] else "",
            "message": str(row[5]) if row[5] else "",
            "type": str(row[6]) if row[6] else "",
            "order_group": str(row[7]) if row[7] else "",
            "location": str(row[8]) if row[8] else "",
        })
    return messages


def load_sap_data():
    """Load SAP data: products, orders, ship-to, customer master."""
    sap_path = os.path.join(DATA_DIR, "whatsapp testing data.xlsx")

    orders_df = pd.read_excel(sap_path, sheet_name="Orders")
    products_df = pd.read_excel(sap_path, sheet_name="Product master")
    ship_df = pd.read_excel(sap_path, sheet_name="Ship to")
    customers_df = pd.read_excel(sap_path, sheet_name="Customer master")
    wp_df = pd.read_excel(sap_path, sheet_name="weightpack")

    # Merge weightpack into products
    products_df = products_df.merge(
        wp_df[["ItemCode", "SalPackUn", "BWeight1"]],
        on="ItemCode", how="left"
    )

    # Build historical order ranges
    hist_ranges = {}
    for (card, item), grp in orders_df.groupby(["CardCode", "ItemCode"]):
        qtys = grp["Quantity"].tolist()
        hist_ranges[(card, item)] = {
            "min": float(min(qtys)),
            "max": float(max(qtys)),
            "median": float(statistics.median(qtys)),
            "mean": float(statistics.mean(qtys)),
            "count": len(qtys),
        }

    return orders_df, products_df, ship_df, customers_df, hist_ranges


def translate_to_1to1(messages_on_date, chat_name, card_codes):
    """
    Translate group chat messages into 1-to-1 conversation format.

    In group chats:
      - Customer senders post orders
      - TJUK staff (Anushka, Kumudini, Hem) respond with acks, questions
      - Multiple customers may post in the same group

    In 1-to-1 translation:
      - Each unique customer sender becomes a separate conversation
      - TJUK staff messages become "bot" context (the bot would say these)
      - Order text stays exactly as-is
      - We group by sender to create per-customer conversations
    """
    conversations = []

    # Separate customer messages from staff messages
    customer_msgs = []
    staff_msgs = []

    for m in messages_on_date:
        sender = m["sender"]
        is_staff = any(staff_name in sender for staff_name in TJUK_STAFF)

        if is_staff:
            # If staff is placing an order (not just ack/operational), treat as customer
            # This handles the case where TJUK staff places orders on behalf of customers
            if m["type"] in ("order", "order_addition"):
                customer_msgs.append(m)
            else:
                staff_msgs.append(m)
        else:
            customer_msgs.append(m)

    # Group customer messages by sender
    by_sender = defaultdict(list)
    for m in customer_msgs:
        by_sender[m["sender"]].append(m)

    # Build one conversation per sender
    for sender, msgs in by_sender.items():
        # Interleave with relevant staff responses
        conversation = []
        for m in msgs:
            # Add the customer message
            conversation.append({
                "role": "customer",
                "sender": sender,
                "time": m["time"],
                "text": m["message"],
                "type": m["type"],
                "order_group": m["order_group"],
                "location": m["location"],
            })

        # Add staff messages that appear between/after customer messages as bot context
        for sm in staff_msgs:
            conversation.append({
                "role": "bot_context",
                "sender": sm["sender"],
                "time": sm["time"],
                "text": sm["message"],
                "type": sm["type"],
            })

        # Sort by time
        conversation.sort(key=lambda x: x["time"])

        conversations.append({
            "sender_phone": sender,
            "message_count": len(msgs),
            "order_count": sum(1 for m in msgs if m["type"] in ("order", "order_addition")),
            "messages": conversation,
        })

    return conversations


def build_scenario(idx, chat_name, date_str, difficulty, features,
                   all_messages, orders_df, products_df, ship_df, hist_ranges):
    """Build a single scenario with all context."""
    card_codes = CHAT_TO_CARDS.get(chat_name, [])

    # Get messages for this chat on this date
    msgs_on_date = [
        m for m in all_messages
        if m["chat"] == chat_name and m["date"] == date_str
    ]

    if not msgs_on_date:
        print(f"  WARNING: No messages found for {chat_name} on {date_str}")
        return None

    # Get SAP truth for this date
    try:
        target_date = datetime.strptime(date_str, "%d/%m/%y").strftime("%Y-%m-%d")
    except ValueError:
        print(f"  WARNING: Cannot parse date {date_str}")
        return None

    sap_truth = orders_df[
        (orders_df["CardCode"].isin(card_codes))
        & (orders_df["DocDate"].dt.strftime("%Y-%m-%d") == target_date)
    ]

    # Get customer products (items they've ever ordered)
    cust_orders = orders_df[orders_df["CardCode"].isin(card_codes)]
    cust_items = cust_orders["ItemCode"].unique()
    cust_products = products_df[products_df["ItemCode"].isin(cust_items)].copy()

    # Get ship-to addresses
    cust_ship = ship_df[ship_df["CardCode"].isin(card_codes)]
    ship_addresses = list(cust_ship["Address"].dropna().unique())
    card_names = list(cust_orders["CardName"].unique()[:3])

    # Build historical patterns for this customer
    customer_hist = []
    for cc in card_codes:
        for (card, item), stats in hist_ranges.items():
            if card == cc:
                item_match = products_df[products_df["ItemCode"] == item]
                item_name = item_match.iloc[0]["ItemName"] if len(item_match) > 0 else item
                customer_hist.append({
                    "item_code": item,
                    "item_name": str(item_name),
                    "order_count": stats["count"],
                    "min_qty": stats["min"],
                    "max_qty": stats["max"],
                    "median_qty": stats["median"],
                })
    customer_hist.sort(key=lambda x: x["order_count"], reverse=True)

    # Translate to 1-to-1 format
    conversations = translate_to_1to1(msgs_on_date, chat_name, card_codes)

    # Build the original group messages (for reference)
    original_group_messages = []
    for m in sorted(msgs_on_date, key=lambda x: x["time"]):
        original_group_messages.append({
            "time": m["time"],
            "sender": m["sender"],
            "text": m["message"],
            "type": m["type"],
            "order_group": m["order_group"],
            "location": m["location"],
        })

    # SAP truth as serializable format
    sap_truth_lines = []
    for _, row in sap_truth.iterrows():
        sap_truth_lines.append({
            "item_code": str(row["ItemCode"]),
            "description": str(row["Dscription"]),
            "quantity": float(row["Quantity"]),
            "price": float(row["Price"]) if pd.notna(row["Price"]) else 0,
            "ship_to_code": str(row.get("ShipToCode", "")),
            "doc_num": str(row["DocNum"]),
        })

    # Product catalog (serializable)
    catalog = []
    for _, p in cust_products.iterrows():
        pack = p.get("SalPackUn", 1)
        weight = p.get("BWeight1", None)
        catalog.append({
            "item_code": str(p["ItemCode"]),
            "item_name": str(p["ItemName"]),
            "uom": str(p.get("SalUnitMsr", "PCS")),
            "pack_size": int(pack) if pd.notna(pack) else 1,
            "unit_weight_kg": float(weight) if pd.notna(weight) else 0,
        })

    return {
        "scenario_id": idx,
        "difficulty": difficulty,
        "features": features,
        "chat_name": chat_name,
        "date": date_str,
        "date_iso": target_date,
        "card_codes": card_codes,
        "card_names": card_names,
        "ship_to_addresses": ship_addresses,

        # The 1-to-1 translated conversations
        "conversations_1to1": [
            {
                "sender": conv["sender_phone"],
                "message_count": conv["message_count"],
                "order_count": conv["order_count"],
                "messages": conv["messages"],
            }
            for conv in conversations
        ],

        # Original group messages (for reference/comparison)
        "original_group_messages": original_group_messages,

        # SAP ground truth
        "sap_truth": sap_truth_lines,
        "sap_truth_count": len(sap_truth_lines),

        # Product catalog for this customer
        "product_catalog_count": len(catalog),

        # Historical order patterns (top 30)
        "historical_patterns": customer_hist[:30],

        # Stats
        "total_messages": len(msgs_on_date),
        "order_messages": sum(1 for m in msgs_on_date if m["type"] in ("order", "order_addition")),
        "chatter_messages": sum(1 for m in msgs_on_date if m["type"] not in ("order", "order_addition")),
        "unique_senders": len(set(m["sender"] for m in msgs_on_date)),
        "conversation_count": len(conversations),
    }


def main():
    print("=" * 70)
    print("Building 25 Test Scenarios — Group → 1-to-1 Translation")
    print("=" * 70)

    print("\nLoading WhatsApp messages...")
    all_messages = load_whatsapp_messages()
    print(f"  {len(all_messages)} messages loaded")

    print("\nLoading SAP data...")
    orders_df, products_df, ship_df, customers_df, hist_ranges = load_sap_data()
    print(f"  {len(orders_df)} orders, {len(products_df)} products, "
          f"{len(ship_df)} ship-to addresses, {len(hist_ranges)} historical patterns")

    print(f"\nBuilding {len(SCENARIOS)} scenarios...")
    scenarios = []
    for idx, (chat, date, diff, features) in enumerate(SCENARIOS, 1):
        print(f"\n  S{idx:02d} [{diff:<6}] {chat} @ {date}")
        scenario = build_scenario(
            idx, chat, date, diff, features,
            all_messages, orders_df, products_df, ship_df, hist_ranges
        )
        if scenario:
            scenarios.append(scenario)
            print(f"    → {scenario['total_messages']} msgs total, "
                  f"{scenario['order_messages']} orders, "
                  f"{scenario['conversation_count']} conversations (1-to-1), "
                  f"{scenario['sap_truth_count']} SAP truth lines")
        else:
            print(f"    → SKIPPED (no data)")

    # Save scenarios
    output_path = os.path.join(SCRIPT_DIR, "test_scenarios.json")
    with open(output_path, "w") as f:
        json.dump({
            "metadata": {
                "created": datetime.now().isoformat(),
                "total_scenarios": len(scenarios),
                "easy": sum(1 for s in scenarios if s["difficulty"] == "EASY"),
                "medium": sum(1 for s in scenarios if s["difficulty"] == "MEDIUM"),
                "hard": sum(1 for s in scenarios if s["difficulty"] == "HARD"),
                "description": (
                    "25 test scenarios for WhatsApp Order Bot. "
                    "Each scenario takes a day's group chat messages and translates them "
                    "into 1-to-1 format. SAP ground truth is included for scoring."
                ),
            },
            "scenarios": scenarios,
        }, f, indent=2, default=str)

    print(f"\n{'=' * 70}")
    print(f"DONE: {len(scenarios)} scenarios saved to {output_path}")
    print(f"{'=' * 70}")

    # Print summary table
    print(f"\n{'S#':<4} {'Diff':<7} {'Chat':<32} {'Date':<10} {'Msgs':>5} {'Orders':>7} "
          f"{'Conv':>5} {'SAP':>4} {'Features'}")
    print("-" * 120)
    for s in scenarios:
        print(f"S{s['scenario_id']:02d} {s['difficulty']:<7} {s['chat_name']:<32} "
              f"{s['date']:<10} {s['total_messages']:>5} {s['order_messages']:>7} "
              f"{s['conversation_count']:>5} {s['sap_truth_count']:>4} {s['features']}")

    # Totals
    total_msgs = sum(s["total_messages"] for s in scenarios)
    total_orders = sum(s["order_messages"] for s in scenarios)
    total_sap = sum(s["sap_truth_count"] for s in scenarios)
    print(f"\n{'TOTALS':<44} {'':<10} {total_msgs:>5} {total_orders:>7} "
          f"{'':>5} {total_sap:>4}")

    # Also save the product catalog separately (for reference/prompt building)
    catalog_path = os.path.join(SCRIPT_DIR, "product_catalog.json")
    catalog = []
    for _, p in products_df.iterrows():
        pack = p.get("SalPackUn", 1)
        weight = p.get("BWeight1", None)
        catalog.append({
            "item_code": str(p["ItemCode"]),
            "item_name": str(p["ItemName"]),
            "foreign_name": str(p.get("FrgnName", "")),
            "group": str(p.get("ItmsGrpNam", "")),
            "uom": str(p.get("SalUnitMsr", "PCS")),
            "pack_size": int(pack) if pd.notna(pack) else 1,
            "unit_weight_kg": float(weight) if pd.notna(weight) else 0,
        })
    with open(catalog_path, "w") as f:
        json.dump(catalog, f, indent=2, default=str)
    print(f"\nProduct catalog saved to {catalog_path} ({len(catalog)} items)")


if __name__ == "__main__":
    main()
