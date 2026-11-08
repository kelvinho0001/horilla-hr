from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from attendance.sidebar import SUBMENUS


def biometric_integration_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("biometric_integration.view_importbatch")


SUBMENUS.append(
    {
        "menu": _("Biometric Import"),
        "redirect": reverse_lazy("bio-int-dashboard"),
        "accessibility": "biometric_integration.sidebar.biometric_integration_accessibility",
        "match_prefixes": ["/biometric-integration/"],
    }
)
