from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

import openpyxl
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from attendance_processing import permissions as att_perms
from attendance_processing.models import AttendanceApproval, DailyAttendance
from base.models import CompanyGroupAssignment
from horilla.testkit import make_company, make_employee
from payroll.models.models import LoanAccount
from weekly_payroll import permissions, services
from weekly_payroll.export_xlsx import build_workbook
from weekly_payroll.models import (
    Fine, LoanAgreement, LoanRepayment, PayrollLine, PayrollRun, WorkerPayProfile,
)
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
        cls.approval = AttendanceApproval.objects.create(
            start_date=date(2024, 1, 1), end_date=date(2024, 12, 31)
        )

    def user(self):
        return make_employee(company=self.company, email=f"u{next_n()}@x.test").employee_user_id

    def worker(self, basic="70000", badge=None, from_=date(2024, 1, 1), approve=True):
        n = next_n()
        e = make_employee(company=self.company, email=f"w{n}@x.test", first_name=f"F{n}", last_name=f"L{n}")
        e.badge_id = badge or f"SE{n:03d}"
        e.save()
        WorkerPayProfile.objects.create(
            employee=e, basic_salary=D(basic), effective_from=from_,
            status="approved" if approve else "pending",
        )
        return e

    def present(self, e, days, approved=True, start=date(2024, 10, 1)):
        for i in range(days):
            DailyAttendance.objects.create(
                employee=e, date=start + timedelta(i), status="present",
                approval=self.approval if approved else None,
            )

    def mkrun(self, start=date(2024, 10, 1), end=date(2024, 10, 31), wd=28, name=None):
        r = PayrollRun.objects.create(
            name=name or f"Run{next_n()}", start_date=start, end_date=end, default_working_days=wd
        )
        return services.generate(r)

    def line(self, run, e):
        return PayrollLine.objects.get(run=run, employee=e)

    def loan(self, e, amount="60000", freq="monthly", instalment="20000", first=date(2024, 10, 25), approve=True):
        la = LoanAccount.objects.create(
            title="IOU", employee_id=e, loan_amount=float(amount), provided_date=date(2024, 9, 1),
            installments=3, installment_start_date=first,
        )
        a = LoanAgreement.objects.create(loan=la, frequency=freq, instalment_amount=D(instalment), first_due_date=first)
        if approve:
            services.approve_agreement(a, self.user())
        return a


class FormulaTests(TestCase):
    def test_direction_matches_sheet_row1(self):
        # 15 days attended of 28, basic 70,000, fines 5,000, IOU 20,000
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
    def test_uses_approved_attendance_only(self):
        e = self.worker()
        self.present(e, 10, approved=True)
        self.present(e, 5, approved=False, start=date(2024, 10, 20))
        l = self.line(self.mkrun(), e)
        self.assertEqual(l.total_days, 10)
        self.assertTrue(any("not yet approved" in f for f in l.flags))

    def test_workers_without_approved_profile_excluded_and_rates_differ(self):
        a, b = self.worker("70000"), self.worker("55000")
        self.worker("90000", approve=False)
        for e in (a, b):
            self.present(e, 14)
        r = self.mkrun()
        self.assertEqual(r.employee_count, 2)
        self.assertEqual(self.line(r, a).amount_earned, D("35000.00"))
        self.assertEqual(self.line(r, b).amount_earned, D("27500.00"))
        self.assertEqual(r.total_net, D("62500.00"))

    def test_profile_effective_date_respected(self):
        e = self.worker("50000", from_=date(2024, 1, 1))
        WorkerPayProfile.objects.create(employee=e, basic_salary=D("60000"), effective_from=date(2024, 10, 15), status="approved")
        self.present(e, 28)
        self.assertEqual(self.line(self.mkrun(end=date(2024, 10, 31)), e).basic_salary, D("60000.00"))
        self.assertEqual(self.line(self.mkrun(end=date(2024, 10, 10), wd=10), e).basic_salary, D("50000.00"))

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

    def test_unique_employee_per_run_and_no_duplicate_on_recalc(self):
        e = self.worker()
        r = self.mkrun()
        services.generate(r)
        self.assertEqual(PayrollLine.objects.filter(run=r, employee=e).count(), 1)

    def test_totals_reconcile(self):
        for b in ("70000", "55000", "31250.50"):
            self.present(self.worker(b), 9)
        r = self.mkrun()
        self.assertEqual(r.total_net, sum(l.amount_earned for l in r.lines.all()))


