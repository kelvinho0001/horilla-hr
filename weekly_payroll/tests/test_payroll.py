from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from attendance.models import Attendance
from attendance_processing import permissions as att_perms
from base.models import CompanyGroupAssignment, Department, EmployeeType
from biometric_integration.models import ExternalEmployeeMapping, ProviderConfig
from employee.models import EmployeeWorkInformation
from horilla.testkit import make_company, make_employee
from payroll.models.models import LoanAccount
from weekly_payroll import permissions, services
from weekly_payroll.export_xlsx import build_workbook
from weekly_payroll.models import LoanDeduction, PayrollLine, PayrollRun
from weekly_payroll.money import kobo

D = Decimal
_N = [0]


def next_n():
    _N[0] += 1
    return _N[0]


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = make_company()
        cls.provider = ProviderConfig.objects.create(name="Dev", provider_type="manual_file")

    def user(self):
        return make_employee(company=self.company, email=f"u{next_n()}@x.test").employee_user_id

    def worker(self, basic="70000", badge=None, department=None, mapped=True):
        """A worker whose basic salary is on the employee record (Work information)."""
        n = next_n()
        e = make_employee(company=self.company, email=f"w{n}@x.test", first_name=f"F{n}", last_name=f"L{n}", department=department)
        e.badge_id = badge or f"SE{n:03d}"
        e.save()
        EmployeeWorkInformation.objects.filter(employee_id=e).update(basic_salary=int(D(basic)) if basic is not None else None)
        if mapped:
            ExternalEmployeeMapping.objects.create(provider=self.provider, employee=e, external_employee_id=e.badge_id)
        return e

    def present(self, e, days, validated=True, start=date(2024, 10, 1)):
        """Horilla attendance records, one per day (what the Attendances page holds)."""
        for i in range(days):
            Attendance.objects.create(
                employee_id=e, attendance_date=start + timedelta(i), attendance_clock_in_date=start + timedelta(i),
                attendance_clock_in="16:00", attendance_validated=validated)

    def mkrun(self, start=date(2024, 10, 1), end=date(2024, 10, 31), wd=28, name=None, **kw):
        r = PayrollRun.objects.create(name=name or f"Run{next_n()}", start_date=start, end_date=end, default_working_days=wd, **kw)
        return services.generate(r)

    def line(self, run, e):
        return PayrollLine.objects.get(run=run, employee=e)

    def loan(self, e, amount="60000", installments=3, start=date(2024, 10, 25), type="loan", settled=False):
        return LoanAccount.objects.create(
            title=type.title(), employee_id=e, type=type, loan_amount=float(amount), provided_date=date(2024, 9, 1),
            installments=installments, installment_start_date=start, settled=settled)

    def approve_run(self, run):
        services.mark_reviewed(run, self.user())
        return services.approve(run, self.user())


class FormulaTests(TestCase):
    def test_direction_matches_sheet_row1(self):
        self.assertEqual(services.amount_earned(15, 28, D("70000"), D("5000"), D("20000")), D("12500.00"))

    def test_full_attendance_pays_full_basic(self):
        self.assertEqual(services.amount_earned(24, 24, D("70000"), 0, 0), D("70000.00"))

    def test_rounds_to_the_kobo_half_up(self):
        self.assertEqual(kobo(D("0.005")), D("0.01"))
        self.assertEqual(services.amount_earned(1, 3, D("100"), 0, 0), D("33.33"))
        self.assertEqual(services.amount_earned(2, 3, D("100"), 0, 0), D("66.67"))

    def test_zero_working_days_rejected(self):
        with self.assertRaises(services.PayrollError):
            services.amount_earned(5, 0, D("1"), 0, 0)


