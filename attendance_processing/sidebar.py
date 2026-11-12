from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from attendance.sidebar import SUBMENUS


def overview_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("attendance_processing.process_attendance") or request.user.has_perm(
        "attendance_processing.view_attendancerule"
    )


SUBMENUS.append(
    {
        "menu": _("Biometric Attendance"),
        "redirect": reverse_lazy("att-proc-review"),
        "accessibility": "attendance_processing.sidebar.overview_accessibility",
        "match_prefixes": ["/attendance-processing/"],
    }
)
