#!/usr/bin/env python3
"""
Generate Messages vs SAP Order Excel

Creates an Excel workbook with bifurcated views:
  Sheet 1: Summary — one row per scenario with key metrics
  Sheet 2: Messages — all customer messages across all scenarios
  Sheet 3: SAP Orders — all SAP ground truth lines across all scenarios
  Sheet 4: Side-by-Side — messages and SAP truth aligned per scenario
  Sheet 5: Product Catalog — top products from catalog

Usage:
  python generate_msg_vs_sap.py
"""

import json
import os
import sys

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    print("Installing openpyxl...")
    os.system(f"{sys.executable} -m pip install openpyxl")
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCENARIOS_PATH = os.path.join(SCRIPT_DIR, "test_scenarios.json")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "Messages_vs_SAP_Orders.xlsx")

# Styles
HEADER_FONT = Font(bold=True, color="FFFFFF", size=11)
HEADER_FILL = PatternFill(start_color="202C33", end_color="202C33", fill_type="solid")
EASY_FILL = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
MED_FILL = PatternFill(start_color="FEF9C3", end_color="FEF9C3", fill_type="solid")
HARD_FILL = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
ORDER_FILL = PatternFill(start_color="E8F5E9", end_color="E8F5E9", fill_type="solid")
OPER_FILL = PatternFill(start_color="E3F2FD", end_color="E3F2FD", fill_type="solid")
CHAT_FILL = PatternFill(start_color="F5F5F5", end_color="F5F5F5", fill_type="solid")
ADD_FILL = PatternFill(start_color="FFF8E1", end_color="FFF8E1", fill_type="solid")
THIN_BORDER = Border(
    left=Side(style="thin", color="D0D0D0"),
    right=Side(style="thin", color="D0D0D0"),
    top=Side(style="thin", color="D0D0D0"),
    bottom=Side(style="thin", color="D0D0D0"),
)
WRAP = Alignment(wrap_text=True, vertical="top")

DIFF_FILLS = {"EASY": EASY_FILL, "MEDIUM": MED_FILL, "HARD": HARD_FILL}
TYPE_FILLS = {"order": ORDER_FILL, "operational": OPER_FILL, "chatter": CHAT_FILL, "order_addition": ADD_FILL}


def style_header(ws, row, ncols):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER


def auto_width(ws, min_w=8, max_w=50):
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.value:
                lines = str(cell.value).split("\n")
                max_line = max(len(l) for l in lines) if lines else 0
                max_len = max(max_len, max_line)
        ws.column_dimensions[col_letter].width = max(min_w, min(max_len + 2, max_w))


def load_data():
    with open(SCENARIOS_PATH) as f:
        return json.load(f)


