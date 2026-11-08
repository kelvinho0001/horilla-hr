from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attendance_processing import calculators, permissions, services
from attendance_processing.models import (
    AttendanceApproval, AttendanceCorrection, AttendanceRule, DailyAttendance,
)
from base.models import CompanyGroupAssignment, Department, Holidays
from biometric_integration.models import (
    ExternalEmployeeMapping, ImportBatch, ProviderConfig, RawTransaction,
)
from horilla.testkit import make_company, make_employee
from horilla.testkit.factories import make_leave_request, make_leave_type

_N = [0]


def _bump():
    _N[0] += 1


LAGOS = ZoneInfo("Africa/Lagos")
# Monday 2024-09-02 .. Sunday 2024-09-08 (all in the past)
MON = date(2024, 9, 2)


def at(day, hh=16, mm=0):
    return datetime.combine(day, time(hh, mm), tzinfo=LAGOS)


class Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = make_company()
        cls.provider = ProviderConfig.objects.create(name="M", provider_type="manual_file")
        cls.batch = ImportBatch.objects.create(provider=cls.provider)

    def emp(self, joined=date(2024, 1, 1), department=None, mapped=True):
        _bump()
        e = make_employee(
            company=self.company, email=f"e{_N[0]}@x.test", first_name=f"E{_N[0]}",
            date_joining=joined, department=department,
        )
        if mapped:
            ExternalEmployeeMapping.objects.create(
                provider=self.provider, employee=e, external_employee_id=f"{_N[0]}"
            )
        return e

    def punch(self, e, ts, uniq=None):
        _bump()
        return RawTransaction.objects.create(
            provider=self.provider, batch=self.batch, dedup_key=f"k{_N[0]}",
            external_employee_id="x", employee=e, attendance_timestamp=ts,
            source_timezone="Africa/Lagos",
        )

    def status(self, e, d):
        return DailyAttendance.objects.get(employee=e, date=d).status

    def run_week(self, e, start=MON, end=MON + timedelta(days=6)):
        return services.process_range(start, end, [e])


class CalculatorTests(TestCase):
    def test_week_buckets_any_length(self):
        b = calculators.week_buckets(date(2024, 9, 1), date(2024, 9, 17))
        self.assertEqual([(a.day, z.day) for a, z in b], [(1, 7), (8, 14), (15, 17)])
        self.assertEqual(calculators.week_buckets(date(2024, 9, 3), date(2024, 9, 3)), [(date(2024, 9, 3),) * 2])

    def test_multiple_punches_is_one_present(self):
        s, f = calculators.classify([time(15, 55), time(16, 5), time(16, 6)],
                                    window=None, working_weekday=True, is_holiday=False, on_leave=False)
        self.assertEqual(s, "present")
        self.assertIn("multiple_punches", f)

    def test_outside_window_needs_review_not_discarded(self):
        s, f = calculators.classify([time(7, 0)], window=(time(15, 0), time(17, 0)),
                                    working_weekday=True, is_holiday=False, on_leave=False)
        self.assertEqual((s, f), ("review", ["out_of_window"]))


