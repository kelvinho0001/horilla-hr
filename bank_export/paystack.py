"""
Paystack account-name lookup (GET /bank/resolve). Asks Paystack who owns an account number at a bank; no money moves.

The secret key comes from Settings > Payroll > Paystack (PaystackSettings), or the PAYSTACK_SECRET_KEY environment
variable if nothing is saved there. Paystack bank codes are the CBN codes in the bank list ('044', '058' ...).
"""

import logging
import os
import re
from dataclasses import dataclass

import requests
from django.conf import settings
from django.utils import timezone

from .models import AccountVerification, PaystackSettings

log = logging.getLogger(__name__)
URL = "https://api.paystack.co/bank/resolve"
TIMEOUT = 10


@dataclass
class Result:
    status: str  # verified | failed | error
    account_name: str = ""
    message: str = ""


def secret_key():
    """Key saved in Settings > Payroll > Paystack, else the PAYSTACK_SECRET_KEY environment variable."""
    row = PaystackSettings.objects.first()
    if row is not None and not row.is_enabled:
        return ""  # switched off on the settings page
    if row is not None and (row.secret_key or "").strip():
        return row.secret_key.strip()
    return (os.environ.get("PAYSTACK_SECRET_KEY") or getattr(settings, "PAYSTACK_SECRET_KEY", "") or "").strip()


def is_configured():
    return bool(secret_key())


def resolve(account_number, bank_code, key=None):
    key = key or secret_key()
    if not key:
        return Result("error", message="Paystack is not set up (Settings > Payroll > Paystack).")
    try:
        r = requests.get(
            URL, params={"account_number": account_number, "bank_code": bank_code},
            headers={"Authorization": f"Bearer {key}"}, timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        log.warning("Paystack unreachable: %s", e)
        return Result("error", message="Could not reach Paystack. Try again.")
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 200 and body.get("status"):
        return Result("verified", account_name=(body.get("data") or {}).get("account_name", "").strip())
    msg = str(body.get("message") or f"Paystack answered {r.status_code}")[:280]
    if r.status_code in (400, 404, 422):
        return Result("failed", message=msg)  # wrong number / bank
    return Result("error", message=msg)  # bad key, rate limit, outage: not the employee's fault


def _tokens(s):
    return {t for t in re.split(r"[^a-z]+", (s or "").lower()) if len(t) > 1}


def names_match(account_name, employee):
    """True when at least two name parts agree (order and middle names ignored), or all parts of a short name."""
    have = _tokens(account_name)
    want = _tokens(f"{employee.employee_first_name} {employee.employee_last_name or ''}")
    if not have or not want:
        return None
    common = len(have & want)
    return common >= min(2, len(want))


def verify_details(details, save=True):
    """Check one EmployeeBankDetails row and store the answer."""
    acct = (details.account_number or "").strip()
    code = (details.any_other_code1 or "").strip()
    if not acct or not code:
        res = Result("failed", message="Account number or bank code is missing.")
    else:
        res = resolve(acct, code)
    emp = details.employee_id
    match = names_match(res.account_name, emp) if res.status == "verified" and emp else None
    if not save:
        return res, match
    AccountVerification.objects.update_or_create(
        details=details,
        defaults={
            "status": res.status, "account_number": acct, "bank_code": code, "account_name": res.account_name,
            "name_matches": match, "message": res.message, "checked_at": timezone.now(),
        },
    )
    return res, match


def current_status(details):
    """Verification that still applies to the record as it is now, else None."""
    v = getattr(details, "verification", None) if details is not None else None
    if v is None:
        return None
    if (v.account_number, v.bank_code) != ((details.account_number or "").strip(), (details.any_other_code1 or "").strip()):
        return None  # account or bank changed since the check
    return v
