"""
Generic adapter - fallback for clients without a specific ERP integration.

Uses local Excel/CSV files from the client's data/ directory.
"""

import json
from pathlib import Path


def create_order(client_config, order_data):
    """Log order to a local JSON file for manual processing.

    Used when no ERP integration is configured yet.
    """
    # TODO: Write order to clients/<id>/data/pending_orders.json
    raise NotImplementedError("Generic order creation not yet implemented")


def get_products(client_config):
    """Load product catalog from local Excel file.

    Reads from the path specified in client_config["product_catalog"]["local_fallback"]
    """
    # TODO: Read Excel with openpyxl/pandas
    raise NotImplementedError("Generic product fetch not yet implemented")


def get_customer(client_config, phone_number):
    """Look up customer from local customer list file."""
    # TODO: Read customer list from client data directory
    raise NotImplementedError("Generic customer lookup not yet implemented")
