from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from payroll.sidebar import SUBMENUS


def access(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("weekly_payroll.view_payrollrun")


SUBMENUS.append(
    {
        "menu": _("Weekly Payroll"),
        "redirect": reverse_lazy("wp-runs"),
        "accessibility": "weekly_payroll.sidebar.access",
        "match_prefixes": ["/weekly-payroll/"],
    }
)
