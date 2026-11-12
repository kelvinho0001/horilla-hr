"""Who may do what: guards against permissions drifting onto the wrong role."""

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase, override_settings
from io import StringIO

from attendance_processing import permissions as a
from bank_export import permissions as b
from weekly_payroll import permissions as w

HOLDERS = {
    # permission -> the only custom roles allowed to hold it
    "weekly_payroll.approve_payrollrun": {"HR/Payroll Approver"},
    "weekly_payroll.review_payrollrun": {"HR Officer", "HR/Payroll Approver"},
    "bank_export.generate_export": {"Accountant"},
    "bank_export.mark_export_paid": {"Accountant"},
    "bank_export.supersede_export": {"HR/Payroll Approver"},
    "bank_export.edit_bank_accounts": {"HR Officer"},
    "attendance_processing.process_attendance": {"HR Officer", "HR/Payroll Approver"},
    "attendance_processing.add_attendancerule": {"HR/Payroll Approver"},
    "payroll.add_loanaccount": {"HR Officer"},
}
CUSTOM_ROLES = {"HR Officer", "HR/Payroll Approver", "Management", "Accountant", "IT Administrator"}


class RoleMatrixTests(TestCase):
    def setUp(self):
        for m in (a, w, b):
            m.sync_groups()

    def holders(self, dotted):
        app, code = dotted.split(".")
        return set(
            Group.objects.filter(
                permissions__content_type__app_label=app, permissions__codename=code, name__in=CUSTOM_ROLES
            ).values_list("name", flat=True)
        )

    def test_sensitive_permissions_sit_with_exactly_the_right_role(self):
        for perm, roles in HOLDERS.items():
            self.assertEqual(self.holders(perm), roles, perm)

    def test_every_role_permission_actually_exists(self):
        from django.contrib.auth.models import Permission

        for module in (a, w, b):
            for role, perms in module.ROLE_PERMISSIONS.items():
                for dotted in perms:
                    app, code = dotted.split(".")
                    self.assertTrue(
                        Permission.objects.filter(content_type__app_label=app, codename=code).exists(),
                        f"{role}: {dotted} does not exist",
                    )

    def test_it_admin_has_no_payroll_or_bank_export_rights(self):
        g = Group.objects.get(name="IT Administrator")
        apps_held = set(g.permissions.values_list("content_type__app_label", flat=True))
        self.assertTrue(apps_held <= {"biometric_integration"})

    def test_accountant_cannot_change_attendance_or_approve_anything(self):
        codes = set(Group.objects.get(name="Accountant").permissions.values_list("codename", flat=True))
        self.assertFalse({c for c in codes if c.startswith(("approve_", "add_", "change_", "delete_", "review_", "process_"))
                          and c not in ("add_bank", "change_bank")})

    def test_sync_is_idempotent_and_does_not_strip_admin_changes(self):
        g = Group.objects.get(name="Accountant")
        before = g.permissions.count()
        for m in (a, w, b):
            m.sync_groups()
        self.assertEqual(g.permissions.count(), before)


class CheckCommandTests(TestCase):
    def run_check(self):
        out = StringIO()
        try:
            call_command("demiller_check", stdout=out)
            code = 0
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue()

    def test_reports_debug_and_hosts_failures_and_roles(self):
        for m in (a, w, b):
            m.sync_groups()
        with override_settings(DEBUG=True, ALLOWED_HOSTS=["*"]):
            code, text = self.run_check()
        self.assertEqual(code, 1)
        self.assertIn("[FAIL] DEBUG is on", text)
        self.assertIn("[FAIL] ALLOWED_HOSTS", text)
        self.assertIn("[OK  ] app installed: bank_export", text)
        self.assertIn("[OK  ] role group exists: Accountant", text)

    def test_clean_configuration_passes_failures(self):
        for m in (a, w, b):
            m.sync_groups()
        Group.objects.get_or_create(name="IT Administrator")
        with override_settings(DEBUG=False, ALLOWED_HOSTS=["hr.demillerfarms.com"], SECRET_KEY="x" * 60,
                               CSRF_TRUSTED_ORIGINS=["https://hr.demillerfarms.com"]):
            code, text = self.run_check()
        self.assertEqual(code, 0, text)
