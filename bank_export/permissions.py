from django.contrib.auth.models import Group, Permission

P = "bank_export."
ROLE_PERMISSIONS = {
    "HR Officer": [P + "view_bank", P + "view_employeebanklink", P + "view_bank_accounts", P + "edit_bank_accounts"],
    "HR/Payroll Approver": [P + "view_bank", P + "view_exportbatch", P + "supersede_export"],
    "Management": [P + "view_exportbatch"],
    "Accountant": [
        P + "view_bank", P + "add_bank", P + "change_bank", P + "view_exportbatch",
        P + "generate_export", P + "mark_export_paid", P + "view_bank_accounts",
    ],
}


def sync_groups(sender=None, **kwargs):
    for name, dotted in ROLE_PERMISSIONS.items():
        group, _c = Group.objects.get_or_create(name=name)
        for entry in dotted:
            app_label, codename = entry.split(".", 1)
            perm = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if perm:
                group.permissions.add(perm)
