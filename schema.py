

def empty_order() -> dict:
    """A blank record matching the schema above -- used as a base so the
    review page always has every key to render, even if extraction
    dropped one."""
    return {
        "order": {
            "order_date": None,
            "external_reference": None,
            "customer_id": None,
            "currency": None,
        },
        "debtor": {
            "company": None,
            "contact_name": None,
            "alias": None,
            "email": None,
            "phone": None,
            "billing_address": {"street": None, "zip": None, "city": None, "country": None},
            "delivery_address": {"street": None, "zip": None, "city": None, "country": None},
            "payment_method": None,
            "paid_status": None,
            "payment_date": None,
        },
        "items": [],
    }