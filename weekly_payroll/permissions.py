"""Roles for payroll, seeded additively (same Horilla pattern as the other apps)."""

from django.contrib.auth.models import Group, Permission

P = "weekly_payroll."
ROLE_PERMISSIONS = {
    "HR Officer": [
        P + "view_payrollrun", P + "add_payrollrun", P + "change_payrollrun", P + "review_payrollrun",
        # loans, salary advances and fines live on Horilla's own Payroll > Loans page
        "payroll.view_loanaccount", "payroll.add_loanaccount", "payroll.change_loanaccount",
    ],
    "HR/Payroll Approver": [
        P + "view_payrollrun", P + "add_payrollrun", P + "change_payrollrun",
        P + "review_payrollrun", P + "approve_payrollrun",
        "payroll.view_loanaccount",
    ],
    "Management": [P + "view_payrollrun", "payroll.view_loanaccount"],
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
