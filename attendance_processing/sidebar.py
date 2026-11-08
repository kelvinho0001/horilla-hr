from django.urls import reverse_lazy
from django.utils.translation import gettext_lazy as _

from attendance.sidebar import SUBMENUS


def review_accessibility(request, submenu, user_perms, *args, **kwargs):
    return request.user.has_perm("attendance_processing.view_dailyattendance")


SUBMENUS.append(
    {
        "menu": _("Weekly Attendance Review"),
        "redirect": reverse_lazy("att-proc-review"),
        "accessibility": "attendance_processing.sidebar.review_accessibility",
        "match_prefixes": ["/attendance-processing/"],
    }
)