class CalculationTests(Base):
    def test_basic_salary_comes_from_the_employee_record(self):
        a, b = self.worker("70000"), self.worker("55000")
        for e in (a, b):
            self.present(e, 14)
        r = self.mkrun()
        self.assertEqual(self.line(r, a).basic_salary, D("70000"))
        self.assertEqual(self.line(r, a).amount_earned, D("35000.00"))
        self.assertEqual(self.line(r, b).amount_earned, D("27500.00"))
        self.assertEqual(r.total_net, D("62500.00"))
        # changing the employee record changes the next calculation
        EmployeeWorkInformation.objects.filter(employee_id=a).update(basic_salary=84000)
        self.assertEqual(self.line(services.generate(r), a).amount_earned, D("42000.00"))

    def test_only_validated_attendance_counts_and_waiting_days_are_flagged(self):
        e = self.worker()
        self.present(e, 10, validated=True)
        self.present(e, 5, validated=False, start=date(2024, 10, 20))
        l = self.line(self.mkrun(), e)
        self.assertEqual(l.total_days, 10)
        self.assertTrue(any("waiting for validation" in f for f in l.flags))

    def test_attendance_added_by_hand_in_horilla_counts_once_validated(self):
        e = self.worker()
        Attendance.objects.create(employee_id=e, attendance_date=date(2024, 10, 3), attendance_clock_in="09:00",
                                  attendance_clock_in_date=date(2024, 10, 3), attendance_validated=True)
        self.assertEqual(self.line(self.mkrun(), e).total_days, 1)

    def test_worker_with_validated_attendance_but_no_mapping_is_included(self):
        e = self.worker(mapped=False)
        self.present(e, 3)
        self.assertEqual(self.line(self.mkrun(), e).total_days, 3)

    def test_mapped_worker_without_attendance_shows_zero_days(self):
        e = self.worker()
        l = self.line(self.mkrun(), e)
        self.assertEqual((l.total_days, l.amount_earned), (0, D("0.00")))
        self.assertTrue(any("No validated attendance" in f for f in l.flags))

    def test_missing_basic_salary_flagged_and_blocks_review(self):
        e = self.worker(basic=None)
        self.present(e, 5)
        r = self.mkrun()
        self.assertTrue(any("No basic salary" in f for f in self.line(r, e).flags))
        with self.assertRaises(services.PayrollError):
            services.mark_reviewed(r, self.user())

    def test_department_and_worker_category_filters(self):
        d1, d2 = Department.objects.create(department="A"), Department.objects.create(department="B")
        a, b = self.worker(department=d1), self.worker(department=d2)
        r = self.mkrun(department=d1)
        self.assertEqual(r.employee_count, 1)
        self.assertEqual(PayrollLine.objects.get(run=r).employee, a)
        t = EmployeeType.objects.create(employee_type="Casual")
        EmployeeWorkInformation.objects.filter(employee_id=b).update(employee_type_id=t)
        r2 = self.mkrun(employee_type=t)
        self.assertEqual(PayrollLine.objects.get(run=r2).employee, b)

    def test_weekly_columns_any_length(self):
        e = self.worker()
        self.present(e, 17)
        l = self.line(self.mkrun(start=date(2024, 10, 1), end=date(2024, 10, 17), wd=17), e)
        self.assertEqual(l.week_days, [7, 7, 3])

    def test_working_days_override_survives_recalculation(self):
        e = self.worker()
        self.present(e, 10)
        r = self.mkrun()
        services.set_working_days(self.line(r, e), 25)
        services.generate(r)
        l = self.line(r, e)
        self.assertEqual((l.working_days, l.amount_earned), (25, D("28000.00")))

    def test_one_line_per_worker_and_totals_reconcile(self):
        for b in ("70000", "55000", "31250"):
            self.present(self.worker(b), 9)
        r = self.mkrun()
        services.generate(r)
        self.assertEqual(r.lines.count(), 3)
        self.assertEqual(r.total_net, sum(l.amount_earned for l in r.lines.all()))