def sheet_summary(wb, scenarios):
    ws = wb.create_sheet("Summary")
    headers = [
        "Scenario", "Difficulty", "Chat Name", "Date", "Card Codes",
        "Customer Messages", "Order Messages", "Operational", "Chatter",
        "SAP Line Items", "Unique Ship-To", "Features"
    ]
    for c, h in enumerate(headers, 1):
        ws.cell(row=1, column=c, value=h)
    style_header(ws, 1, len(headers))

    for r, s in enumerate(scenarios, 2):
        all_msgs = []
        for conv in s["conversations_1to1"]:
            for m in conv["messages"]:
                if m["role"] == "customer":
                    all_msgs.append(m)

        order_msgs = [m for m in all_msgs if m.get("type") == "order"]
        oper_msgs = [m for m in all_msgs if m.get("type") == "operational"]
        chat_msgs = [m for m in all_msgs if m.get("type") == "chatter"]

        ship_tos = set()
        for t in s.get("sap_truth", []):
            ship_tos.add(t.get("ship_to_code", "DEFAULT"))

        row_data = [
            f"S{s['scenario_id']:02d}",
            s["difficulty"],
            s["chat_name"],
            s["date"],
            ", ".join(s["card_codes"]),
            len(all_msgs),
            len(order_msgs),
            len(oper_msgs),
            len(chat_msgs),
            s.get("sap_truth_count", len(s.get("sap_truth", []))),
            len(ship_tos),
            s["features"],
        ]
        for c, v in enumerate(row_data, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = THIN_BORDER
            cell.alignment = WRAP
            if c == 2:
                cell.fill = DIFF_FILLS.get(s["difficulty"], CHAT_FILL)

    auto_width(ws)
    ws.freeze_panes = "A2"


def sheet_messages(wb, scenarios):
    ws = wb.create_sheet("Messages")
    headers = [
        "Scenario", "Difficulty", "Chat Name", "Sender", "Time",
        "Message Type", "Message Text", "Location", "Order Group"
    ]
    for c, h in enumerate(headers, 1):
        ws.cell(row=1, column=c, value=h)
    style_header(ws, 1, len(headers))

    r = 2
    for s in scenarios:
        for conv in s["conversations_1to1"]:
            for m in conv["messages"]:
                if m["role"] != "customer":
                    continue
                msg_type = m.get("type", "")
                row_data = [
                    f"S{s['scenario_id']:02d}",
                    s["difficulty"],
                    s["chat_name"],
                    conv["sender"],
                    m["time"],
                    msg_type,
                    m["text"],
                    m.get("location", ""),
                    m.get("order_group", ""),
                ]
                for c, v in enumerate(row_data, 1):
                    cell = ws.cell(row=r, column=c, value=v)
                    cell.border = THIN_BORDER
                    cell.alignment = WRAP
                    if c == 2:
                        cell.fill = DIFF_FILLS.get(s["difficulty"], CHAT_FILL)
                    if c == 6:
                        cell.fill = TYPE_FILLS.get(msg_type, CHAT_FILL)
                r += 1

    auto_width(ws)
    ws.freeze_panes = "A2"


def sheet_sap(wb, scenarios):
    ws = wb.create_sheet("SAP Orders")
    headers = [
        "Scenario", "Difficulty", "Chat Name", "Ship-To",
        "Item Code", "Description", "Quantity", "Price", "Doc Num"
    ]
    for c, h in enumerate(headers, 1):
        ws.cell(row=1, column=c, value=h)
    style_header(ws, 1, len(headers))

    r = 2
    for s in scenarios:
        for t in s.get("sap_truth", []):
            row_data = [
                f"S{s['scenario_id']:02d}",
                s["difficulty"],
                s["chat_name"],
                t.get("ship_to_code", ""),
                t["item_code"],
                t["description"],
                t["quantity"],
                t.get("price", 0),
                t.get("doc_num", ""),
            ]
            for c, v in enumerate(row_data, 1):
                cell = ws.cell(row=r, column=c, value=v)
                cell.border = THIN_BORDER
                cell.alignment = WRAP
                if c == 2:
                    cell.fill = DIFF_FILLS.get(s["difficulty"], CHAT_FILL)
            r += 1

    auto_width(ws)
    ws.freeze_panes = "A2"


def sheet_side_by_side(wb, scenarios):
    ws = wb.create_sheet("Side-by-Side")
    headers = [
        "Scenario", "Difficulty", "Chat Name",
        "MSG: Sender", "MSG: Time", "MSG: Type", "MSG: Text",
        "|",
        "SAP: Ship-To", "SAP: Item Code", "SAP: Description", "SAP: Qty", "SAP: Price"
    ]
    for c, h in enumerate(headers, 1):
        ws.cell(row=1, column=c, value=h)
    style_header(ws, 1, len(headers))

    # Separator column
    ws.column_dimensions[get_column_letter(8)].width = 3

    r = 2
    for s in scenarios:
        # Collect messages
        msgs = []
        for conv in s["conversations_1to1"]:
            for m in conv["messages"]:
                if m["role"] == "customer":
                    msgs.append({"sender": conv["sender"], **m})

        sap = s.get("sap_truth", [])
        max_rows = max(len(msgs), len(sap), 1)

        for i in range(max_rows):
            # Scenario info (first row only)
            if i == 0:
                ws.cell(row=r, column=1, value=f"S{s['scenario_id']:02d}").border = THIN_BORDER
                c2 = ws.cell(row=r, column=2, value=s["difficulty"])
                c2.border = THIN_BORDER
                c2.fill = DIFF_FILLS.get(s["difficulty"], CHAT_FILL)
                ws.cell(row=r, column=3, value=s["chat_name"]).border = THIN_BORDER
            else:
                for cc in range(1, 4):
                    ws.cell(row=r, column=cc).border = THIN_BORDER

            # Message columns
            if i < len(msgs):
                m = msgs[i]
                msg_type = m.get("type", "")
                ws.cell(row=r, column=4, value=m["sender"]).border = THIN_BORDER
                ws.cell(row=r, column=5, value=m["time"]).border = THIN_BORDER
                tc = ws.cell(row=r, column=6, value=msg_type)
                tc.border = THIN_BORDER
                tc.fill = TYPE_FILLS.get(msg_type, CHAT_FILL)
                mc = ws.cell(row=r, column=7, value=m["text"])
                mc.border = THIN_BORDER
                mc.alignment = WRAP
            else:
                for cc in range(4, 8):
                    ws.cell(row=r, column=cc).border = THIN_BORDER

            # Separator
            sep = ws.cell(row=r, column=8, value="|")
            sep.alignment = Alignment(horizontal="center")
            sep.font = Font(color="CCCCCC")
            sep.border = THIN_BORDER

            # SAP columns
            if i < len(sap):
                t = sap[i]
                ws.cell(row=r, column=9, value=t.get("ship_to_code", "")).border = THIN_BORDER
                ws.cell(row=r, column=10, value=t["item_code"]).border = THIN_BORDER
                ws.cell(row=r, column=11, value=t["description"]).border = THIN_BORDER
                ws.cell(row=r, column=12, value=t["quantity"]).border = THIN_BORDER
                ws.cell(row=r, column=13, value=t.get("price", 0)).border = THIN_BORDER
            else:
                for cc in range(9, 14):
                    ws.cell(row=r, column=cc).border = THIN_BORDER

            r += 1

        # Blank separator row between scenarios
        r += 1

    auto_width(ws)
    ws.freeze_panes = "A2"


def main():
    print("Loading scenarios...")
    data = load_data()
    scenarios = data["scenarios"]
    print(f"  Found {len(scenarios)} scenarios")

    wb = openpyxl.Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    print("Creating Summary sheet...")
    sheet_summary(wb, scenarios)

    print("Creating Messages sheet...")
    sheet_messages(wb, scenarios)

    print("Creating SAP Orders sheet...")
    sheet_sap(wb, scenarios)

    print("Creating Side-by-Side sheet...")
    sheet_side_by_side(wb, scenarios)

    wb.save(OUTPUT_PATH)
    print(f"\nDone! Saved to: {OUTPUT_PATH}")
    print(f"  Sheets: {wb.sheetnames}")


if __name__ == "__main__":
    main()