class ProcessingTests(Base):
    def test_valid_punch_creates_present(self):
        e = self.emp()
        self.punch(e, at(MON))
        self.run_week(e)
        self.assertEqual(self.status(e, MON), "present")

    def test_two_punches_one_day_is_one_row(self):
        e = self.emp()
        self.punch(e, at(MON, 15, 58))
        self.punch(e, at(MON, 16, 3))
        self.run_week(e)
        self.assertEqual(DailyAttendance.objects.filter(employee=e, date=MON).count(), 1)
        self.assertEqual(DailyAttendance.objects.get(employee=e, date=MON).raw_count, 2)

    def test_missing_rest_day_and_idempotent(self):
        e = self.emp()
        self.punch(e, at(MON))
        first = self.run_week(e)
        self.run_week(e)
        self.assertEqual(self.status(e, MON + timedelta(days=1)), "missing")
        self.assertEqual(self.status(e, MON + timedelta(days=6)), "rest")  # Sunday, fallback
        self.assertEqual(DailyAttendance.objects.filter(employee=e).count(), 7)
        self.assertEqual(first["created"], 7)

    def test_approved_leave_not_absent(self):
        e = self.emp()
        make_leave_request(employee=e, leave_type=make_leave_type(), start_date=MON + timedelta(1), end_date=MON + timedelta(1))
        self.run_week(e)
        self.assertEqual(self.status(e, MON + timedelta(1)), "leave")

    def test_pending_leave_does_not_count(self):
        e = self.emp()
        make_leave_request(employee=e, leave_type=make_leave_type(), start_date=MON + timedelta(1),
                           end_date=MON + timedelta(1), status="requested")
        self.run_week(e)
        self.assertEqual(self.status(e, MON + timedelta(1)), "missing")

    def test_public_holiday(self):
        e = self.emp()
        Holidays.objects.create(name="Fest", start_date=MON + timedelta(2), end_date=MON + timedelta(2))
        self.run_week(e)
        self.assertEqual(self.status(e, MON + timedelta(2)), "holiday")

    def test_joiners_and_leavers(self):
        e = self.emp(joined=MON + timedelta(3))
        self.run_week(e)
        days = set(DailyAttendance.objects.filter(employee=e).values_list("date", flat=True))
        self.assertNotIn(MON, days)
        self.assertIn(MON + timedelta(3), days)

    def test_future_dates_never_marked(self):
        e = self.emp()
        stats = services.process_range(services.today() + timedelta(1), services.today() + timedelta(5), [e])
        self.assertEqual(stats["days"], 0)
        self.assertFalse(DailyAttendance.objects.filter(employee=e).exists())

    def test_day_uses_local_date_not_utc(self):
        e = self.emp()
        # 23:30 UTC on Monday is 00:30 Tuesday in Lagos.
        self.punch(e, datetime(2024, 9, 2, 23, 30, tzinfo=ZoneInfo("UTC")))
        self.run_week(e)
        self.assertEqual(self.status(e, MON + timedelta(1)), "present")
        self.assertEqual(self.status(e, MON), "missing")

    def test_unmapped_employees_not_processed(self):
        e = self.emp(mapped=False)
        self.assertNotIn(e, list(services.employees_for_attendance()))

    def test_rule_specificity_and_window(self):
        d = Department.objects.create(department="Packing")
        a, b = self.emp(department=d), self.emp()
        AttendanceRule.objects.create(name="all", scope="global", window_start=time(15), window_end=time(17), working_weekdays="0,1,2,3,4,5,6")
        AttendanceRule.objects.create(name="pack", scope="department", department=d, window_start=time(9), window_end=time(11), working_weekdays="0,1,2,3,4,5")
        for e in (a, b):
            self.punch(e, at(MON, 10))
        services.process_range(MON, MON + timedelta(6), [a, b])
        self.assertEqual(self.status(a, MON), "present")   # dept window 9-11
        self.assertEqual(self.status(b, MON), "review")    # global window 15-17
        self.assertEqual(self.status(a, MON + timedelta(6)), "rest")
        self.assertEqual(self.status(b, MON + timedelta(6)), "missing")  # global works Sundays

    def test_concurrent_creation_race_does_not_duplicate(self):
        e = self.emp()
        DailyAttendance.objects.create(employee=e, date=MON, status="missing")
        self.run_week(e)
        self.assertEqual(DailyAttendance.objects.filter(employee=e, date=MON).count(), 1)


class CorrectionApprovalTests(Base):
    def setUp(self):
        self.user = make_employee(company=self.company, email="hr@x.test").employee_user_id
        self.e = self.emp()
        self.run_week(self.e)

    def test_correction_requires_reason_and_is_audited(self):
        d = MON + timedelta(1)
        with self.assertRaises(services.AttendanceError):
            services.correct(self.e, d, "present", "  ", self.user)
        services.correct(self.e, d, "present", "Phone punch lost", self.user)
        c = AttendanceCorrection.objects.get()
        self.assertEqual((c.old_status, c.new_status, c.reason), ("missing", "present", "Phone punch lost"))

    def test_correction_survives_reprocessing(self):
        d = MON + timedelta(1)
        services.correct(self.e, d, "present", "ok", self.user)
        self.run_week(self.e)
        self.assertEqual(self.status(self.e, d), "present")

    def test_cannot_mark_future(self):
        with self.assertRaises(services.AttendanceError):
            services.correct(self.e, services.today() + timedelta(1), "present", "x", self.user)

    def resolve_all(self):
        services.bulk_mark_missing_absent([self.e], MON, MON + timedelta(6), "No show", self.user)

    def test_approval_blocked_while_unresolved(self):
        with self.assertRaises(services.ApprovalBlocked) as cm:
            services.approve(MON, MON + timedelta(6), self.user)
        self.assertEqual(len(cm.exception.rows), 6)

    def test_approve_locks_and_reopen_unlocks_with_audit(self):
        self.resolve_all()
        a = services.approve(MON, MON + timedelta(6), self.user)
        self.assertEqual(a.day_count, 7)
        d = MON + timedelta(1)
        with self.assertRaises(services.AttendanceError):
            services.correct(self.e, d, "present", "late fix", self.user)
        # re-processing leaves locked rows alone
        self.punch(self.e, at(d))
        self.run_week(self.e)
        self.assertEqual(self.status(self.e, d), "absent")
        with self.assertRaises(services.AttendanceError):
            services.reopen(a, self.user, "")
        services.reopen(a, self.user, "Wrong week")
        a.refresh_from_db()
        self.assertEqual((a.status, a.reopen_reason, a.reopened_by), ("reopened", "Wrong week", self.user))
        services.correct(self.e, d, "present", "late fix", self.user)
        with self.assertRaises(services.AttendanceError):  # cannot reopen twice
            services.reopen(a, self.user, "again")

    def test_approve_empty_range_rejected(self):
        with self.assertRaises(services.AttendanceError):
            services.approve(date(2020, 1, 1), date(2020, 1, 7), self.user)

    def test_department_scoped_approval(self):
        d = Department.objects.create(department="Sel")
        other = self.emp(department=d)
        self.run_week(other)
        services.bulk_mark_missing_absent([other], MON, MON + timedelta(6), "x", self.user)
        services.approve(MON, MON + timedelta(6), self.user, department=d)
        self.assertTrue(DailyAttendance.objects.filter(employee=other, approval__isnull=False).exists())
        self.assertFalse(DailyAttendance.objects.filter(employee=self.e, approval__isnull=False).exists())