class LoanFineAdvanceTests(Base):
    def test_schedule_follows_horillas_monthly_installments_and_sums_to_the_amount(self):
        e = self.worker()
        loan = self.loan(e, "100", 3, date(2024, 1, 31))
        sched = services.installment_schedule(loan)
        self.assertEqual([(k, d.isoformat(), str(a)) for k, d, a in sched],
                         [(1, "2024-01-31", "33.33"), (2, "2024-02-29", "33.33"), (3, "2024-03-31", "33.34")])
        self.assertEqual(sum(a for _k, _d, a in sched), D("100.00"))

    def test_monthly_installment_is_not_taken_in_earlier_weekly_runs(self):
        e = self.worker()
        self.loan(e)  # 20,000 x 3 from 25 Oct
        for s, en in [(1, 7), (8, 14), (15, 21)]:
            r = self.mkrun(start=date(2024, 10, s), end=date(2024, 10, en), wd=6)
            self.assertEqual(self.line(r, e).iou, D("0"))
        r = self.mkrun(start=date(2024, 10, 22), end=date(2024, 10, 28), wd=6)
        self.assertEqual(self.line(r, e).iou, D("20000.00"))

    def test_taken_once_even_across_overlapping_runs_and_drafts_write_nothing(self):
        e = self.worker()
        self.present(e, 28)
        loan = self.loan(e)
        r1 = self.mkrun(name="Oct")
        self.assertEqual(self.line(r1, e).iou, D("20000.00"))
        services.generate(r1)
        self.assertEqual(self.line(r1, e).iou, D("20000.00"))   # recalculation does not double
        self.assertEqual(LoanDeduction.objects.count(), 0)       # nothing written for a draft
        self.approve_run(r1)
        self.assertEqual(LoanDeduction.objects.filter(loan=loan).count(), 1)
        r2 = self.mkrun(start=date(2024, 10, 20), end=date(2024, 10, 31), name="Overlap")
        self.assertEqual(self.line(r2, e).iou, D("0"))
        r3 = self.mkrun(start=date(2024, 11, 1), end=date(2024, 11, 30), name="Nov")
        self.assertEqual(self.line(r3, e).iou, D("20000.00"))    # next instalment, due 25 Nov

    def test_two_drafts_race_second_approval_refused(self):
        e = self.worker()
        self.present(e, 28)
        self.loan(e)
        r1, r2 = self.mkrun(name="One"), self.mkrun(start=date(2024, 10, 2), end=date(2024, 10, 30), name="Two")
        services.mark_reviewed(r1, self.user())
        services.mark_reviewed(r2, self.user())
        services.approve(r1, self.user())
        with self.assertRaises(services.PayrollError):
            services.approve(r2, self.user())
        self.assertEqual(LoanDeduction.objects.count(), 1)

    def test_database_refuses_the_same_instalment_twice(self):
        e = self.worker()
        self.present(e, 5)
        loan = self.loan(e)
        r = self.mkrun()
        LoanDeduction.objects.create(loan=loan, installment_no=1, due_date=date(2024, 10, 25), amount=D("1"), run=r)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LoanDeduction.objects.create(loan=loan, installment_no=1, due_date=date(2024, 10, 25), amount=D("1"), run=r)

    def test_loan_and_salary_advance_go_to_iou_and_fine_goes_to_fines(self):
        e = self.worker()
        self.present(e, 28)
        self.loan(e, "20000", 1, date(2024, 10, 10), type="loan")
        self.loan(e, "5000", 1, date(2024, 10, 12), type="advanced_salary")
        self.loan(e, "3000", 1, date(2024, 10, 14), type="fine")
        l = self.line(self.mkrun(), e)
        self.assertEqual((l.iou, l.fines), (D("25000.00"), D("3000.00")))
        self.assertEqual({i["type"] for i in l.breakdown["loans"] + l.breakdown["fines"]}, {"Loan", "Salary Advance", "Penalty / Fine"})

    def test_fine_is_taken_in_full_by_the_first_approved_run_only(self):
        e = self.worker()
        self.present(e, 28)
        fine = self.loan(e, "5000", 1, date(2024, 10, 5), type="fine")
        r1 = self.mkrun(name="A")
        self.assertEqual(self.line(r1, e).fines, D("5000.00"))
        self.approve_run(r1)
        fine.refresh_from_db()
        self.assertTrue(fine.settled)
        r2 = self.mkrun(start=date(2024, 11, 1), end=date(2024, 11, 30), name="B")
        self.assertEqual(self.line(r2, e).fines, D("0"))

    def test_loan_settles_after_last_instalment_and_settled_loans_are_skipped(self):
        e = self.worker()
        loan = self.loan(e, "30000", 2, date(2024, 10, 25))
        r = self.mkrun(start=date(2024, 10, 1), end=date(2024, 12, 31), wd=60)
        self.assertEqual(self.line(r, e).iou, D("30000.00"))
        self.approve_run(r)
        loan.refresh_from_db()
        self.assertTrue(loan.settled)
        r2 = self.mkrun(start=date(2025, 1, 1), end=date(2025, 1, 31), name="Jan")
        self.assertEqual(self.line(r2, e).iou, D("0"))
        flagged = self.loan(e, "9000", 3, date(2024, 10, 1), settled=True)  # marked settled in Horilla
        self.assertEqual(self.line(self.mkrun(name="x"), e).iou, D("0") + self.line(r2, e).iou)

    def test_future_loan_not_due_yet(self):
        e = self.worker()
        self.loan(e, "9000", 3, date(2025, 3, 1))
        self.assertEqual(self.line(self.mkrun(), e).iou, D("0"))

    def test_negative_net_is_flagged_not_clamped(self):
        e = self.worker("10000")
        self.present(e, 2)
        self.loan(e, "9000", 1, date(2024, 10, 2), type="advanced_salary")
        l = self.line(self.mkrun(), e)
        self.assertLess(l.amount_earned, 0)
        self.assertIn("Net pay is negative", l.flags)

    def test_migrating_old_collected_instalments_would_still_block_recollection(self):
        e = self.worker()
        self.present(e, 28)
        loan = self.loan(e)
        r0 = self.mkrun(start=date(2024, 9, 1), end=date(2024, 9, 30), name="Sep")
        LoanDeduction.objects.create(loan=loan, installment_no=1, due_date=date(2024, 10, 25), amount=D("20000"), run=r0)
        self.assertEqual(self.line(self.mkrun(), e).iou, D("0"))


