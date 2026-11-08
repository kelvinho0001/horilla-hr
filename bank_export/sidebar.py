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
        "match_prefixes": ["/bank-export/"],
    }
)
