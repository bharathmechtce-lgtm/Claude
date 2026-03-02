"""
Generic adapter - fallback for clients without a specific ERP integration.
Uses local Excel/CSV files from the client's data/ directory.
"""


def create_order(client_config, order_data):
    """Log order to local file for manual processing."""
    raise NotImplementedError("Generic order creation not yet implemented")


def get_products(client_config):
    """Load product catalog from local Excel file."""
    raise NotImplementedError("Generic product fetch not yet implemented")


def get_customer(client_config, phone_number):
    """Look up customer from local customer list file."""
    raise NotImplementedError("Generic customer lookup not yet implemented")