class LifecycleTests(Base):
    def setUp(self):
        self.e = self.worker()
        self.present(self.e, 20)
        self.r = self.mkrun()

    def test_must_be_reviewed_before_approval(self):
        with self.assertRaises(services.PayrollError):
            services.approve(self.r, self.user())

    def test_approved_run_is_frozen(self):
        self.approve_run(self.r)
        self.r.refresh_from_db()
        with self.assertRaises(services.PayrollError):
            services.generate(self.r)
        with self.assertRaises(services.PayrollError):
            services.set_working_days(self.line(self.r, self.e), 20)
        l = self.line(self.r, self.e)
        l.amount_earned = D("1")
        with self.assertRaises(ValidationError):
            l.save()

    def test_changing_working_days_sends_reviewed_back_to_draft(self):
        services.mark_reviewed(self.r, self.user())
        services.set_working_days(self.line(self.r, self.e), 26)
        self.r.refresh_from_db()
        self.assertEqual(self.r.status, "draft")

    def test_zero_working_days_blocks_review(self):
        PayrollLine.objects.filter(run=self.r).update(working_days=0)
        with self.assertRaises(services.PayrollError):
            services.mark_reviewed(self.r, self.user())

    def test_overlapping_approved_run_blocks(self):
        self.approve_run(self.r)
        r2 = self.mkrun(start=date(2024, 10, 20), end=date(2024, 11, 5), name="Overlap")
        services.mark_reviewed(r2, self.user())
        with self.assertRaises(services.PayrollError):
            services.approve(r2, self.user())

    def test_empty_run_explains_why(self):
        PayrollRun.objects.all().delete()
        r = self.mkrun(start=date(2020, 1, 1), end=date(2020, 1, 7), wd=6, department=Department.objects.create(department="Nobody"))
        self.assertIn("no workers", services.blockers(r)[0].lower())


class ExcelTests(Base):
    def test_workbook_matches_template_layout_and_formulas(self):
        e = self.worker("70000", badge="SE001")
        self.present(e, 17)
        r = self.mkrun(start=date(2024, 10, 1), end=date(2024, 10, 17), wd=17)
        wb = openpyxl.load_workbook(BytesIO(build_workbook(r)))
        ws = wb["PAYROLL"]
        self.assertEqual([c.value for c in ws[1]], ["EMP ID", "FIRST NAME", "LAST NAME", "DEPARTMENT", "WEEK 1", "WEEK 2", "WEEK 3",
                                                   "TOTAL DAY", "WORKING DAYS", "BASIC SALARY", "FINES", "IOU", "AMOUNT EARNED"])
        self.assertEqual([c.value for c in ws[2]][4:7], [7, 7, 3])
        self.assertEqual(ws["I2"].value, 17)
        self.assertIn("Table1", ws.tables)
        self.assertTrue(ws["M2"].value.startswith("=ROUND("))
        self.assertIn("NOT APPROVED", wb["RUN INFO"]["B3"].value)

    def test_loan_fine_and_advance_amounts_reach_the_sheet(self):
        e = self.worker("70000", badge="SE001")
        self.present(e, 28)
        self.loan(e, "20000", 1, date(2024, 10, 10), type="loan")
        self.loan(e, "4500", 1, date(2024, 10, 10), type="fine")
        ws = openpyxl.load_workbook(BytesIO(build_workbook(self.mkrun())))["PAYROLL"]
        header = [c.value for c in ws[1]]
        row = dict(zip(header, [c.value for c in ws[2]]))
        self.assertEqual((row["FINES"], row["IOU"]), (4500.0, 20000.0))