class RoleAndViewTests(Base):
    def setUp(self):
        permissions.sync_groups()
        self.e = self.emp()
        self.run_week(self.e)

    def login(self, group):
        emp = make_employee(company=self.company, email=f"{group.replace('/', '')}@x.test")
        user = emp.employee_user_id
        user.is_new_employee = False  # else Horilla forces a password change
        user.save()
        # Horilla scopes group permissions per company (COMPANY_SCOPED_PERMISSIONS).
        CompanyGroupAssignment.objects.create(
            user=user, company=self.company, group=Group.objects.get(name=group)
        )
        self.client.force_login(user)
        return user

    def post_approve(self):
        return self.client.post(reverse("att-proc-approve"), {
            "start": str(MON), "end": str(MON + timedelta(6)),
        })

    def test_groups_seeded_with_split_duties(self):
        officer = set(Group.objects.get(name="HR Officer").permissions.values_list("codename", flat=True))
        approver = set(Group.objects.get(name="HR/Payroll Approver").permissions.values_list("codename", flat=True))
        self.assertIn("correct_attendance", officer)
        self.assertNotIn("approve_attendance", officer)
        self.assertNotIn("reopen_attendance", officer)
        self.assertTrue({"approve_attendance", "reopen_attendance"} <= approver)
        accountant = set(Group.objects.get(name="Accountant").permissions.values_list("codename", flat=True))
        self.assertNotIn("correct_attendance", accountant)

    def test_officer_cannot_approve_approver_can(self):
        services.bulk_mark_missing_absent([self.e], MON, MON + timedelta(6), "x", make_employee(company=self.company, email="z@x.test").employee_user_id)
        self.login("HR Officer")
        self.post_approve()
        self.assertEqual(AttendanceApproval.objects.count(), 0)
        self.login("HR/Payroll Approver")
        self.post_approve()
        self.assertEqual(AttendanceApproval.objects.count(), 1)

    def test_officer_cannot_reopen(self):
        u = self.login("HR/Payroll Approver")
        services.bulk_mark_missing_absent([self.e], MON, MON + timedelta(6), "x", u)
        a = services.approve(MON, MON + timedelta(6), u)
        self.login("HR Officer")
        self.client.post(reverse("att-proc-reopen", args=[a.pk]), {"reason": "x"})
        a.refresh_from_db()
        self.assertEqual(a.status, "approved")

    def test_review_page_flexible_ranges(self):
        self.login("HR Officer")
        for qs in (
            {"start": str(MON), "end": str(MON + timedelta(2))},   # day to day
            {"from_month": "2024-08", "to_month": "2024-09"},      # month to month
            {},                                                    # default last 7 days
        ):
            r = self.client.get(reverse("att-proc-review"), qs)
            self.assertEqual(r.status_code, 200, qs)
        r = self.client.get(reverse("att-proc-review"), {"start": "2024-01-01", "end": "2024-12-31"})
        self.assertContains(r, "at most 93 days")

    def test_review_shows_weekly_columns_and_detail_pages(self):
        self.login("HR Officer")
        r = self.client.get(reverse("att-proc-review"), {"start": str(MON), "end": str(MON + timedelta(9))})
        self.assertContains(r, "Wk1")
        self.assertContains(r, "Wk2")
        self.assertEqual(self.client.get(reverse("att-proc-day", args=[self.e.pk, str(MON)])).status_code, 200)
        self.assertEqual(self.client.get(reverse("att-proc-approvals")).status_code, 200)
        self.assertEqual(self.client.get(reverse("att-proc-rules")).status_code, 200)

    def test_correct_via_view_requires_reason(self):
        self.login("HR Officer")
        d = MON + timedelta(1)
        self.client.post(reverse("att-proc-correct"), {"employee": self.e.pk, "date": str(d), "status": "present", "reason": ""})
        self.assertEqual(self.status(self.e, d), "missing")
        self.client.post(reverse("att-proc-correct"), {"employee": self.e.pk, "date": str(d), "status": "present", "reason": "seen"})
        self.assertEqual(self.status(self.e, d), "present")
