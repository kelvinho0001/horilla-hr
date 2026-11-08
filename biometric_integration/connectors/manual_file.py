"""
Manual CSV / Excel attendance report import.

Report layouts differ between Time.Net, BioTime and others, so columns are
found by header name (case-insensitive, aliases below) and can be overridden in
``ProviderConfig.config["columns"]``. Either one date-time column, or a date
column plus a time column, is accepted. Dates are read day-first
(dd/mm/yyyy) unless ISO.
"""

import csv
import io
import re
from datetime import date, datetime, time

import openpyxl

from .base import (
    FETCH_TRANSACTIONS,
    BaseConnector,
    ConnectorError,
    NormalizedTransaction,
    RowError,
)

ALIASES = {
    "employee_id": [
        "emp id", "emp_id", "empid", "employee id", "employee_id", "emp code",
        "emp_code", "employee code", "user id", "userid", "user_id", "badge id",
        "badge", "person id", "ac-no", "ac no", "no", "id",
    ],
    "datetime": [
        "datetime", "date time", "date/time", "date_time", "punch time",
        "punch_time", "check time", "checktime", "time stamp", "timestamp",
        "punch datetime", "attendance time",
    ],
    "date": ["date", "punch date", "attendance date", "day"],
    "time": ["time", "punch time of day", "check in", "clock in", "in time"],
    "device": ["device", "device id", "device sn", "device serial", "machine", "sn"],
    "txn_id": ["transaction id", "txn id", "record id", "log id", "seq"],
    "method": ["verify", "verify type", "verification", "verification method", "state"],
    "name": ["name", "employee name", "emp name", "full name", "first name"],
}

DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%y", "%d-%m-%y",
)
TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p")
DATETIME_FORMATS = tuple(
    f"{d} {t}" for d in DATE_FORMATS for t in TIME_FORMATS
) + ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


def _norm(header):
    return " ".join(str(header or "").strip().lower().replace("_", " ").split())


def _parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{text}'")


def _parse_time(value):
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    text = str(value).strip()
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"unrecognised time '{text}'")


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date/time '{text}'")


def _clean_id(value):
    """Excel hands numeric IDs back as floats ('12.0'); keep text, drop '.0'."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    # CSV exported from Excel: "12.0" is the number 12, not an ID with a dot.
    if re.fullmatch(r"\d+\.0+", text):
        return text.split(".")[0]
    return text


def _read_rows(file_obj, filename):
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            wb = openpyxl.load_workbook(file_obj, read_only=True, data_only=True)
        except Exception as exc:
            raise ConnectorError(f"Cannot read Excel file: {exc}") from exc
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    if name.endswith((".csv", ".txt")):
        raw = file_obj.read()
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                raw = raw.decode("latin-1")
        return list(csv.reader(io.StringIO(raw)))
    raise ConnectorError("Unsupported file type. Upload a .csv or .xlsx file.")


class ManualFileConnector(BaseConnector):
    provider_type = "manual_file"
    capabilities = frozenset({FETCH_TRANSACTIONS})

    def _column_map(self, header):
        overrides = (self.provider.config or {}).get("columns", {})
        normed = [_norm(h) for h in header]
        found = {}
        for key, aliases in ALIASES.items():
            wanted = [_norm(overrides[key])] if key in overrides else aliases
            for alias in wanted:
                if alias in normed:
                    found[key] = normed.index(alias)
                    break
        return found

    def parse(self, file_obj, filename):
        """Return (transactions, errors). Row numbers are 1-based file rows."""
        rows = _read_rows(file_obj, filename)
        rows = [r for r in rows if any(str(c).strip() for c in r if c is not None)]
        if not rows:
            raise ConnectorError("The file is empty.")
        header, body = rows[0], rows[1:]
        cols = self._column_map(header)
        if "employee_id" not in cols:
            raise ConnectorError(
                "No employee ID column found. Expected one of: "
                + ", ".join(ALIASES["employee_id"][:6])
                + "…"
            )
        has_dt = "datetime" in cols
        if not has_dt and not ("date" in cols and "time" in cols):
            raise ConnectorError(
                "No timestamp found. Provide a date/time column, or separate "
                "date and time columns."
            )

        header_names = [str(h or "") for h in header]
        txns, errors = [], []
        for offset, row in enumerate(body, start=2):
            def cell(key):
                idx = cols.get(key)
                if idx is None or idx >= len(row) or row[idx] is None:
                    return ""
                return row[idx]

            emp = _clean_id(cell("employee_id"))
            if not emp:
                errors.append(RowError(offset, "Missing employee ID"))
                continue
            try:
                if has_dt:
                    stamp = _parse_datetime(cell("datetime"))
                else:
                    stamp = datetime.combine(
                        _parse_date(cell("date")), _parse_time(cell("time"))
                    )
            except ValueError as exc:
                errors.append(RowError(offset, f"Invalid timestamp: {exc}"))
                continue
            raw = {
                header_names[i]: (v.isoformat() if hasattr(v, "isoformat") else v)
                for i, v in enumerate(row)
                if i < len(header_names) and header_names[i]
            }
            txns.append(
                NormalizedTransaction(
                    external_employee_id=emp,
                    timestamp=stamp,
                    external_transaction_id=str(cell("txn_id")).strip(),
                    external_device_id=str(cell("device")).strip(),
                    verification_method=str(cell("method")).strip(),
                    raw=raw,
                )
            )
        return txns, errors
