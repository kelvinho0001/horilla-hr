from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from payroll.sidebar import SUBMENUS


def access(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("bank_export.view_exportbatch")


SUBMENUS.append(
    {
        "menu": _("Bank Export"),
        "redirect": reverse_lazy("bx-dashboard"),
        "accessibility": "bank_export.sidebar.access",
        "match_prefixes": ["/bank-payments/"],
    }
)


def paystack_settings_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("bank_export.change_paystacksettings")


def _add_to_payroll_settings():
    """Settings > Payroll > Paystack, below Encashment Settings."""
    from django.urls import reverse_lazy
    from payroll.sidebar import PayrollSettings

    if any(i.get("label") == _("Paystack") for i in PayrollSettings.items):
        return
    PayrollSettings.items.append(
        {
            "label": _("Paystack"),
            "url": reverse_lazy("bx-paystack"),
            "accessibility": paystack_settings_accessibility,
            "search_entries": [
                {"text": _("Paystack"), "description": _("Secret key used to check employee bank account names")},
                {"text": _("Account Name"), "description": _("Account name lookup for bank accounts")},
            ],
        }
    )


_add_to_payroll_settings()