class SampleSheetTests(Base):
    """The four workers of Demiller's PAYROLL_TEMPLATE_1.xlsx, end to end from Horilla's own records."""

    SAMPLE = [
        ("SE001", [3, 5, 6, 1], 28, "70000", "5000", "20000", "12500.00"),
        ("SE002", [4, 5, 4, 6], 28, "55000", "4500", "15000", "17821.43"),
        ("SE003", [6, 2, 5, 6], 28, "70000", "2300", "23000", "22200.00"),
        ("SE004", [6, 6, 6, 6], 24, "70000", "0", "0", "70000.00"),
    ]

    def test_sample_sheet_reconciles(self):
        start = date(2024, 10, 7)
        workers = {}
        for badge, weeks, _wd, basic, fine, iou, _exp in self.SAMPLE:
            e = self.worker(basic, badge=badge)
            workers[badge] = e
            for w, n in enumerate(weeks):
                self.present(e, n, start=start + timedelta(7 * w))
            if D(fine):
                self.loan(e, fine, 1, start + timedelta(3), type="fine")
            if D(iou):
                self.loan(e, iou, 1, start + timedelta(10), type="loan")
        r = PayrollRun.objects.create(name="Sample", start_date=start, end_date=start + timedelta(27), default_working_days=28)
        services.generate(r)
        services.set_working_days(self.line(r, workers["SE004"]), 24)
        for badge, weeks, _wd, basic, fine, iou, expected in self.SAMPLE:
            line = self.line(r, workers[badge])
            self.assertEqual(line.week_days, weeks, badge)
            self.assertEqual((line.fines, line.iou), (D(fine), D(iou)), badge)
            self.assertEqual(line.amount_earned, D(expected), badge)
        r.refresh_from_db()
        self.assertEqual(r.total_net, D("122521.43"))


class RoleTests(Base):
    def setUp(self):
        permissions.sync_groups()
        att_perms.sync_groups()
        self.e = self.worker()
        self.present(self.e, 20)
        self.r = self.mkrun()

    def login(self, group):
        user = make_employee(company=self.company, email=f"{next_n()}@x.test").employee_user_id
        user.is_new_employee = False
        user.save()
        CompanyGroupAssignment.objects.create(user=user, company=self.company, group=Group.objects.get(name=group))
        self.client.force_login(user)
        return user

    def test_only_approver_approves_and_officer_reviews(self):
        self.login("HR Officer")
        self.client.post(reverse("wp-run-review", args=[self.r.pk]))
        self.r.refresh_from_db()
        self.assertEqual(self.r.status, "reviewed")
        self.client.post(reverse("wp-run-approve", args=[self.r.pk]))
        self.r.refresh_from_db()
        self.assertEqual(self.r.status, "reviewed")
        self.login("HR/Payroll Approver")
        self.client.post(reverse("wp-run-approve", args=[self.r.pk]))
        self.r.refresh_from_db()
        self.assertEqual((self.r.status, self.r.approved_by is not None), ("approved", True))

    def test_hr_officer_can_manage_horilla_loans_accountant_cannot(self):
        officer = set(Group.objects.get(name="HR Officer").permissions.values_list("codename", flat=True))
        self.assertTrue({"add_loanaccount", "change_loanaccount"} <= officer)
        acct = set(Group.objects.get(name="Accountant").permissions.values_list("codename", flat=True))
        self.assertFalse({c for c in acct if c.endswith("loanaccount")})

    def test_accountant_sees_only_approved_runs_and_can_download(self):
        self.login("Accountant")
        self.assertEqual(self.client.get(reverse("wp-run-detail", args=[self.r.pk])).status_code, 404)
        self.approve_run(self.r)
        self.assertEqual(self.client.get(reverse("wp-run-detail", args=[self.r.pk])).status_code, 200)
        resp = self.client.get(reverse("wp-run-download", args=[self.r.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("DRAFT", resp["Content-Disposition"])

    def test_hr_can_download_draft_any_time_and_pages_render(self):
        self.login("HR Officer")
        self.assertIn("DRAFT", self.client.get(reverse("wp-run-download", args=[self.r.pk]))["Content-Disposition"])
        for name in ("wp-runs", "wp-run-new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("wp-run-detail", args=[self.r.pk])).status_code, 200)

    def test_run_create_via_form_any_range_with_category_filter(self):
        self.login("HR Officer")
        resp = self.client.post(reverse("wp-run-new"), {
            "name": "Oct", "start_date": "2024-10-03", "end_date": "2024-10-20", "default_working_days": 28})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PayrollRun.objects.get(name="Oct").lines.count(), 1)
        bad = self.client.post(reverse("wp-run-new"), {"name": "x", "start_date": "2024-10-20", "end_date": "2024-10-03", "default_working_days": 28})
        self.assertEqual(bad.status_code, 200)