class FineTests(Base):
    def fine(self, e, amount="5000", approved=True):
        f = Fine.objects.create(employee=e, description="Late", amount=D(amount), date_issued=date(2024, 10, 5))
        if approved:
            services.approve_fine(f, self.user())
        return f

    def test_only_approved_fines_deducted(self):
        e = self.worker()
        self.present(e, 28)
        self.fine(e, "5000", approved=False)
        self.assertEqual(self.line(self.mkrun(), e).fines, D("0"))
        f2 = self.fine(e, "4500")
        self.assertEqual(self.line(self.mkrun(), e).fines, D("4500.00"))

    def test_fine_taken_in_full_by_first_approved_run_only(self):
        e = self.worker()
        self.present(e, 28)
        f = self.fine(e, "5000")
        r1 = self.mkrun(name="A")
        services.mark_reviewed(r1, self.user())
        services.approve(r1, self.user())
        f.refresh_from_db()
        self.assertEqual(f.applied_run, r1)
        r2 = self.mkrun(start=date(2024, 11, 1), end=date(2024, 11, 30), name="B")
        self.assertEqual(self.line(r2, e).fines, D("0"))

    def test_waiver_needs_reason_and_blocks_deduction(self):
        e = self.worker()
        f = self.fine(e)
        with self.assertRaises(services.PayrollError):
            services.waive_fine(f, self.user(), " ")
        services.waive_fine(f, self.user(), "Error")
        self.assertEqual(self.line(self.mkrun(), e).fines, D("0"))

    def test_missing_attendance_creates_no_fine(self):
        e = self.worker()
        self.mkrun()
        self.assertFalse(Fine.objects.filter(employee=e).exists())


class LoanTests(Base):
    def test_monthly_instalment_not_deducted_every_week(self):
        e = self.worker()
        a = self.loan(e)  # 20,000 monthly from 25 Oct
        for s, en in [(1, 7), (8, 14), (15, 21)]:
            r = self.mkrun(start=date(2024, 10, s), end=date(2024, 10, en), wd=6)
            self.assertEqual(self.line(r, e).iou, D("0"))
        r = self.mkrun(start=date(2024, 10, 22), end=date(2024, 10, 28), wd=6)
        self.assertEqual(self.line(r, e).iou, D("20000.00"))

    def test_deduction_happens_once_even_across_overlapping_runs(self):
        e = self.worker()
        self.present(e, 28)
        a = self.loan(e)
        r1 = self.mkrun(name="Oct")
        self.assertEqual(self.line(r1, e).iou, D("20000.00"))
        self.assertEqual(a.repayments.count(), 0)          # draft writes no ledger
        services.generate(r1)
        self.assertEqual(self.line(r1, e).iou, D("20000.00"))  # recalculation does not double
        services.mark_reviewed(r1, self.user())
        services.approve(r1, self.user())
        self.assertEqual(a.outstanding(), D("40000.00"))
        r2 = self.mkrun(start=date(2024, 10, 20), end=date(2024, 10, 31), name="Overlap")
        self.assertEqual(self.line(r2, e).iou, D("0"))

    def test_two_drafts_race_second_approval_refused(self):
        e = self.worker()
        self.present(e, 28)
        self.loan(e)
        r1, r2 = self.mkrun(name="One"), self.mkrun(start=date(2024, 10, 2), end=date(2024, 10, 30), name="Two")
        for r in (r1, r2):
            services.mark_reviewed(r, self.user())
        services.approve(r1, self.user())
        with self.assertRaises(services.PayrollError):
            services.approve(r2, self.user())
        self.assertEqual(LoanRepayment.objects.count(), 1)

    def test_weekly_agreement_collects_each_week(self):
        e = self.worker()
        a = self.loan(e, "40000", "weekly", "10000", first=date(2024, 10, 4))
        r = self.mkrun()
        # due 4, 11, 18, 25 Oct
        self.assertEqual(self.line(r, e).iou, D("40000.00"))

    def test_never_exceeds_outstanding_and_closes(self):
        e = self.worker()
        a = self.loan(e, "30000", "monthly", "20000", first=date(2024, 10, 25))
        r = self.mkrun(start=date(2024, 10, 1), end=date(2024, 12, 31), wd=60)
        self.assertEqual(self.line(r, e).iou, D("30000.00"))  # 20,000 + capped 10,000
        services.mark_reviewed(r, self.user())
        services.approve(r, self.user())
        a.refresh_from_db()
        self.assertEqual((a.outstanding(), a.status), (D("0.00"), "settled"))
        a.loan.refresh_from_db()
        self.assertTrue(a.loan.settled)
        r2 = self.mkrun(start=date(2025, 1, 1), end=date(2025, 1, 31), name="Jan")
        self.assertEqual(self.line(r2, e).iou, D("0"))

    def test_pending_agreement_not_deducted_and_early_settlement(self):
        e = self.worker()
        a = self.loan(e, approve=False)
        self.assertEqual(self.line(self.mkrun(), e).iou, D("0"))
        services.approve_agreement(a, self.user())
        with self.assertRaises(services.PayrollError):
            services.record_adjustment(a, "settlement", "70000", "x", self.user())
        services.record_adjustment(a, "settlement", "60000", "Paid in cash", self.user())
        a.refresh_from_db()
        self.assertEqual(a.status, "settled")
        self.assertEqual(self.line(self.mkrun(name="again"), e).iou, D("0"))

    def test_authorised_adjustment_reduces_balance(self):
        e = self.worker()
        a = self.loan(e)
        services.record_adjustment(a, "adjustment", "20000", "Waived by MD", self.user())
        self.assertEqual(a.outstanding(), D("40000.00"))
        with self.assertRaises(services.PayrollError):
            services.record_adjustment(a, "adjustment", "100", "", self.user())


