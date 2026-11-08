"""Stable de-duplication keys so a re-import can never create a second row."""

import hashlib


def dedup_key(provider_id, external_employee_id, timestamp, external_device_id="",
              external_transaction_id=""):
    """
    Prefer the source's own transaction id. Without one, key on provider +
    device + employee + the exact instant (``timestamp`` must be aware UTC-able).
    """
    if external_transaction_id:
        basis = f"{provider_id}|txn|{external_transaction_id}"
    else:
        basis = "|".join(
            [
                str(provider_id),
                "ts",
                str(external_device_id),
                str(external_employee_id),
                timestamp.isoformat(),
            ]
        )
    return hashlib.sha256(basis.encode()).hexdigest()
