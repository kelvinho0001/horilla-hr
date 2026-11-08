"""Roles for payroll, seeded additively like attendance_processing (same Horilla pattern)."""

from django.contrib.auth.models import Group, Permission

P = "weekly_payroll."
ROLE_PERMISSIONS = {
    "HR Officer": [
        P + "view_payrollrun", P + "add_payrollrun", P + "change_payrollrun", P + "review_payrollrun",
        P + "view_workerpayprofile", P + "add_workerpayprofile",
        P + "view_fine", P + "add_fine", P + "view_finetype",
        P + "view_loanagreement", P + "add_loanagreement",
    ],
    "HR/Payroll Approver": [
        P + "view_payrollrun", P + "add_payrollrun", P + "change_payrollrun",
        P + "review_payrollrun", P + "approve_payrollrun",
        P + "view_workerpayprofile", P + "view_fine", P + "view_finetype", P + "view_loanagreement",
    ],
    "Management": [
        P + "view_payrollrun",
        P + "view_workerpayprofile", P + "approve_workerpayprofile",
        P + "view_fine", P + "approve_fine", P + "waive_fine",
        P + "view_loanagreement", P + "approve_loanagreement",
        "attendance_processing.view_dailyattendance",
    ],
    "Accountant": [P + "view_payrollrun"],
}


def sync_groups(sender=None, **kwargs):
    for name, dotted in ROLE_PERMISSIONS.items():
        group, _c = Group.objects.get_or_create(name=name)
        for entry in dotted:
            app_label, codename = entry.split(".", 1)
            perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if perm:
                group.permissions.add(perm)
