"""Check an employee's bank account with Paystack whenever it is saved from a form (never during bulk imports)."""

import logging

from django.db import transaction

from . import paystack

log = logging.getLogger(__name__)


def verify_after_save(sender, instance, created=False, raw=False, **kwargs):
    if raw or not (instance.account_number and instance.any_other_code1):
        return

    def run():
        try:
            from employee.models import EmployeeBankDetails

            d = EmployeeBankDetails.objects.entire().filter(pk=instance.pk).select_related("employee_id").first()
            if d is None or not paystack.is_configured() or paystack.current_status(d):
                return
            paystack.verify_details(d)
        except Exception:  # a failed lookup must never break saving the form
            log.exception("Paystack check after save failed")

    transaction.on_commit(run)
