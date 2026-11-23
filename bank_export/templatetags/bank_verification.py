from django import template
from django.template.loader import render_to_string

from bank_export import paystack

register = template.Library()


@register.simple_tag
def bank_verification(details):
    """Paystack check row for the employee record's bank card. Empty when there is no account to check."""
    if details is None or not details.account_number:
        return ""
    return render_to_string("bank_export/_check_row.html", {"v": paystack.current_status(details)})
