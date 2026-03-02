"""
SAP Business One adapter.

Standard client adapter interface for SAP B1 Service Layer.
"""


def create_order(client_config, order_data):
    """Push a parsed order into SAP B1 as a Sales Order."""
    raise NotImplementedError("SAP B1 order creation not yet implemented")


def get_products(client_config):
    """Fetch product catalog from SAP B1.

    Uses client_config["product_catalog"]["columns"] for field mapping.
    """
    raise NotImplementedError("SAP B1 product fetch not yet implemented")


def get_customer(client_config, phone_number):
    """Look up customer in SAP B1 by phone number."""
    raise NotImplementedError("SAP B1 customer lookup not yet implemented")
