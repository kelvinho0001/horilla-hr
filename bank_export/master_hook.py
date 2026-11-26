"""
Bank columns on the Employees import (master_import app) and on its update mode.

The sheet only needs Bank Code 1 + Account Number. From the bank list this fills in:
  * Bank Name (so the mapping/validation step no longer asks for it),
  * Bank Branch (default Lagos),
and checks the account number (10 digits, not used by another employee). After the employees are saved the accounts
are checked with Paystack so the employee record shows the account name.

Everything is a no-op until a bank list has been loaded, so Horilla's own behaviour is unchanged without it.
"""

from django.conf import settings

from employee.models import EmployeeBankDetails

from . import paystack, services
from .models import Bank, EmployeeBankLink

MAX_CHECKS_PER_IMPORT = 40


def _clean(v):
    return "" if v is None else str(v).strip()


def enrich_row(row):
    """Fill Bank Name / Code / Branch from the bank list. Sets row['__bank_problem'] for an unknown bank."""
    if not Bank.objects.exists():
        return row
    code, name, acct = _clean(row.get("Bank Code 1")), _clean(row.get("Bank Name")), _clean(row.get("Account Number"))
    if not (code or name or acct):
        return row
    n = services.account_length()
    if acct.isdigit() and n - 2 <= len(acct) < n:
        acct = acct.zfill(n)  # Excel drops the leading 0 of a number cell
        row["Account Number"] = acct
    bank = None
    if code:
        candidates = [code] + ([code.zfill(3)] if code.isdigit() and len(code) < 3 else [])
        bank = next((b for b in (Bank.objects.filter(code=c).first() for c in candidates) if b), None)
        if bank is None:
            row["__bank_problem"] = f"Bank code '{code}' is not in the bank list."
    elif name:
        bank = Bank.objects.filter(name__iexact=name).first()
        if bank is None:  # tolerate case, spacing, punctuation and "&" / "and" differences
            same = [b for b in Bank.objects.all() if services._norm(b.name) == services._norm(name)]
            bank = same[0] if len(same) == 1 else None
        if bank is None:
            row["__bank_problem"] = f"Bank '{name}' is not in the bank list; use its Bank Code 1."
    if bank is not None:
        row["Bank Name"], row["Bank Code 1"], row["__bank_id"] = bank.name, bank.code, bank.pk
    if (bank is not None or acct) and not _clean(row.get("Bank Branch")):
        row["Bank Branch"] = getattr(settings, "BANK_DEFAULT_BRANCH", "Lagos")
    return row


def bank_problem(row, seen):
    """Message for a row whose bank columns cannot be saved, else ''. `seen`: account numbers already in the file."""
    if row.get("__bank_problem"):
        return row["__bank_problem"]
    acct = _clean(row.get("Account Number"))
    if not acct:
        return ""
    if not Bank.objects.exists():
        return ""
    n = services.account_length()
    if not acct.isdigit() or len(acct) != n:
        return f"Account number must be {n} digits."
    if acct in seen:
        return "This account number is used twice in the file."
    seen.add(acct)
    mine = (
        EmployeeBankDetails.objects.entire().filter(account_number=acct)
        .exclude(employee_id__badge_id=_clean(row.get("Badge ID")))
        .exists()
    )
    return "Another employee already has this account number." if mine else ""


def after_create(rows):
    """Link new records to the bank list and ask Paystack for the account names. Never raises."""
    out = {"bank_verified": 0, "bank_unchecked": 0, "bank_warnings": []}
    try:
        wanted = [r for r in rows if r.get("__bank_id") and r.get("Account Number")]
        if not wanted:
            return out
        live, checks = paystack.is_configured(), 0
        for r in wanted:
            d = EmployeeBankDetails.objects.entire().filter(employee_id__badge_id=r["Badge ID"]).first()
            if d is None:
                continue
            EmployeeBankLink.objects.update_or_create(details=d, defaults={"bank_id": r["__bank_id"]})
            if not live or checks >= MAX_CHECKS_PER_IMPORT:
                out["bank_unchecked"] += 1
                continue
            checks += 1
            res, match = paystack.verify_details(d)
            if res.status == "verified":
                out["bank_verified"] += 1
                if match is False:
                    out["bank_warnings"].append(f"{r['Badge ID']}: account belongs to {res.account_name}")
            else:
                out["bank_unchecked"] += 1
                out["bank_warnings"].append(f"{r['Badge ID']}: {res.message or 'account not found'}")
    except Exception:  # bank checks must never undo an import
        import logging

        logging.getLogger(__name__).exception("bank check after import failed")
    return out
