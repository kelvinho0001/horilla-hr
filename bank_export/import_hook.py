"""
Bank details on Horilla's own employee import (Employees > Action > Import).

Horilla's importer ignores bank columns. employee/views.py::work_info_import calls `apply(success_list)` after the
employees are created. For each row that has a Bank Code 1 and an Account Number this:
  1. finds the bank in the bank list by code and takes the bank NAME from it,
  2. saves bank name, code, account number and branch (default Lagos) on the employee's bank details,
  3. asks Paystack who owns the account and records the answer (shown on the employee record).
Employees are never rejected because of their bank columns; problems are listed in the import result instead.
"""

from django.conf import settings

from employee.models import Employee

from . import paystack, services
from .models import Bank

CODE_COLUMNS = ("Bank Code 1", "Bank Code #1", "Bank Code")
ACCOUNT_COLUMNS = ("Account Number", "Bank Account Number")
NAME_COLUMNS = ("Bank Name",)
BRANCH_COLUMNS = ("Bank Branch",)
MAX_CHECKS_PER_IMPORT = 40  # keeps the upload quick; the rest are checked from Bank Payments > Accounts


def _cell(row, names):
    for n in names:
        v = row.get(n)
        if v is not None and str(v).strip() not in ("", "nan", "None"):
            if isinstance(v, float) and v.is_integer():
                v = int(v)  # Excel numbers: 44.0 -> 44
            return str(v).strip()
    return ""


def _find_bank(code, name):
    if code:
        for candidate in dict.fromkeys([code, code.zfill(3) if code.isdigit() else code]):  # Excel drops the 0 of 044
            b = Bank.objects.filter(code=candidate).first()
            if b:
                return b
        return None
    if name:
        return Bank.objects.filter(name__iexact=name).first()
    return None


def apply(success_list):
    """Returns {'saved', 'verified', 'problems': [(badge, message)], 'unchecked'}; or None if no row has bank data."""
    report = {"saved": 0, "verified": 0, "unchecked": 0, "problems": [], "name_warnings": []}
    seen = False
    checks = 0
    live = paystack.is_configured()
    for row in success_list:
        code, acct = _cell(row, CODE_COLUMNS), _cell(row, ACCOUNT_COLUMNS)
        bank_name = _cell(row, NAME_COLUMNS)
        if not (code or acct):
            continue
        seen = True
        badge = str(row.get("Badge ID", "")).strip()
        emp = Employee.objects.entire().filter(badge_id=badge).first()
        if emp is None:
            continue
        n = services.account_length()
        if acct.isdigit() and n - 2 <= len(acct) < n:
            acct = acct.zfill(n)  # Excel drops the leading 0 of a number cell; anything shorter is a real mistake
        if not (code or bank_name) or not acct:
            report["problems"].append((badge, "Needs both a Bank Code 1 and an Account Number."))
            continue
        bank = _find_bank(code, bank_name)
        if bank is None:
            report["problems"].append((badge, f"Bank code '{code or bank_name}' is not in the bank list."))
            continue
        try:
            details = services.save_worker_bank(emp, bank, acct)
        except services.ExportError as exc:
            report["problems"].append((badge, str(exc)))
            continue
        branch = _cell(row, BRANCH_COLUMNS) or getattr(settings, "BANK_DEFAULT_BRANCH", "Lagos")
        if not details.branch:
            details.branch = branch
            details.save(update_fields=["branch"])
        report["saved"] += 1
        if not live:
            report["unchecked"] += 1
            continue
        if checks >= MAX_CHECKS_PER_IMPORT:
            report["unchecked"] += 1
            continue
        checks += 1
        res, match = paystack.verify_details(details)
        if res.status == "verified":
            report["verified"] += 1
            if match is False:
                report["name_warnings"].append((badge, f"Paystack says the account belongs to {res.account_name}."))
        elif res.status == "failed":
            report["problems"].append((badge, f"Paystack could not find this account: {res.message}"))
        else:
            report["unchecked"] += 1
            report["problems"].append((badge, res.message))
    return report if seen else None
