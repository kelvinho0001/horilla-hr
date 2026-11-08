"""
Connector contract. Every provider returns ``NormalizedTransaction`` objects, so
nothing downstream (mapping, attendance, payroll) knows which vendor produced
them. A connector declares what it can do through ``capabilities``; calling an
operation it does not declare raises ``NotSupported``.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

TEST_CONNECTION = "test_connection"
FETCH_TRANSACTIONS = "fetch_transactions"
FETCH_EMPLOYEES = "fetch_employees"
FETCH_DEVICES = "fetch_devices"
CREATE_EMPLOYEES = "create_employees"


class NotSupported(Exception):
    """The connector does not implement this operation."""


class ConnectorError(Exception):
    """The source could not be reached or answered with an error."""


@dataclass
class NormalizedTransaction:
    external_employee_id: str
    timestamp: datetime  # naive = in the provider's source timezone
    external_transaction_id: str = ""
    external_device_id: str = ""
    verification_method: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class ExternalEmployee:
    external_employee_id: str
    name: str = ""
    code: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class RowError:
    row: Optional[int]
    message: str

    def as_dict(self):
        return {"row": self.row, "message": self.message}


class BaseConnector:
    provider_type = ""
    capabilities: frozenset = frozenset()

    def __init__(self, provider):
        self.provider = provider

    def supports(self, capability):
        return capability in self.capabilities

    def _require(self, capability):
        if capability not in self.capabilities:
            raise NotSupported(f"{self.provider_type} does not support {capability}")

    def test_connection(self):
        """Return (ok: bool, message: str)."""
        raise NotSupported("test_connection")

    def fetch_transactions(self, since=None, until=None):
        """Return (list[NormalizedTransaction], list[RowError])."""
        raise NotSupported("fetch_transactions")

    def fetch_employees(self):
        """Return list[ExternalEmployee]."""
        raise NotSupported("fetch_employees")

    def fetch_devices(self):
        raise NotSupported("fetch_devices")
