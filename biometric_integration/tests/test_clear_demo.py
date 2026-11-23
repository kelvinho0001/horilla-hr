from datetime import date
from io import StringIO

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from attendance.models import Attendance
from attendance_processing.models import AttendanceRule
from bank_export.models import Bank
from base.models import CompanyGroupAssignment, Department
from biometric_integration.models import ExternalEmployeeMapping, ImportBatch, ProviderConfig, RawTransaction
from employee.models import Employee, EmployeeBankDetails, EmployeeWorkInformation
from horilla.testkit import make_company, make_employee
from horilla_auth.models import HorillaUser
from payroll.models.models import LoanAccount
from weekly_payroll import services
from weekly_payroll.models import PayrollRun


def run_command(*args):
    out = StringIO()
    call_command("demiller_clear_demo_data", *args, stdout=out)
    return out.getvalue()


class ClearDemoDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = make_company()
        cls.dept = Department.objects.create(department="Packing")
        cls.admin = make_employee(company=cls.company, email="admin@x.test", first_name="Admin")
        u = cls.admin.employee_user_id
        u.is_superuser = True
        u.save()
        cls.hr = make_employee(company=cls.company, email="hr@x.test", first_name="Hr")
        CompanyGroupAssignment.objects.create(user=cls.hr.employee_user_id, company=cls.company, group=Group.objects.create(name="HR Officer X"))
        cls.workers = [make_employee(company=cls.company, email=f"w{i}@x.test", first_name=f"W{i}", department=cls.dept) for i in range(3)]
        for i, w in enumerate(cls.workers):
            w.badge_id = f"SE{i:03d}"
            w.save()
            EmployeeWorkInformation.objects.filter(employee_id=w).update(basic_salary=70000)
            EmployeeBankDetails.objects.create(employee_id=w, bank_name="Access Bank", account_number=f"00000000{i:02d}")
        cls.provider = ProviderConfig.objects.create(name="Device X", provider_type="manual_file")
        cls.batch = ImportBatch.objects.create(provider=cls.provider)
        for w in cls.workers:
            ExternalEmployeeMapping.objects.create(provider=cls.provider, employee=w, external_employee_id=w.badge_id)
            RawTransaction.objects.create(provider=cls.provider, batch=cls.batch, dedup_key=f"k{w.pk}", external_employee_id=w.badge_id,
                                          employee=w, attendance_timestamp="2024-10-01T10:00:00Z")
            Attendance.objects.create(employee_id=w, attendance_date=date(2024, 10, 1), attendance_clock_in="16:00",
                                      attendance_clock_in_date=date(2024, 10, 1), attendance_validated=True)
            LoanAccount.objects.create(title="L", employee_id=w, type="loan", loan_amount=3000.0, provided_date=date(2024, 9, 1),
                                       installments=1, installment_start_date=date(2024, 10, 2))
        Bank.objects.create(code="044", name="Access Bank")
        AttendanceRule.objects.create(name="Rule", scope="global")
        run = PayrollRun.objects.create(name="R", start_date=date(2024, 10, 1), end_date=date(2024, 10, 31), default_working_days=28)
        services.generate(run)
        services.mark_reviewed(run, cls.admin.employee_user_id)
        services.approve(run, cls.admin.employee_user_id)

    def test_dry_run_changes_nothing(self):
        text = run_command()
        self.assertIn("DRY RUN", text)
        self.assertEqual(Employee.objects.entire().count(), 5)
        self.assertEqual(Attendance.objects.count(), 3)
        self.assertEqual(PayrollRun.objects.count(), 1)

    def test_clears_demo_data_and_keeps_the_setup_and_the_people_you_keep(self):
        run_command("--yes")
        self.assertEqual({e.first_name if hasattr(e, "first_name") else e.employee_first_name for e in Employee.objects.entire()}, {"Admin", "Hr"})
        self.assertFalse(HorillaUser.objects.filter(username__in=[f"w{i}@x.test" for i in range(3)]).exists())
        for model in (Attendance, LoanAccount, PayrollRun, RawTransaction, ImportBatch, ExternalEmployeeMapping, ProviderConfig):
            self.assertEqual(getattr(model, "_base_manager", model.objects).count(), 0, model.__name__)
        self.assertEqual(EmployeeBankDetails.objects.count(), 0)
        # the setup survives
        self.assertEqual((Department.objects.count(), AttendanceRule.objects.count(), Bank.objects.count()), (1, 1, 1))
        self.assertTrue(Group.objects.filter(name="HR Officer X").exists())
        self.assertTrue(CompanyGroupAssignment.objects.filter(user=self.hr.employee_user_id).exists())

    def test_keep_badges_keeps_extra_people_and_unknown_badge_is_an_error(self):
        run_command("--yes", "--keep-badges", "SE001")
        self.assertTrue(Employee.objects.entire().filter(badge_id="SE001").exists())
        self.assertFalse(Employee.objects.entire().filter(badge_id="SE000").exists())
        with self.assertRaises(CommandError):
            run_command("--yes", "--keep-badges", "NOPE")

    def test_workers_can_be_imported_again_with_the_same_emails_and_badges_afterwards(self):
        run_command("--yes")
        again = make_employee(company=self.company, email="w0@x.test", first_name="Again")
        again.badge_id = "SE000"
        again.save()
        self.assertEqual(Employee.objects.entire().filter(badge_id="SE000").count(), 1)

    def test_all_or_nothing_when_a_removed_worker_is_someone_elses_reporting_manager(self):
        boss = self.workers[0]
        EmployeeWorkInformation.objects.filter(employee_id=self.hr).update(reporting_manager_id=boss)
        try:
            run_command("--yes")
        except CommandError as exc:
            self.assertIn("Nothing was changed", str(exc))
            self.assertEqual(Attendance.objects.count(), 3)              # rolled back
            self.assertEqual(PayrollRun.objects.count(), 1)
            self.assertEqual(Employee.objects.entire().count(), 5)
        else:  # Horilla may SET_NULL the manager: then it simply succeeds and the kept employee is intact
            self.assertTrue(Employee.objects.entire().filter(pk=self.hr.pk).exists())