class LifecycleTests(Base):
    def setUp(self):
        self.e = self.worker()
        self.present(self.e, 20)
        self.r = self.mkrun()

    def test_must_be_reviewed_before_approval(self):
        with self.assertRaises(services.PayrollError):
            services.approve(self.r, self.user())

    def test_approved_run_is_frozen(self):
        services.mark_reviewed(self.r, self.user())
        services.approve(self.r, self.user())
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
        r = PayrollRun.objects.create(name="Z", start_date=date(2024, 10, 1), end_date=date(2024, 10, 31), default_working_days=1)
        services.generate(r)
        PayrollLine.objects.filter(run=r).update(working_days=0)
        with self.assertRaises(services.PayrollError):
            services.mark_reviewed(r, self.user())

    def test_overlapping_approved_run_blocks(self):
        services.mark_reviewed(self.r, self.user())
        services.approve(self.r, self.user())
        r2 = self.mkrun(start=date(2024, 10, 20), end=date(2024, 11, 5), name="Overlap")
        services.mark_reviewed(r2, self.user())
        with self.assertRaises(services.PayrollError):
            services.approve(r2, self.user())


class ExcelTests(Base):
    def test_workbook_matches_template_layout_and_formulas(self):
        e = self.worker("70000", badge="SE001")
        self.present(e, 17)
        r = self.mkrun(start=date(2024, 10, 1), end=date(2024, 10, 17), wd=17)
        wb = openpyxl.load_workbook(BytesIO(build_workbook(r)))
        ws = wb["PAYROLL"]
        self.assertEqual(
            [c.value for c in ws[1]],
            ["EMP ID", "FIRST NAME", "LAST NAME", "DEPARTMENT", "WEEK 1", "WEEK 2", "WEEK 3",
             "TOTAL DAY", "WORKING DAYS", "BASIC SALARY", "FINES", "IOU", "AMOUNT EARNED"],
        )
        self.assertEqual([c.value for c in ws[2]][4:7], [7, 7, 3])
        self.assertEqual(ws["I2"].value, 17)
        self.assertIn("Table1", ws.tables)
        self.assertIn("Table1[[#This Row],[WEEK 3]]", ws["H2"].value)
        self.assertTrue(ws["M2"].value.startswith("=ROUND("))
        self.assertIn("NOT APPROVED", wb["RUN INFO"]["B3"].value)


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
        self.assertEqual(self.r.status, "approved")
        self.assertIsNotNone(self.r.approved_by)

    def test_management_approves_pay_profiles_officer_cannot(self):
        p = WorkerPayProfile.objects.create(employee=self.e, basic_salary=D("1"), effective_from=date(2025, 1, 1))
        self.login("HR Officer")
        self.client.post(reverse("wp-profile-decide", args=[p.pk, "approve"]))
        p.refresh_from_db()
        self.assertEqual(p.status, "pending")
        self.login("Management")
        self.client.post(reverse("wp-profile-decide", args=[p.pk, "approve"]))
        p.refresh_from_db()
        self.assertEqual(p.status, "approved")

    def test_management_approves_fines_and_loans(self):
        f = Fine.objects.create(employee=self.e, description="x", amount=D("10"))
        a = self.loan(self.e, approve=False)
        self.login("HR Officer")
        self.client.post(reverse("wp-fine-approve", args=[f.pk]))
        self.client.post(reverse("wp-loan-approve", args=[a.pk]))
        f.refresh_from_db(); a.refresh_from_db()
        self.assertEqual((f.status, a.status), ("pending", "pending"))
        self.login("Management")
        self.client.post(reverse("wp-fine-approve", args=[f.pk]))
        self.client.post(reverse("wp-loan-approve", args=[a.pk]))
        f.refresh_from_db(); a.refresh_from_db()
        self.assertEqual((f.status, a.status), ("approved", "approved"))

    def test_accountant_sees_only_approved_runs_and_can_download(self):
        self.login("Accountant")
        self.assertEqual(self.client.get(reverse("wp-run-detail", args=[self.r.pk])).status_code, 404)
        services.mark_reviewed(self.r, self.user())
        services.approve(self.r, self.user())
        self.assertEqual(self.client.get(reverse("wp-run-detail", args=[self.r.pk])).status_code, 200)
        resp = self.client.get(reverse("wp-run-download", args=[self.r.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("DRAFT", resp["Content-Disposition"])
        self.assertEqual(self.client.post(reverse("wp-run-recalculate", args=[self.r.pk])).status_code, 302)

    def test_hr_can_download_draft_any_time_and_pages_render(self):
        self.login("HR Officer")
        resp = self.client.get(reverse("wp-run-download", args=[self.r.pk]))
        self.assertIn("DRAFT", resp["Content-Disposition"])
        for name in ("wp-runs", "wp-run-new", "wp-profiles", "wp-fines", "wp-loans", "wp-fine-new", "wp-loan-new", "wp-profile-new"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("wp-run-detail", args=[self.r.pk])).status_code, 200)

    def test_run_create_via_form_any_range(self):
        self.login("HR Officer")
        resp = self.client.post(reverse("wp-run-new"), {
            "name": "Oct", "start_date": "2024-10-03", "end_date": "2024-10-20", "default_working_days": 28,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(PayrollRun.objects.get(name="Oct").lines.count(), 1)


class SampleSheetTests(Base):
    """Reproduces the four workers of Demiller's PAYROLL_TEMPLATE_1.xlsx with the agreed formula."""

    SAMPLE = [
        # badge, weekly present days, working days, basic, fines, IOU, expected
        ("SE001", [3, 5, 6, 1], 28, "70000", "5000", "20000", "12500.00"),
        ("SE002", [4, 5, 4, 6], 28, "55000", "4500", "15000", "17821.43"),
        ("SE003", [6, 2, 5, 6], 28, "70000", "2300", "23000", "22200.00"),
        ("SE004", [6, 6, 6, 6], 24, "70000", "0", "0", "70000.00"),
    ]

    def test_sample_sheet_reconciles(self):
        start = date(2024, 10, 7)  # a Monday; four 7-day blocks = 28 days
        user = self.user()
        workers = {}
        for badge, weeks, wd, basic, fine, iou, _exp in self.SAMPLE:
            e = self.worker(basic, badge=badge)
            workers[badge] = e
            for w, n in enumerate(weeks):
                for i in range(n):
                    DailyAttendance.objects.create(
                        employee=e, date=start + timedelta(7 * w + i), status="present", approval=self.approval
                    )
            if D(fine):
                f = Fine.objects.create(employee=e, description="Fine", amount=D(fine), date_issued=start)
                services.approve_fine(f, user)
            if D(iou):
                self.loan(e, iou, "monthly", iou, first=start + timedelta(10))
        r = PayrollRun.objects.create(
            name="Sample", start_date=start, end_date=start + timedelta(27), default_working_days=28
        )
        services.generate(r)
        services.set_working_days(self.line(r, workers["SE004"]), 24)
        for badge, weeks, wd, basic, fine, iou, expected in self.SAMPLE:
            line = self.line(r, workers[badge])
            self.assertEqual(line.week_days, weeks, badge)
            self.assertEqual(line.total_days, sum(weeks), badge)
            self.assertEqual((line.fines, line.iou), (D(fine), D(iou)), badge)
            self.assertEqual(line.amount_earned, D(expected), badge)
        r.refresh_from_db()
        self.assertEqual(r.total_net, D("122521.43"))
