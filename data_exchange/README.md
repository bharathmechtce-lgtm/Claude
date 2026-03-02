# Data Exchange

This folder is your drop zone for feeding data into the project **without touching the main structure**.

## How to Use

### `inbox/`
Drop any files here that you want processed or reviewed:
- New WhatsApp chat exports (.zip or .txt)
- Updated product lists (.xlsx, .csv)
- Customer lists
- New test data
- Any files you want Claude Code to pick up

### `processed/`
Files that have been consumed/processed are moved here automatically with a timestamp prefix so you know when they were handled.

## Naming Convention
No strict naming required - just drop files in `inbox/`. However, prefixing with the client name helps:
- `TJUK_new_product_list.xlsx`
- `ACS_customer_update.csv`
- `NEWCLIENT_chat_export.zip`

## Important
- This folder is **gitignored** (except this README) to keep the repo clean
- Large files should go here rather than directly into `clients/`
- The processing scripts will move files to `processed/` after handling them
